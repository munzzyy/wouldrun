"""GitHub Actions filter-pattern glob matching.

This implements the "filter pattern cheat sheet" semantics GitHub documents
for `branches`, `branches-ignore`, `tags`, `tags-ignore`, `paths`, and
`paths-ignore`:

  *      any run of characters, but never `/`
  **     as a whole path segment, matches zero or more path segments; a
         leading `**/` or trailing `/**` also folds away its own `/`, so
         `**/README.md` matches a root-level `README.md` too
  ?      zero or one of the character (or `[...]` class) immediately
         before it
  +      one or more of the character (or `[...]` class) immediately
         before it
  [...]  a character class; a leading `!` or `^` negates it
  \\x     a literal `x`, escaping whatever glob meaning it would have had

Patterns are matched against the whole string (a path or a ref name),
start to end, the same way GitHub does. Leading `!` negation on an entire
pattern is a list-level concern (a pattern can flip an earlier match within
a `paths:`/`branches:` list) and is handled by the caller, not here.

A run of two or more consecutive `*` anywhere in a segment -- not just as
the whole segment -- crosses `/` the same way: GitHub's cheat sheet gives
`**.js` as a worked example matching `index.js`, `js/index.js`, and
`src/js/app.js`. It does not get the folded-slash treatment a whole-segment
`**` gets (there is no adjoining `/` to fold), only the cross-slash part.
A single, non-doubled `*` still stops at `/` as described above.
"""

from __future__ import annotations

import functools
import re


class GlobError(ValueError):
    """A pattern could not be translated or compiled."""


def match(pattern: str, value: str) -> bool:
    # Matching runs a linear reach-set sweep over a compiled token list rather
    # than a backtracking regex. A regex translation of these globs (each `*`
    # becoming `[^/]*`) is exponentially ambiguous: a pattern like
    # `*a*a*a...*!` sends Python's engine into catastrophic backtracking and
    # hangs the whole run on a legal filter -- which SECURITY.md classes as a
    # vulnerability, since workflow files are third-party input here. The DP
    # below is polynomial with no backtracking, and handles quantified atoms
    # (`?`/`+`/`*`) directly, which also removes the possessive-`*+` footgun.
    tokens = _compile(pattern)
    n = len(value)
    reach = [False] * (n + 1)
    reach[0] = True
    for tok in tokens:
        reach = _advance(tok, reach, value)
        if not any(reach):
            return False
    return reach[n]


def _advance(tok, reach, s):
    """Given the set of string offsets reachable before `tok` (as a bool list
    indexed by offset), return the set reachable after it."""
    n = len(s)
    new = [False] * (n + 1)
    kind = tok[0]

    if kind == "presegs":
        # `(?:.*/)?`: zero, or any run of characters ending at a `/`.
        for i in range(n + 1):
            new[i] = reach[i]
        seen = False
        for k in range(n):
            if reach[k]:
                seen = True
            if s[k] == "/" and seen:
                new[k + 1] = True
        return new

    if kind == "postsegs":
        # `(?:/.*)?`: zero, or a `/` followed by anything to the end.
        for i in range(n + 1):
            new[i] = reach[i]
        for i in range(n):
            if reach[i] and s[i] == "/":
                for j in range(i + 1, n + 1):
                    new[j] = True
                break
        return new

    _, pred, quant = tok
    if quant == "one":
        for i in range(n):
            if reach[i] and pred(s[i]):
                new[i + 1] = True
    elif quant == "opt":
        for i in range(n + 1):
            new[i] = reach[i]
        for i in range(n):
            if reach[i] and pred(s[i]):
                new[i + 1] = True
    elif quant == "plus":
        for i in range(n):
            if (reach[i] or new[i]) and pred(s[i]):
                new[i + 1] = True
    else:  # "star"
        for i in range(n + 1):
            new[i] = reach[i]
        for i in range(n):
            if new[i] and pred(s[i]):
                new[i + 1] = True
    return new


def _any(_ch):
    return True


def _not_slash(ch):
    return ch != "/"


def _is_slash(ch):
    return ch == "/"


def _literal(c):
    return lambda ch: ch == c


@functools.lru_cache(maxsize=1024)
def _compile(pattern: str):
    try:
        return _compile_tokens(pattern)
    except re.error as e:
        raise GlobError(f"invalid filter pattern {pattern!r}: {e}") from e


def _compile_tokens(pattern):
    if pattern == "":
        return []
    segments = pattern.split("/")
    n = len(segments)
    tokens = []
    prev_globstar = False
    for i, seg in enumerate(segments):
        is_first = i == 0
        is_last = i == n - 1
        if seg == "**":
            if is_first and is_last:
                tokens.append(("atom", _any, "star"))
            elif is_first:
                tokens.append(("presegs",))
            elif is_last:
                tokens.append(("postsegs",))
            else:
                tokens.append(("atom", _is_slash, "one"))
                tokens.append(("presegs",))
            prev_globstar = True
        else:
            if not is_first and not prev_globstar:
                tokens.append(("atom", _is_slash, "one"))
            tokens.extend(_segment_tokens(seg))
            prev_globstar = False
    return tokens


def _segment_tokens(seg):
    tokens = []
    i = 0
    n = len(seg)
    while i < n:
        c = seg[i]
        if c == "\\" and i + 1 < n:
            tokens.append(("atom", _literal(seg[i + 1]), "one"))
            i += 2
            continue
        if c == "*":
            # A run of two or more consecutive `*` crosses `/` the same way a
            # whole-segment `**` does -- GitHub's cheat sheet gives `**.js` as
            # an example matching `index.js`, `js/index.js`, and
            # `src/js/app.js`. A lone `*` stays within its segment.
            j = i
            while j < n and seg[j] == "*":
                j += 1
            pred = _any if j - i >= 2 else _not_slash
            tokens.append(("atom", pred, "star"))
            i = j
            continue
        if c == "?":
            # GitHub's `?` means "zero or one of the atom immediately before
            # it", a postfix quantifier -- not classic-glob "one arbitrary
            # character".
            _apply_quant(tokens, "?")
            i += 1
            continue
        if c == "+":
            _apply_quant(tokens, "+")
            i += 1
            continue
        if c == "[":
            end = _find_class_end(seg, i)
            if end == -1:
                tokens.append(("atom", _literal(c), "one"))
                i += 1
                continue
            tokens.append(("atom", _class_pred(seg[i + 1 : end]), "one"))
            i = end + 1
            continue
        tokens.append(("atom", _literal(c), "one"))
        i += 1
    return tokens


def _apply_quant(tokens, ch):
    if not tokens or tokens[-1][0] != "atom":
        # Nothing to quantify -- GitHub doesn't document a leading `?`/`+`, so
        # fall back to a literal, the way a stray `+` already did.
        tokens.append(("atom", _literal(ch), "one"))
        return
    _, pred, quant = tokens[-1]
    if quant == "one":
        quant = "opt" if ch == "?" else "plus"
    # Otherwise the previous atom is already a `*`/`?`/`+` -- a `*` absorbs a
    # trailing `?`/`+` (a possessive `[^/]*+` would otherwise match nothing),
    # keeping the wider set. Leave the quantifier as it is.
    tokens[-1] = ("atom", pred, quant)


def _class_pred(body):
    rx = re.compile(_translate_class(body), re.DOTALL)
    return lambda ch: rx.fullmatch(ch) is not None


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
