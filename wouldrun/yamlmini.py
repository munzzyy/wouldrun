"""A small, defensive YAML reader for the subset of YAML that GitHub Actions
workflow files actually use.

This exists instead of a PyYAML dependency for two reasons. First, it keeps
wouldrun at zero runtime dependencies. Second, and more important: PyYAML's
default (and "safe") loaders follow the YAML 1.1 core schema, which resolves
an unquoted `on`, `off`, `yes`, or `no` scalar to a boolean. That means a
workflow's `on:` key silently becomes the Python boolean `True` instead of
the string `"on"` the moment you round-trip it through `yaml.safe_load`, and
every rule downstream that does `doc["on"]` breaks quietly. GitHub's own
parser does not have this bug; PyYAML does. Rather than lean on a library
with a footgun in exactly the field wouldrun cares about most, this module
resolves booleans the way YAML 1.2's core schema does: only `true`/`false`
(in any casing) are booleans. `on`, `off`, `yes`, and `no` stay plain
strings, and mapping keys are never coerced to anything but a string, full
stop, in either schema. See workflow.py's `_extract_on` for a second,
belt-and-suspenders guard against a stray boolean key.

Supported: block and flow mappings, block and flow sequences, single- and
double-quoted scalars, plain scalars, `|`/`>` block scalars (consumed and
kept as opaque text, since wouldrun never needs step/run bodies), comments,
anchors and aliases (`&name` / `*name`), and a single leading `---` /
trailing `...` document marker. An alias returns the same object its anchor
built, never a copy, but anything that later walks the document still sees
every alias copied out. So each alias is charged the expanded size of what it
points at, and a file whose aliases add up past MAX_ALIAS_EXPANSION is a
YamlError, like a file over MAX_BYTES. Merge keys (`<<: *name`) are rejected
with a YamlError because GitHub rejects them too. Not supported: multi-document
streams and tag annotations (`!!str` and friends are kept as part of the scalar
text). Neither appears in the trigger and job metadata this tool reads.
"""

from __future__ import annotations

import re

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 20000
MAX_DEPTH = 200  # nesting levels; real workflow files never get close
MAX_ALIAS_EXPANSION = MAX_BYTES  # characters plus one per node, summed over every alias

_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?\d*$")
_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+[eE][-+]?\d+|\d+\.\d*[eE][-+]?\d+)$")
_ANCHOR_RE = re.compile(r"^&([^\s,\[\]{}]+)(?:\s+|$)")
_ALIAS_RE = re.compile(r"^\*([^\s,\[\]{}]+)$")
_MERGE_KEY_ERROR = "YAML merge keys (`<<`) are not supported in GitHub Actions workflows"


class YamlError(ValueError):
    """Raised for malformed or oversized input; never a bare traceback."""


def load(text: str):
    """Parse `text` and return the top-level value (usually a dict)."""
    if not isinstance(text, str):
        raise YamlError(f"expected str, got {type(text).__name__}")
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise YamlError(f"input exceeds {MAX_BYTES} byte cap")
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise YamlError(f"input exceeds {MAX_LINES} line cap")
    parser = _Parser(lines)
    value, _ = parser.parse_block(0, 0)
    return value


