"""Tests for sim/fake_board.py.

Run from the repository folder:

    python -m unittest -v

The simulator works out its numbers from Ohm's law. These tests check
those numbers against lessons/*.json, so a typo in either place fails.
"""

import json
import statistics
import subprocess
import sys
import unittest
from pathlib import Path

from sim.fake_board import CURRENT_LIMIT_MA, SCENARIOS, FakeBoard

ROOT = Path(__file__).resolve().parent.parent
WINDOW = 20  # readings per check (2 seconds of real board time)


def load_lessons():
    lessons = {}
    for path in sorted((ROOT / "lessons").glob("lesson*.json")):
        data = json.loads(path.read_text())
        lessons[data["id"]] = data
    return lessons


LESSONS = load_lessons()


def summarize(readings):
    """Average the readings over a window, like the agent will."""
    probe = [r["voltages"]["probe1"] for r in readings]
    return {
        "current_ma": statistics.mean(r["current_ma"] for r in readings),
        "supply_v": statistics.mean(r["supply_v"] for r in readings),
        "probe1_v": statistics.mean(probe),
        "probe1_v_spread": max(probe) - min(probe),
    }


def in_range(value, rule):
    """rule looks like {"min": 1, "max": 2} or [1, 2]."""
    if isinstance(rule, list):
        rule = {"min": rule[0], "max": rule[1]}
    return rule.get("min", float("-inf")) <= value <= rule.get("max", float("inf"))


def matches(summary, detect):
    return all(in_range(summary[field], rule) for field, rule in detect.items())


def window(lesson, scenario, seed=1):
    board = FakeBoard(lesson, scenario, seed=seed)
    return [board.read() for _ in range(WINDOW)]


class TestLessonFiles(unittest.TestCase):
    def test_lessons_found(self):
        self.assertEqual(sorted(LESSONS), [1, 2])

    def test_every_mistake_has_a_scenario_and_back(self):
        for lesson_id, lesson in LESSONS.items():
            mistake_ids = {m["id"] for m in lesson["mistakes"]}
            sim_ids = set(SCENARIOS[lesson_id]) - {"correct"}
            self.assertEqual(mistake_ids, sim_ids, f"lesson {lesson_id}")


class TestReadingFormat(unittest.TestCase):
    def test_fields_and_types(self):
        r = FakeBoard(seed=0).read()
        self.assertEqual(r["type"], "reading")
        self.assertIsInstance(r["t_ms"], int)
        self.assertIsInstance(r["power_on"], bool)
        self.assertIsInstance(r["supply_v"], float)
        self.assertIsInstance(r["current_ma"], float)
        self.assertIsInstance(r["voltages"]["probe1"], float)
        self.assertIsNone(r["led_row"])

    def test_is_one_json_line(self):
        line = json.dumps(FakeBoard(seed=0).read())
        self.assertNotIn("\n", line)
        self.assertEqual(json.loads(line)["type"], "reading")

    def test_time_advances_100ms(self):
        board = FakeBoard(seed=0)
        self.assertEqual([board.read()["t_ms"] for _ in range(3)], [0, 100, 200])

    def test_same_seed_same_readings(self):
        self.assertEqual(window(1, "correct", seed=7), window(1, "correct", seed=7))


class TestCorrectCircuits(unittest.TestCase):
    def test_correct_is_accepted_and_no_mistake_matches(self):
        for lesson_id, lesson in LESSONS.items():
            with self.subTest(lesson=lesson_id):
                s = summarize(window(lesson_id, "correct"))
                self.assertTrue(matches(s, lesson["accept"]), s)
                for m in lesson["mistakes"]:
                    self.assertFalse(matches(s, m["detect"]), m["id"])

    def test_correct_values_match_expected(self):
        for lesson_id, lesson in LESSONS.items():
            with self.subTest(lesson=lesson_id):
                s = summarize(window(lesson_id, "correct"))
                exp = lesson["expected"]
                self.assertAlmostEqual(s["current_ma"], exp["current_ma"], delta=0.1)
                self.assertAlmostEqual(s["probe1_v"], exp["probe1_v"], delta=0.05)


