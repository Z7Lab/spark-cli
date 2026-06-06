"""Unit tests for the generic argv→params parser.

Run: python3 -m pytest tests/test_cliparse.py   (or: python3 tests/test_cliparse.py)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import cliparse
from cliparse import ParseError


class TestParse(unittest.TestCase):
    def test_positional_required_present(self):
        spec = [{"name": "model", "positional": True, "required": True}]
        self.assertEqual(cliparse.parse(spec, ["m"]), {"model": "m"})

    def test_positional_required_missing(self):
        spec = [{"name": "model", "positional": True, "required": True}]
        with self.assertRaises(ParseError):
            cliparse.parse(spec, [])

    def test_flag_value_and_default(self):
        spec = [{"name": "ctx", "type": "int", "default": 8192}]
        self.assertEqual(cliparse.parse(spec, [])["ctx"], 8192)
        self.assertEqual(cliparse.parse(spec, ["--ctx", "4096"])["ctx"], 4096)

    def test_int_coercion_error(self):
        spec = [{"name": "ctx", "type": "int"}]
        with self.assertRaises(ParseError):
            cliparse.parse(spec, ["--ctx", "abc"])

    def test_float_coercion(self):
        spec = [{"name": "guidance", "type": "float", "default": 3.5}]
        self.assertEqual(cliparse.parse(spec, ["--guidance", "2.0"])["guidance"], 2.0)

    def test_bool_flag(self):
        spec = [{"name": "all", "type": "bool"}]
        self.assertFalse(cliparse.parse(spec, [])["all"])
        self.assertTrue(cliparse.parse(spec, ["--all"])["all"])

    def test_enum_valid_and_invalid(self):
        spec = [{"name": "set", "options": ["a", "b"], "default": "a"}]
        self.assertEqual(cliparse.parse(spec, ["--set", "b"])["set"], "b")
        with self.assertRaises(ParseError):
            cliparse.parse(spec, ["--set", "c"])

    def test_variadic_positional(self):
        spec = [{"name": "names", "positional": True, "variadic": True}]
        self.assertEqual(cliparse.parse(spec, ["x", "y", "z"])["names"], ["x", "y", "z"])
        self.assertEqual(cliparse.parse(spec, [])["names"], [])

    def test_variadic_with_flag_mixed(self):
        spec = [
            {"name": "names", "positional": True, "variadic": True},
            {"name": "all", "type": "bool"},
        ]
        r = cliparse.parse(spec, ["a", "--all", "b"])
        self.assertEqual(r["names"], ["a", "b"])
        self.assertTrue(r["all"])

    def test_rest_captures_raw_tail(self):
        spec = [
            {"name": "name", "positional": True, "required": True},
            {"name": "opts", "rest": True},
        ]
        r = cliparse.parse(spec, ["pb", "--step", "x", "--model", "m"])
        self.assertEqual(r["name"], "pb")
        self.assertEqual(r["opts"], ["--step", "x", "--model", "m"])

    def test_unknown_flag_hard_fails(self):
        with self.assertRaises(ParseError):
            cliparse.parse([{"name": "ctx", "type": "int"}], ["--bogus", "1"])

    def test_missing_flag_value(self):
        with self.assertRaises(ParseError):
            cliparse.parse([{"name": "out"}], ["--out"])

    def test_kebab_to_snake_key(self):
        spec = [{"name": "search_box", "type": "string"}]
        r = cliparse.parse(spec, ["--search-box", "0,0,1,1"])
        self.assertEqual(r["search_box"], "0,0,1,1")

    def test_negative_number_flag_value(self):
        spec = [{"name": "dy", "type": "int", "default": 0}]
        self.assertEqual(cliparse.parse(spec, ["--dy", "-5"])["dy"], -5)

    def test_extra_positional_rejected(self):
        spec = [{"name": "a", "positional": True}]
        with self.assertRaises(ParseError):
            cliparse.parse(spec, ["x", "y"])

    def test_required_flag_enforced(self):
        spec = [{"name": "out", "type": "string", "required": True}]
        with self.assertRaises(ParseError):
            cliparse.parse(spec, [])
        self.assertEqual(cliparse.parse(spec, ["--out", "p"])["out"], "p")

    def test_flag_token_roundtrip(self):
        self.assertEqual(cliparse.flag_token("search_box"), "--search-box")


if __name__ == "__main__":
    unittest.main(verbosity=2)