class _Parser:
    def __init__(self, lines):
        self.lines = lines
        self.n = len(lines)
        self._depth = 0
        self.anchors = {}
        self._sizes = {}  # id -> (object, expanded size); holding the object keeps the id unique
        self._expanded = 0
        self._trim_document_markers()

    def _trim_document_markers(self):
        # A lone "---" opens a document; a lone "..." ends it. wouldrun only
        # ever reads the first document in a workflow file.
        end = self.n
        start = 0
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if stripped == "":
                continue
            if stripped == "---":
                start = i + 1
            break
        for i in range(start, self.n):
            if self.lines[i].strip() == "...":
                end = i
                break
        self.lines = self.lines[start:end]
        self.n = len(self.lines)

    # -- low-level line helpers -------------------------------------------------

    def _indent_of(self, line):
        if "\t" in line[: len(line) - len(line.lstrip(" \t"))]:
            raise YamlError("tabs are not allowed for indentation")
        return len(line) - len(line.lstrip(" "))

    def _is_blank_or_comment(self, line):
        s = line.strip()
        return s == "" or s.startswith("#")

    def _next_real(self, idx):
        """Index of the next non-blank, non-comment line at/after idx, or None."""
        i = idx
        while i < self.n and self._is_blank_or_comment(self.lines[i]):
            i += 1
        return i if i < self.n else None

    def _content(self, idx):
        return _strip_comment(self.lines[idx]).strip()

    # -- block parsing ------------------------------------------------------

    def parse_block(self, idx, min_indent):
        # Every recursive descent -- from _parse_mapping, _parse_sequence,
        # and _parse_mapping_continuation alike -- funnels back through this
        # one method, so it is the single place to cap how deep the mutual
        # recursion is allowed to go. Sibling keys/items at the same level
        # call back in sequentially, not simultaneously, so the try/finally
        # unwind keeps their depth from stacking; only genuine nesting does.
        self._depth += 1
        try:
            if self._depth > MAX_DEPTH:
                raise YamlError(f"nesting exceeds {MAX_DEPTH} levels")
            i = self._next_real(idx)
            if i is None:
                return None, idx
            indent = self._indent_of(self.lines[i])
            if indent < min_indent:
                return None, idx
            content = self._content(i)
            if content == "-" or content.startswith("- "):
                return self._parse_sequence(i, indent)
            return self._parse_mapping(i, indent)
        finally:
            self._depth -= 1

    def _parse_mapping(self, idx, indent):
        result = {}
        i = idx
        while True:
            real = self._next_real(i)
            if real is None:
                break
            if self._indent_of(self.lines[real]) != indent:
                break
            content = self._content(real)
            split = _split_key_value(content)
            if split is None:
                raise YamlError(f"malformed mapping line: {self.lines[real]!r}")
            key = self._key(split[0], content)
            value, next_i = self._value_after_key(split[1], real, indent)
            result[key] = value
            i = next_i
        return result, i

    def _key(self, key, content):
        """Resolve a block mapping key's anchor or alias; a quoted key is literal."""
        if content[0] in ("'", '"'):
            return key
        if key == "<<":
            raise YamlError(_MERGE_KEY_ERROR)
        anchor, key = _split_anchor(key)
        if _ALIAS_RE.match(key):
            token, key = key, self._alias(key)
            if not isinstance(key, str):
                raise YamlError(f"alias {token} is used as a mapping key but is not a string")
        if anchor is not None:
            self.anchors[anchor] = key
        return key

    def _value_after_key(self, rest, real, indent):
        anchor, rest = _split_anchor(rest)
        if rest == "":
            value, next_i = self.parse_block(real + 1, indent + 1)
            if value is None:
                seq = self._indentless_sequence(next_i, indent)
                if seq is not None:
                    value, next_i = seq
        elif _BLOCK_SCALAR_RE.match(rest):
            value, next_i = self._consume_block_scalar(real, indent)
        else:
            value, next_i = self._flow_value(rest, real)
        if anchor is not None:
            self.anchors[anchor] = value
        return value, next_i

    def _alias(self, token):
        return self.resolve(token[1:])

    def resolve(self, name):
        if name not in self.anchors:
            raise YamlError(f"alias *{name} refers to an anchor that is not defined above it")
        value = self.anchors[name]
        self._expanded += self._expanded_size(value)
        if self._expanded > MAX_ALIAS_EXPANSION:
            raise YamlError(f"aliases expand past {MAX_ALIAS_EXPANSION} characters at *{name}")
        return value

    def _expanded_size(self, value):
        """Characters plus one per node in `value` with every alias copied out,
        memoized per object so shared values are walked once."""
        sizes = self._sizes
        stack = [value]
        while stack:
            obj = stack[-1]
            if id(obj) in sizes:
                stack.pop()
                continue
            if isinstance(obj, dict):
                children = [*obj.keys(), *obj.values()]
            elif isinstance(obj, list):
                children = obj
            else:
                sizes[id(obj)] = (obj, _scalar_size(obj))
                stack.pop()
                continue
            todo = [c for c in children if id(c) not in sizes]
            if todo:
                stack.extend(todo)
                continue
            sizes[id(obj)] = (obj, 1 + sum(sizes[id(c)][1] for c in children))
            stack.pop()
        return sizes[id(value)][1]

    def _parse_sequence(self, idx, indent):
        result = []
        i = idx
        while True:
            real = self._next_real(i)
            if real is None:
                break
            line = self.lines[real]
            if self._indent_of(line) != indent:
                break
            content = self._content(real)
            if content == "-":
                rest = ""
            elif content.startswith("- "):
                rest = content[2:]
            else:
                break
            # "- key: value" starts an inline mapping; the item's effective
            # indent is wherever `rest` began on this physical line.
            item_indent = indent + (len(content) - len(rest))
            # On `- &a key: v` the anchor names the key, as in PyYAML.
            anchor, rest = _split_anchor(rest)
            if rest == "":
                value, next_i = self.parse_block(real + 1, indent + 1)
                if anchor is not None:
                    self.anchors[anchor] = value
                result.append(value)
                i = next_i
                continue
            split = _split_key_value(rest)
            if split is not None:
                key = self._key(split[0], rest)
                if anchor is not None:
                    self.anchors[anchor] = key
                value, next_i = self._value_after_key(split[1], real, item_indent)
                mapping = {key: value}
                more, next_i = self._parse_mapping_continuation(next_i, item_indent, mapping)
                result.append(more)
                i = next_i
            else:
                if _BLOCK_SCALAR_RE.match(rest):
                    value, next_i = self._consume_block_scalar_at(real, item_indent, rest)
                else:
                    value, next_i = self._flow_value(rest, real)
                if anchor is not None:
                    self.anchors[anchor] = value
                result.append(value)
                i = next_i
        return result, i

    def _parse_mapping_continuation(self, idx, indent, mapping):
        """Continue a "- key: value" mapping with sibling keys at `indent`."""
        i = idx
        while True:
            real = self._next_real(i)
            if real is None:
                break
            if self._indent_of(self.lines[real]) != indent:
                break
            content = self._content(real)
            if content.startswith("- "):
                break
            split = _split_key_value(content)
            if split is None:
                break
            key = self._key(split[0], content)
            value, next_i = self._value_after_key(split[1], real, indent)
            mapping[key] = value
            i = next_i
        return mapping, i

    def _consume_block_scalar(self, key_line_idx, key_indent):
        return self._consume_block_scalar_at(key_line_idx, key_indent, None)

    def _consume_block_scalar_at(self, key_line_idx, key_indent, _marker):
        j = key_line_idx + 1
        content_indent = None
        first = j
        while first < self.n and self.lines[first].strip() == "":
            first += 1
        if first < self.n:
            candidate = self._indent_of(self.lines[first])
            if candidate > key_indent:
                content_indent = candidate
        if content_indent is None:
            return "", key_line_idx + 1
        out = []
        i = j
        last_content = j
        while i < self.n:
            line = self.lines[i]
            if line.strip() == "":
                out.append("")
                i += 1
                continue
            if self._indent_of(line) < content_indent:
                break
            out.append(line[content_indent:])
            last_content = i
            i += 1
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out), last_content + 1

    def _indentless_sequence(self, idx, indent):
        """A block sequence whose `-` items sit at the SAME indent as their
        parent key -- e.g.

            branches:
            - main
            - dev

        This is valid YAML and GitHub's parser accepts it, but parse_block
        requires a child to be indented deeper than its key, so it skips the
        items and the mapping loop then chokes on the bare `- main` line.
        Called only when parse_block found nothing, this picks the flush
        sequence up. Returns (value, next_i), or None if the next real line
        is not such a sequence."""
        seq_i = self._next_real(idx)
        if seq_i is None or self._indent_of(self.lines[seq_i]) != indent:
            return None
        content = self._content(seq_i)
        if content == "-" or content.startswith("- "):
            return self._parse_sequence(seq_i, indent)
        return None

    def _flow_value(self, rest, real):
        """Resolve a scalar or flow value that starts with `rest` on line
        `real`. A flow collection (`[...]` / `{...}`) may span several
        physical lines, so gather following lines until its brackets balance
        before parsing. Returns (value, next_line_index)."""
        if rest and rest[0] in "[{":
            text, end = self._gather_flow(rest, real)
            return _FlowParser(text, self).parse(), end + 1
        if rest.startswith("*"):
            if not _ALIAS_RE.match(rest):
                raise YamlError(f"malformed alias: {rest!r}")
            return self._alias(rest), real + 1
        return _coerce_scalar(rest), real + 1

    def _gather_flow(self, rest, line_idx):
        """`rest` opens a flow collection. If its brackets don't close on this
        physical line, join the following lines until they do (multi-line flow
        is standard YAML). Returns (combined_text, last_line_idx)."""
        depth = _flow_depth(rest)
        i = line_idx
        parts = [rest]
        while depth > 0 and i + 1 < self.n:
            i += 1
            segment = self._content(i)
            parts.append(segment)
            depth += _flow_depth(segment)
        return " ".join(parts), i


