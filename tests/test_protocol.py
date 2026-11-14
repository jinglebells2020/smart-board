"""Tests for protocol.py: the names and rules of protocol version 2."""

import unittest
from pathlib import Path

import protocol as p

ROOT = Path(__file__).resolve().parent.parent


class TestNames(unittest.TestCase):
    def test_row_names(self):
        self.assertEqual(len(p.TOP_ROWS), 63)
        self.assertEqual((p.TOP_ROWS[0], p.TOP_ROWS[-1]), ("T1", "T63"))
        self.assertEqual((p.BOTTOM_ROWS[0], p.BOTTOM_ROWS[-1]), ("B1", "B63"))
        self.assertEqual(p.RAILS, ["PT+", "PT-", "PB+", "PB-"])
        self.assertEqual(len(p.ALL_NODES), 130)

    def test_is_row_and_is_node(self):
        self.assertTrue(p.is_row("T10") and p.is_row("B63"))
        self.assertFalse(p.is_row("PT+"))  # power strips have no light
        self.assertTrue(p.is_node("PT+"))
        for bad in ("T0", "T64", "C3", "t10", "", "probe1"):
            self.assertFalse(p.is_node(bad), bad)

    def test_parse_rows(self):
        self.assertEqual(p.parse_rows("T1-T16"), [f"T{i}" for i in range(1, 17)])
        self.assertEqual(p.parse_rows("16"), [f"T{i}" for i in range(1, 17)])
        self.assertEqual(len(p.parse_rows("all")), 130)
        self.assertEqual(p.parse_rows("PT+, T3, T1-T2"), ["T1", "T2", "T3", "PT+"])
        self.assertEqual(p.parse_rows("B5-7"), ["B5", "B6", "B7"])
        for bad in ("T0-T5", "T10-T5", "X1", "64", "T1-T99"):
            with self.assertRaises(ValueError, msg=bad):
                p.parse_rows(bad)

    def test_row_label_is_plain(self):
        self.assertEqual(p.row_label("T10"), "row 10")
        self.assertEqual(p.row_label("B10"), "row 10 (lower half)")
        self.assertNotIn("rail", p.row_label("PT+"))


class TestColorsAndButtons(unittest.TestCase):
    def test_colors_and_meanings(self):
        self.assertEqual(p.COLORS, {"blue": "put a part here", "green": "correct",
                                    "red": "problem here", "yellow": "careful",
                                    "off": "light off"})

    def test_buttons_in_one_place(self):
        self.assertEqual(p.BUTTONS, {1: "check", 2: "hint", 3: "next_step", 4: "power"})
        self.assertEqual(set(p.BUTTON_LABELS), {1, 2, 3, 4})


class TestScreen(unittest.TestCase):
    def check_fits(self, lines):
        self.assertLessEqual(len(lines), p.SCREEN_LINES)
        for line in lines:
            self.assertLessEqual(len(line), p.SCREEN_CHARS, line)
            self.assertNotIn("|", line)
            line.encode("ascii")

    def test_size_setting(self):
        self.assertEqual((p.SCREEN_LINES, p.SCREEN_CHARS), (4, 20))

    def test_short_text(self):
        self.assertEqual(p.fit_screen("Step 2 of 4|220 ohm resistor"),
                         ["Step 2 of 4", "220 ohm resistor"])

    def test_wraps_words(self):
        lines = p.fit_screen("Switch the power off by hand now, then fix the circuit")
        self.check_fits(lines)
        self.assertEqual(lines[0], "Switch the power off")

    def test_never_more_than_the_screen(self):
        for text in ["x" * 500, "word " * 200, "a|b|c|d|e|f|g",
                     "Supercalifragilisticexpialidocious is long",
                     "Arrows → and Ω and “quotes”"]:
            with self.subTest(text=text[:30]):
                lines = p.fit_screen(text)
                self.check_fits(lines)
        self.assertTrue(p.fit_screen("a|b|c|d|e")[-1].endswith(".."))

    def test_other_sizes(self):
        lines = p.fit_screen("one two three four five six", lines=2, chars=8)
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(len(l) <= 8 for l in lines))

    def test_screen_command(self):
        self.assertEqual(p.screen_command("Hi|there"), "SHOW Hi|there")


class TestProtocolDoc(unittest.TestCase):
    def test_doc_describes_version_2(self):
        doc = (ROOT / "docs" / "SERIAL_PROTOCOL.md").read_text(encoding="utf-8")
        for needed in ['"type": "hello"', '"has_current"', '"has_power_switch"',
                       '"has_screen"', '"buttons"', '"floating"', '"current_ma": null',
                       "LED <row> <color>", "LED CLEAR", "SHOW", "no power switch",
                       '"type": "button"', "t_ms", "T1", "B63", "PT+", "PB-"]:
            self.assertIn(needed, doc)
        for color, meaning in p.COLORS.items():
            self.assertIn(f"`{color}`", doc)


if __name__ == "__main__":
    unittest.main()
