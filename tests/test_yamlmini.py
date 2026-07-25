"""Unit tests for the minimal YAML reader."""

import unittest

from wouldrun import yamlmini
from wouldrun.yamlmini import YamlError, load


class Scalars(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(load("name: foo"), {"name": "foo"})

    def test_single_quoted(self):
        self.assertEqual(load("name: 'foo bar'"), {"name": "foo bar"})

    def test_single_quoted_escape(self):
        self.assertEqual(load("name: 'it''s here'"), {"name": "it's here"})

    def test_double_quoted_escapes(self):
        self.assertEqual(load('name: "a\\nb"'), {"name": "a\nb"})

    def test_null_variants(self):
        for token in ("~", "null", "Null", "NULL"):
            self.assertIsNone(load(f"x: {token}")["x"])

    def test_true_false(self):
        self.assertIs(load("x: true")["x"], True)
        self.assertIs(load("x: True")["x"], True)
        self.assertIs(load("x: false")["x"], False)

    def test_on_off_yes_no_stay_strings(self):
        # This is the whole point: YAML 1.1 (what PyYAML's default loader
        # uses) would turn these into booleans. wouldrun must not.
        self.assertEqual(load("x: on")["x"], "on")
        self.assertEqual(load("x: off")["x"], "off")
        self.assertEqual(load("x: yes")["x"], "yes")
        self.assertEqual(load("x: no")["x"], "no")

    def test_int_and_float(self):
        self.assertEqual(load("x: 42")["x"], 42)
        self.assertEqual(load("x: -3")["x"], -3)
        self.assertEqual(load("x: 3.5")["x"], 3.5)

    def test_empty_value_is_none(self):
        self.assertIsNone(load("x:")["x"])


class OnKeyIsNeverBoolean(unittest.TestCase):
    def test_bare_on_key_stays_string(self):
        doc = load("on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
        self.assertIn("on", doc)
        self.assertNotIn(True, doc)
        self.assertEqual(doc["on"], "push")

    def test_on_mapping_key_stays_string(self):
        doc = load("on:\n  push:\n    branches: [main]\n")
        self.assertIn("on", doc)
        self.assertIsInstance(doc["on"], dict)
        self.assertIn("push", doc["on"])


class Comments(unittest.TestCase):
    def test_trailing_comment_stripped(self):
        self.assertEqual(load("x: foo # a comment"), {"x": "foo"})

    def test_hash_inside_quotes_not_a_comment(self):
        self.assertEqual(load('x: "a # b"'), {"x": "a # b"})

    def test_comment_only_line_skipped(self):
        doc = load("# top comment\nx: 1\n# trailing\n")
        self.assertEqual(doc, {"x": 1})

    def test_hash_mid_word_is_not_a_comment(self):
        self.assertEqual(load("x: v1.0#beta"), {"x": "v1.0#beta"})


class BlockCollections(unittest.TestCase):
    def test_nested_mapping(self):
        doc = load("a:\n  b:\n    c: 1\n")
        self.assertEqual(doc, {"a": {"b": {"c": 1}}})

    def test_block_sequence_of_scalars(self):
        doc = load("items:\n  - a\n  - b\n  - c\n")
        self.assertEqual(doc, {"items": ["a", "b", "c"]})

    def test_block_sequence_of_mappings(self):
        doc = load("schedule:\n  - cron: '0 0 * * *'\n  - cron: '0 12 * * *'\n")
        self.assertEqual(doc, {"schedule": [{"cron": "0 0 * * *"}, {"cron": "0 12 * * *"}]})

    def test_sequence_of_mappings_multi_key(self):
        text = "steps:\n  - name: build\n    run: echo hi\n  - name: test\n    run: echo bye\n"
        doc = load(text)
        self.assertEqual(
            doc["steps"],
            [{"name": "build", "run": "echo hi"}, {"name": "test", "run": "echo bye"}],
        )

    def test_empty_mapping_value(self):
        doc = load("on:\n  push:\n  pull_request:\n")
        self.assertEqual(doc, {"on": {"push": None, "pull_request": None}})

    def test_indentless_block_sequence_under_key(self):
        # A block sequence whose `-` items sit at the SAME indent as their
        # parent key is valid YAML that GitHub's parser accepts. The old
        # reader required a deeper indent and choked on the bare `- main`
        # line with a "malformed mapping line" error.
        doc = load("on:\n  push:\n    branches:\n    - main\n    - dev\n")
        self.assertEqual(doc, {"on": {"push": {"branches": ["main", "dev"]}}})

    def test_indentless_top_level_sequence(self):
        doc = load("on:\n- push\n- pull_request\n")
        self.assertEqual(doc, {"on": ["push", "pull_request"]})


class FlowCollections(unittest.TestCase):
    def test_inline_list(self):
        self.assertEqual(load("x: [a, b, c]"), {"x": ["a", "b", "c"]})

    def test_inline_list_quoted_items(self):
        self.assertEqual(load("x: ['a b', \"c,d\"]"), {"x": ["a b", "c,d"]})

    def test_inline_map(self):
        self.assertEqual(load("x: {a: 1, b: 2}"), {"x": {"a": 1, "b": 2}})

    def test_nested_inline(self):
        self.assertEqual(load("x: [{a: 1}, {a: 2}]"), {"x": [{"a": 1}, {"a": 2}]})

    def test_empty_inline_list(self):
        self.assertEqual(load("x: []"), {"x": []})

    def test_multiline_flow_sequence_is_joined(self):
        # A flow list whose brackets don't close on the same physical line is
        # standard YAML. The old reader saw only the `[` on the first line,
        # returned `[None]`, and silently dropped both the real items and
        # every following line -- including a whole `jobs:` section.
        doc = load("branches: [\n  main,\n  release\n]\njobs:\n  test: {}\n")
        self.assertEqual(doc["branches"], ["main", "release"])
        self.assertEqual(doc["jobs"], {"test": {}})

    def test_multiline_flow_mapping_is_joined(self):
        doc = load("x: {\n  a: 1,\n  b: 2\n}\ny: 3\n")
        self.assertEqual(doc, {"x": {"a": 1, "b": 2}, "y": 3})

    def test_unterminated_flow_sequence_raises(self):
        # An unterminated flow collection must be a loud parse error, not a
        # silent truncation of the rest of the value.
        with self.assertRaises(YamlError):
            load("x: [a, b\ny: 1\n")

    def test_flow_nesting_cap_is_a_yaml_error_not_a_recursion_error(self):
        # `[[[[...` past the nesting cap must degrade to a clean YamlError
        # like the block path already does, not blow Python's call stack.
        with self.assertRaises(YamlError):
            load("on: " + "[" * 50000)


class BlockScalars(unittest.TestCase):
    def test_literal_block_scalar_is_skipped_cleanly(self):
        text = "jobs:\n  build:\n    steps:\n      - run: |\n          echo one\n          echo two\n    runs-on: ubuntu-latest\n"
        doc = load(text)
        self.assertIn("run", doc["jobs"]["build"]["steps"][0])
        self.assertEqual(doc["jobs"]["build"]["runs-on"], "ubuntu-latest")

    def test_folded_block_scalar_boundary(self):
        text = "a: >\n  line one\n  line two\nb: 2\n"
        doc = load(text)
        self.assertEqual(doc["b"], 2)
        self.assertIn("line one", doc["a"])


class DocumentMarkers(unittest.TestCase):
    def test_leading_triple_dash(self):
        self.assertEqual(load("---\nx: 1\n"), {"x": 1})

    def test_trailing_dotdotdot(self):
        self.assertEqual(load("x: 1\n...\ny: 2\n"), {"x": 1})


class Malformed(unittest.TestCase):
    def test_tabs_in_indentation_rejected(self):
        with self.assertRaises(YamlError):
            load("a:\n\tb: 1\n")

    def test_oversized_input_rejected(self):
        huge = "x: " + ("a" * (yamlmini.MAX_BYTES + 10))
        with self.assertRaises(YamlError):
            load(huge)

    def test_bare_scalar_document_is_rejected(self):
        # A line with no ": " and no leading "- " is not a mapping or
        # sequence entry; this is a clear parse error, not a silent None.
        with self.assertRaises(YamlError):
            load("just text\n")

    def test_non_str_input_rejected(self):
        with self.assertRaises(YamlError):
            load(12345)  # type: ignore[arg-type]

    def test_deeply_nested_mapping_is_a_yaml_error_not_a_recursion_error(self):
        # 1200 levels of one-space-deeper nesting is ~700KB, well under
        # MAX_BYTES/MAX_LINES, but the old mutually-recursive parser blew
        # Python's call stack on input this shape. This must degrade to a
        # clean YamlError like every other malformed-input case, not a
        # bare RecursionError/traceback.
        nested = "".join(f"{' ' * i}a:\n" for i in range(1200))
        with self.assertRaises(YamlError):
            load(nested)

    def test_moderately_nested_mapping_still_parses(self):
        # The depth cap must have real headroom above anything a real
        # workflow file would ever do -- this is far deeper than any
        # GitHub Actions workflow's `on:`/`jobs:` tree.
        levels = 50
        nested = "".join(f"{'  ' * i}a:\n" for i in range(levels - 1)) + ("  " * (levels - 1)) + "a: 1\n"
        doc = load(nested)
        for _ in range(levels - 1):
            doc = doc["a"]
        self.assertEqual(doc["a"], 1)

    def test_many_sibling_keys_is_not_mistaken_for_deep_nesting(self):
        # Width, not depth: 2000 one-level-nested sibling keys means 2000
        # separate, shallow recursions into parse_block, one per key. Each
        # must fully unwind before the next sibling starts, so this must
        # not trip a depth guard meant only for actual nesting.
        text = "".join(f"key{i}:\n  a: {i}\n" for i in range(2000))
        doc = load(text)
        self.assertEqual(len(doc), 2000)
        self.assertEqual(doc["key1999"]["a"], 1999)


if __name__ == "__main__":
    unittest.main()