def _flow_depth(text):
    """Net `[`/`{` minus `]`/`}` nesting in `text`, ignoring brackets that sit
    inside quoted scalars."""
    depth = 0
    in_squote = in_dquote = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_squote:
            if c == "'":
                in_squote = False
        elif in_dquote:
            if c == "\\":
                i += 1
            elif c == '"':
                in_dquote = False
        else:
            if c == "'":
                in_squote = True
            elif c == '"':
                in_dquote = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
        i += 1
    return depth


def _strip_comment(line):
    in_squote = False
    in_dquote = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_squote:
            if c == "'":
                if i + 1 < n and line[i + 1] == "'":
                    i += 1
                else:
                    in_squote = False
        elif in_dquote:
            if c == "\\":
                i += 1
            elif c == '"':
                in_dquote = False
        else:
            if c == "'":
                in_squote = True
            elif c == '"':
                in_dquote = True
            elif c == "#" and (i == 0 or line[i - 1] in " \t"):
                return line[:i]
        i += 1
    return line


def _split_key_value(content):
    """Split "key: value" / "key:" into (key, rest), or None if not a mapping line."""
    if content == "":
        return None
    if content[0] in ("'", '"'):
        quote = content[0]
        i = 1
        n = len(content)
        if quote == "'":
            while i < n:
                if content[i] == "'":
                    if i + 1 < n and content[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        else:
            while i < n:
                if content[i] == "\\":
                    i += 2
                    continue
                if content[i] == '"':
                    break
                i += 1
        if i >= n:
            return None
        key = _unquote(content[: i + 1])
        rest = content[i + 1 :].strip()
        if not rest.startswith(":"):
            return None
        return key, rest[1:].strip()
    i = 0
    n = len(content)
    while i < n:
        if content[i] == ":" and (i + 1 == n or content[i + 1] == " "):
            return content[:i].strip(), content[i + 1 :].strip()
        i += 1
    return None


def _unquote(token):
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1].replace("''", "'")
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return _unescape_double(token[1:-1])
    return token