class TestMistakes(unittest.TestCase):
    def test_each_mistake_matches_only_itself_and_is_not_accepted(self):
        for lesson_id, lesson in LESSONS.items():
            for mistake in lesson["mistakes"]:
                with self.subTest(lesson=lesson_id, mistake=mistake["id"]):
                    s = summarize(window(lesson_id, mistake["id"]))
                    self.assertTrue(matches(s, mistake["detect"]), s)
                    for other in lesson["mistakes"]:
                        if other is not mistake:
                            self.assertFalse(matches(s, other["detect"]), other["id"])
                    if mistake["id"] != "middle_floating":
                        # A floating probe can land inside the range by
                        # luck; the spread check is what catches it.
                        self.assertFalse(matches(s, lesson["accept"]), s)

    def test_lesson1_numbers(self):
        self.assertAlmostEqual(summarize(window(1, "led_backwards"))["probe1_v"], 5.0, delta=0.05)
        self.assertAlmostEqual(summarize(window(1, "open_circuit"))["probe1_v"], 0.0, delta=0.05)
        s = summarize(window(1, "no_resistor"))
        self.assertAlmostEqual(s["current_ma"], CURRENT_LIMIT_MA, delta=CURRENT_LIMIT_MA * 0.05)

    def test_lesson2_numbers(self):
        s = summarize(window(2, "bottom_1k"))
        self.assertAlmostEqual(s["probe1_v"], 5 * 1 / 11, delta=0.05)  # 0.45 V
        self.assertAlmostEqual(s["current_ma"], 5 / 11, delta=0.05)  # 0.45 mA
        s = summarize(window(2, "middle_floating"))
        self.assertAlmostEqual(s["current_ma"], 0.25, delta=0.02)  # divider still works


class TestCommands(unittest.TestCase):
    def test_cut_and_restore(self):
        board = FakeBoard(1, "no_resistor", seed=0)
        self.assertEqual(board.send("CUT"), {"type": "ack", "cmd": "CUT"})
        r = board.read()
        self.assertFalse(r["power_on"])
        self.assertEqual((r["current_ma"], r["supply_v"], r["voltages"]["probe1"]), (0.0, 0.0, 0.0))
        self.assertEqual(board.send("RESTORE"), {"type": "ack", "cmd": "RESTORE"})
        self.assertTrue(board.read()["power_on"])

    def test_led_row(self):
        board = FakeBoard(seed=0)
        self.assertEqual(board.send("LED 5"), {"type": "ack", "cmd": "LED 5"})
        self.assertEqual(board.read()["led_row"], 5)

    def test_lowercase_and_spaces_ok(self):
        self.assertEqual(FakeBoard().send("  cut \n")["type"], "ack")

    def test_bad_commands(self):
        board = FakeBoard()
        for bad in ["", "FOO", "LED", "LED abc", "LED 0", "CUT now", "SCENARIO 9 x"]:
            with self.subTest(cmd=bad):
                self.assertEqual(board.send(bad)["type"], "error")

    def test_scenario_switch(self):
        board = FakeBoard(1, "correct", seed=0)
        self.assertEqual(board.send("SCENARIO 1 open_circuit")["type"], "ack")
        self.assertLess(board.read()["current_ma"], 1)


class TestCommandLine(unittest.TestCase):
    def run_sim(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "sim" / "fake_board.py"), *args],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )

    def test_prints_json_lines(self):
        out = self.run_sim("--lesson", "2", "--scenario", "bottom_1k",
                           "--count", "3", "--interval", "0", "--seed", "1")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(json.loads(line)["type"], "reading")

    def test_list(self):
        out = self.run_sim("--list")
        self.assertIn("led_backwards", out.stdout)

    def test_bad_scenario_fails(self):
        self.assertEqual(self.run_sim("--scenario", "nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
