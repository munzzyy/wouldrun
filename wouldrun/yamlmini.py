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
and a single leading `---` / trailing `...` document marker. Not supported:
anchors/aliases (a bare `&name`/`*name` token is kept as a literal string),
multi-document streams, and tag annotations (`!!str` and friends are kept
as part of the scalar text). None of those appear in the trigger and job
metadata this tool reads.
"""

from __future__ import annotations

import re

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 20000
MAX_DEPTH = 200  # nesting levels; real workflow files never get close

_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?\d*$")
_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+[eE][-+]?\d+|\d+\.\d*[eE][-+]?\d+)$")


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
            key, rest = split
            if rest == "":
                value, next_i = self.parse_block(real + 1, indent + 1)
            elif _BLOCK_SCALAR_RE.match(rest):
                value, next_i = self._consume_block_scalar(real, indent)
            else:
                value = _parse_scalar_or_flow(rest)
                next_i = real + 1
            result[key] = value
            i = next_i
        return result, i

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
            if rest == "":
                value, next_i = self.parse_block(real + 1, indent + 1)
                result.append(value)
                i = next_i
                continue
            # "- key: value" starts an inline mapping; the item's effective
            # indent is wherever `rest` began on this physical line.
            item_indent = indent + (len(content) - len(rest))
            split = _split_key_value(rest)
            if split is not None:
                key, kv_rest = split
                mapping = {}
                if kv_rest == "":
                    value, next_i = self.parse_block(real + 1, item_indent + 1)
                elif _BLOCK_SCALAR_RE.match(kv_rest):
                    value, next_i = self._consume_block_scalar_at(real, item_indent, kv_rest)
                else:
                    value = _parse_scalar_or_flow(kv_rest)
                    next_i = real + 1
                mapping[key] = value
                more, next_i = self._parse_mapping_continuation(next_i, item_indent, mapping)
                result.append(more)
                i = next_i
            elif _BLOCK_SCALAR_RE.match(rest):
                value, next_i = self._consume_block_scalar_at(real, item_indent, rest)
                result.append(value)
                i = next_i
            else:
                result.append(_parse_scalar_or_flow(rest))
                i = real + 1
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
            key, rest = split
            if rest == "":
                value, next_i = self.parse_block(real + 1, indent + 1)
            elif _BLOCK_SCALAR_RE.match(rest):
                value, next_i = self._consume_block_scalar(real, indent)
            else:
                value = _parse_scalar_or_flow(rest)
                next_i = real + 1
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
    if token.startswith("&") or token.startswith("*"):
        # Anchors/aliases are not resolved; keep the token as literal text
        # (wouldrun never needs this level of the file).
        return token
    return token


def _parse_scalar_or_flow(rest):
    rest = rest.strip()
    if rest == "":
        return None
    if rest[0] == "[" or rest[0] == "{":
        return _FlowParser(rest).parse()
    return _coerce_scalar(rest)


class _FlowParser:
    """Recursive-descent parser for inline `[...]` / `{...}` flow collections."""

    def __init__(self, s):
        self.s = s
        self.i = 0
        self.n = len(s)

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
        if c == "[":
            return self._list()
        if c == "{":
            return self._map()
        if c in ("'", '"'):
            return self._quoted()
        return self._plain()

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
                continue
            if self.i < self.n and self.s[self.i] == "]":
                self.i += 1
            break
        return out

    def _map(self):
        self.i += 1
        out = {}
        self._ws()
        if self.i < self.n and self.s[self.i] == "}":
            self.i += 1
            return out
        while True:
            self._ws()
            key = self._value()
            self._ws()
            if self.i < self.n and self.s[self.i] == ":":
                self.i += 1
            val = self._value()
            out[key if isinstance(key, str) else str(key)] = val
            self._ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i < self.n and self.s[self.i] == "}":
                self.i += 1
            break
        return out

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