def _unescape_double(body):
    out = []
    i = 0
    n = len(body)
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            nxt = body[i + 1]
            out.append(escapes.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _coerce_scalar(token):
    token = token.strip()
    if token == "":
        return None
    if token and token[0] in ("'", '"'):
        return _unquote(token)
    if token in ("~", "null", "Null", "NULL"):
        return None
    if token in ("true", "True", "TRUE"):
        return True
    if token in ("false", "False", "FALSE"):
        return False
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    return token


def _scalar_size(value):
    if isinstance(value, str):
        return len(value) + 1
    if isinstance(value, int):
        return value.bit_length() // 3 + 1  # its decimal digits, without building the string
    return 1


def _split_anchor(text):
    """Peel a leading `&name` off `text`: (name, the rest) or (None, text)."""
    m = _ANCHOR_RE.match(text)
    if m is None:
        return None, text
    return m.group(1), text[m.end() :]


class _FlowParser:
    """Recursive-descent parser for inline `[...]` / `{...}` flow collections."""

    def __init__(self, s, owner):
        self.s = s
        self.i = 0
        self.n = len(s)
        self._depth = 0
        self.owner = owner

    def parse(self):
        self._ws()
        return self._value()

    def _ws(self):
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1

    def _value(self):
        self._ws()
        if self.i >= self.n:
            return None
        c = self.s[self.i]
        if c == "[" or c == "{":
            # Cap flow nesting the same way parse_block caps block nesting, so
            # a crafted `[[[[...` can't blow Python's stack (a bare
            # RecursionError) instead of degrading to a clean YamlError.
            self._depth += 1
            if self._depth > MAX_DEPTH:
                raise YamlError(f"flow nesting exceeds {MAX_DEPTH} levels")
            try:
                return self._list() if c == "[" else self._map()
            finally:
                self._depth -= 1
        if c in ("'", '"'):
            return self._quoted()
        if c == "&":
            name = self._name()
            value = self._value()
            self.owner.anchors[name] = value
            return value
        if c == "*":
            return self.owner.resolve(self._name())
        return self._plain()

    def _name(self):
        self.i += 1
        start = self.i
        while self.i < self.n and self.s[self.i] not in " \t,[]{}":
            self.i += 1
        if self.i == start:
            raise YamlError(f"anchor or alias with no name: {self.s!r}")
        return self.s[start : self.i]

    def _list(self):
        self.i += 1
        out = []
        self._ws()
        if self.i < self.n and self.s[self.i] == "]":
            self.i += 1
            return out
        while True:
            out.append(self._value())
            self._ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                self._ws()
                if self.i < self.n and self.s[self.i] == "]":
                    self.i += 1
                    return out
                continue
            if self.i < self.n and self.s[self.i] == "]":
                self.i += 1
                return out
            # No closing `]`: raise rather than silently return a truncated
            # list, which would drop the rest of the value with no error.
            raise YamlError(f"unterminated flow sequence: {self.s!r}")

    def _map(self):
        self.i += 1
        out = {}
        self._ws()
        if self.i < self.n and self.s[self.i] == "}":
            self.i += 1
            return out
        while True:
            self._ws()
            quoted = self.i < self.n and self.s[self.i] in ("'", '"')
            key = self._value()
            if key == "<<" and not quoted:
                raise YamlError(_MERGE_KEY_ERROR)
            if isinstance(key, (list, dict)):
                raise YamlError("a flow mapping key must be a scalar, not a sequence or a mapping")
            self._ws()
            if self.i < self.n and self.s[self.i] == ":":
                self.i += 1
            val = self._value()
            out[key if isinstance(key, str) else str(key)] = val
            self._ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                if self.i < self.n and self.s[self.i] == "}":
                    self.i += 1
                    return out
                continue
            if self.i < self.n and self.s[self.i] == "}":
                self.i += 1
                return out
            raise YamlError(f"unterminated flow mapping: {self.s!r}")

    def _quoted(self):
        quote = self.s[self.i]
        self.i += 1
        buf = []
        if quote == "'":
            while self.i < self.n:
                if self.s[self.i] == "'":
                    if self.i + 1 < self.n and self.s[self.i + 1] == "'":
                        buf.append("'")
                        self.i += 2
                        continue
                    self.i += 1
                    break
                buf.append(self.s[self.i])
                self.i += 1
        else:
            escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
            while self.i < self.n:
                c = self.s[self.i]
                if c == "\\" and self.i + 1 < self.n:
                    buf.append(escapes.get(self.s[self.i + 1], self.s[self.i + 1]))
                    self.i += 2
                    continue
                if c == '"':
                    self.i += 1
                    break
                buf.append(c)
                self.i += 1
        return "".join(buf)

    def _plain(self):
        start = self.i
        while self.i < self.n and self.s[self.i] not in ",[]{}:":
            self.i += 1
        return _coerce_scalar(self.s[start : self.i])
