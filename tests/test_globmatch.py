"""Tests for the GitHub Actions filter-pattern glob translator."""

import unittest

from wouldrun.globmatch import GlobError, match


class SingleStar(unittest.TestCase):
    def test_matches_within_a_segment(self):
        self.assertTrue(match("*.js", "index.js"))

    def test_does_not_cross_slash(self):
        self.assertFalse(match("*.js", "src/index.js"))

    def test_prefix_star(self):
        self.assertTrue(match("Octo*", "Octocat"))
        self.assertFalse(match("Octo*", "notOctocat"))

    def test_bare_star_matches_one_segment(self):
        self.assertTrue(match("*", "main"))
        self.assertFalse(match("*", "releases/1.0"))


class DoubleStar(unittest.TestCase):
    def test_bare_double_star_matches_everything(self):
        self.assertTrue(match("**", "a/b/c.txt"))
        self.assertTrue(match("**", "c.txt"))

    def test_leading_double_star_matches_root_file(self):
        self.assertTrue(match("**/*.jpg", "img.jpg"))
        self.assertTrue(match("**/*.jpg", "avatars/img.jpg"))
        self.assertTrue(match("**/*.jpg", "a/b/c/img.jpg"))

    def test_leading_double_star_named_file(self):
        self.assertTrue(match("**/README.md", "README.md"))
        self.assertTrue(match("**/README.md", "docs/README.md"))
        self.assertTrue(match("**/README.md", "docs/x/README.md"))
        self.assertFalse(match("**/README.md", "README.md.bak"))

    def test_trailing_double_star_covers_directory(self):
        self.assertTrue(match("docs/**", "docs/a.md"))
        self.assertTrue(match("docs/**", "docs/a/b.md"))
        self.assertFalse(match("docs/**", "docsx/a.md"))

    def test_middle_double_star(self):
        self.assertTrue(match("a/**/b.txt", "a/b.txt"))
        self.assertTrue(match("a/**/b.txt", "a/x/b.txt"))
        self.assertTrue(match("a/**/b.txt", "a/x/y/b.txt"))
        self.assertFalse(match("a/**/b.txt", "a/b.txt.bak"))

    def test_mid_segment_double_star_crosses_slash(self):
        # GitHub's cheat sheet gives `**.js` as a worked example that
        # matches all three of these -- unlike a whole-segment `**`
        # (above), this `**` is only part of a segment, but it still has
        # to cross `/` the same way.
        self.assertTrue(match("**.js", "index.js"))
        self.assertTrue(match("**.js", "js/index.js"))
        self.assertTrue(match("**.js", "src/js/app.js"))

    def test_single_star_still_does_not_cross_slash(self):
        # A lone `*` (a run of exactly one) must keep the old
        # within-segment-only behavior even though star handling now also
        # has to look for runs of two or more.
        self.assertTrue(match("a*.js", "axyz.js"))
        self.assertFalse(match("a*.js", "a/b.js"))

    def test_double_star_segment_boundary_still_requires_the_slash(self):
        # The mid-segment `**` crosses `/` freely, but a literal `/`
        # elsewhere in the pattern (here, the one implied by the leading
        # `*` segment) is still mandatory.
        self.assertTrue(match("*/**.js", "a/b/app.js"))
        self.assertTrue(match("*/**.js", "a/app.js"))
        self.assertFalse(match("*/**.js", "app.js"))


class Question(unittest.TestCase):
    def test_zero_or_one_of_preceding_char(self):
        # GitHub's cheat sheet: `?` matches zero or one of the character
        # immediately before it -- a postfix quantifier, not "exactly one
        # arbitrary character" the way classic shell globs use `?`.
        self.assertTrue(match("v1.?", "v1"))
        self.assertTrue(match("v1.?", "v1."))
        self.assertFalse(match("v1.?", "v1.2"))

    def test_matches_at_most_one_not_more(self):
        # Distinguishes `?` from `*`/`+`: two dots is still too many.
        self.assertFalse(match("v1.?", "v1.."))

    def test_after_char_class(self):
        self.assertTrue(match("file[0-9]?.txt", "file.txt"))
        self.assertTrue(match("file[0-9]?.txt", "file5.txt"))
        self.assertFalse(match("file[0-9]?.txt", "file55.txt"))

    def test_after_star_does_not_cross_slash(self):
        self.assertTrue(match("a*?.txt", "a.txt"))
        self.assertTrue(match("a*?.txt", "abc.txt"))
        self.assertFalse(match("a*?.txt", "a/b.txt"))

    def test_leading_question_mark_has_no_preceding_atom(self):
        # GitHub doesn't document a `?` with nothing before it to quantify;
        # wouldrun falls back to a literal `?`, the same way a leading `+`
        # already does (see the Plus tests below).
        self.assertTrue(match("?abc", "?abc"))
        self.assertFalse(match("?abc", "abc"))


class Plus(unittest.TestCase):
    def test_one_or_more_of_preceding_char(self):
        self.assertTrue(match("Octo+cat", "Octocat"))
        self.assertTrue(match("Octo+cat", "Octoocat"))
        self.assertFalse(match("Octo+cat", "Octcat"))

    def test_one_or_more_of_preceding_class(self):
        # GitHub's own semver example: major version 1 or 2, then two
        # dot-separated runs of digits.
        self.assertTrue(match("v[12].[0-9]+.[0-9]+", "v1.2.3"))
        self.assertTrue(match("v[12].[0-9]+.[0-9]+", "v2.20.100"))
        self.assertFalse(match("v[12].[0-9]+.[0-9]+", "v3.0.0"))
        self.assertFalse(match("v[12].[0-9]+.[0-9]+", "v1.2"))


class CharacterClasses(unittest.TestCase):
    def test_range(self):
        self.assertTrue(match("file[0-9].txt", "file5.txt"))
        self.assertFalse(match("file[0-9].txt", "fileA.txt"))

    def test_explicit_set(self):
        self.assertTrue(match("[abc]atfile", "batfile"))
        self.assertFalse(match("[abc]atfile", "datfile"))

    def test_negated_class(self):
        self.assertTrue(match("[!0-9]file", "afile"))
        self.assertFalse(match("[!0-9]file", "5file"))


class Escaping(unittest.TestCase):
    def test_backslash_escapes_star(self):
        self.assertTrue(match(r"a\*b", "a*b"))
        self.assertFalse(match(r"a\*b", "axb"))

    def test_dot_is_literal(self):
        self.assertTrue(match("a.b", "a.b"))
        self.assertFalse(match("a.b", "axb"))


class WholeStringAnchoring(unittest.TestCase):
    def test_pattern_must_match_entire_value(self):
        self.assertFalse(match("main", "mainx"))
        self.assertFalse(match("main", "xmain"))
        self.assertTrue(match("main", "main"))


class Malformed(unittest.TestCase):
    def test_invalid_character_range_raises_glob_error_not_traceback(self):
        with self.assertRaises(GlobError):
            match("[z-a]", "m")

    def test_unterminated_bracket_falls_back_to_literal(self):
        # No closing ']' at all: treated as a literal '[' rather than raised,
        # since that is unambiguous and matches how a stray '[' behaves in
        # most shell globs.
        self.assertTrue(match("a[b", "a[b"))


if __name__ == "__main__":
    unittest.main()
