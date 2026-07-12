"""GitHub Actions filter-pattern glob matching.

This implements the "filter pattern cheat sheet" semantics GitHub documents
for `branches`, `branches-ignore`, `tags`, `tags-ignore`, `paths`, and
`paths-ignore`:

  *      any run of characters, but never `/`
  **     as a whole path segment, matches zero or more path segments; a
         leading `**/` or trailing `/**` also folds away its own `/`, so
         `**/README.md` matches a root-level `README.md` too
  ?      exactly one character, but never `/`
  +      one or more of the character (or `[...]` class) immediately
         before it
  [...]  a character class; a leading `!` or `^` negates it
  \\x     a literal `x`, escaping whatever glob meaning it would have had

Patterns are matched against the whole string (a path or a ref name),
start to end, the same way GitHub does. Leading `!` negation on an entire
pattern is a list-level concern (a pattern can flip an earlier match within
a `paths:`/`branches:` list) and is handled by the caller, not here.

A `**` that shows up in the middle of a single path segment (e.g.
`foo**bar`, as opposed to `foo/**/bar`) is treated the same as two
consecutive `*` characters rather than given the cross-slash, folded-slash
treatment described above. GitHub does not publish a precise grammar for
that shape and it is vanishingly rare in real workflows, so wouldrun
documents rather than guesses at it.
"""

from __future__ import annotations

import functools
import re


class GlobError(ValueError):
    """A pattern could not be translated or compiled."""


def match(pattern: str, value: str) -> bool:
    return _compiled(pattern).fullmatch(value) is not None


@functools.lru_cache(maxsize=1024)
def _compiled(pattern: str):
    try:
        return re.compile(translate(pattern), re.DOTALL)
    except re.error as e:
        raise GlobError(f"invalid filter pattern {pattern!r}: {e}") from e


def translate(pattern: str) -> str:
    if pattern == "":
        return ""
    segments = pattern.split("/")
    n = len(segments)
    out = []
    prev_globstar = False
    for i, seg in enumerate(segments):
        is_first = i == 0
        is_last = i == n - 1
        if seg == "**":
            if is_first and is_last:
                frag, need_slash = ".*", False
            elif is_first:
                frag, need_slash = "(?:.*/)?", False
            elif is_last:
                frag, need_slash = "(?:/.*)?", False
            else:
                frag, need_slash = "(?:.*/)?", True
            if need_slash:
                out.append("/")
            out.append(frag)
            prev_globstar = True
        else:
            if not is_first and not prev_globstar:
                out.append("/")
            out.append(_segment_to_regex(seg))
            prev_globstar = False
    return "".join(out)


def _segment_to_regex(seg):
    out = []
    last_atom = None
    i = 0
    n = len(seg)
    while i < n:
        c = seg[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(seg[i + 1]))
            last_atom = len(out) - 1
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
            last_atom = len(out) - 1
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            last_atom = len(out) - 1
            i += 1
            continue
        if c == "+":
            if last_atom is None:
                out.append(re.escape("+"))
                last_atom = len(out) - 1
            else:
                out[last_atom] = out[last_atom] + "+"
            i += 1
            continue
        if c == "[":
            end = _find_class_end(seg, i)
            if end == -1:
                out.append(re.escape(c))
                last_atom = len(out) - 1
                i += 1
                continue
            out.append(_translate_class(seg[i + 1 : end]))
            last_atom = len(out) - 1
            i = end + 1
            continue
        out.append(re.escape(c))
        last_atom = len(out) - 1
        i += 1
    return "".join(out)


def _find_class_end(seg, start):
    i = start + 1
    n = len(seg)
    if i < n and seg[i] in ("!", "^"):
        i += 1
    if i < n and seg[i] == "]":
        i += 1
    while i < n and seg[i] != "]":
        i += 1
    return i if i < n else -1


def _translate_class(body):
    if body == "":
        return re.escape("[]")
    neg = body[0] in ("!", "^")
    rest = body[1:] if neg else body
    safe = rest.replace("\\", "\\\\").replace("]", "\\]")
    return f"[{'^' if neg else ''}{safe}]"
