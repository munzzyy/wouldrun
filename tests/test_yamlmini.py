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


if __name__ == "__main__":
    unittest.main()
