"""Tests for sim/fake_board.py (protocol version 2).

Run from the repository folder:

    python -m unittest -v

The fake board works out its numbers from Ohm's law. These tests check
those numbers by hand, and check them against lessons/*.json.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import protocol as P
from agent.lessons import check, load_lessons, summarize
from sim.fake_board import CURRENT_LIMIT_MA, SCENARIOS, UNLISTED, FakeBoard

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
WINDOW = 20
V = P.SUPPLY_V  # 3.3 V, like the station


def window(lesson, scenario, seed=1, **board_options):
    """Readings, keeping only those taken while power was on, like the
    laptop does (a board with a current sensor and a power switch cuts
    power by itself when the current is too high)."""
    board = FakeBoard(lesson, scenario, seed=seed, **board_options)
    readings = [board.read() for _ in range(WINDOW)]
    return [r for r in readings if r["power_on"]] or readings


def ideal(lesson, scenario, **board_options):
    """One noise-free reading."""
    return FakeBoard(lesson, scenario, noise=False, **board_options).read()


class TestHello(unittest.TestCase):
    def test_default_board(self):
        h = FakeBoard().hello()
        self.assertEqual(h["type"], "hello")
        self.assertEqual(h["protocol"], 2)
        self.assertEqual(len(h["rows"]), 130)
        self.assertEqual((h["has_current"], h["has_power_switch"], h["has_screen"], h["buttons"]),
                         (True, True, True, 4))
        self.assertEqual(h["supply_v"], 3.3)  # like the station

    def test_another_supply(self):
        board = FakeBoard(1, "led_backwards", noise=False, supply_v=5)
        self.assertEqual(board.hello()["supply_v"], 5.0)
        r = board.read()
        self.assertAlmostEqual(r["supply_v"], 5.0)
        self.assertAlmostEqual(r["rows"]["T10"], 5.0)  # no current: the full supply
        r = FakeBoard(1, "correct", noise=False, supply_v=5).read()
        self.assertAlmostEqual(r["current_ma"], (5 - 2) / 220 * 1000, places=2)
        for bad in (0, -1, 31):
            with self.assertRaises(ValueError):
                FakeBoard(supply_v=bad)

    def test_any_answer_to_the_open_questions(self):
        h = FakeBoard(rows="T1-T16", has_current=False, has_power_switch=False,
                      has_screen=False, buttons=2).hello()
        self.assertEqual(h["rows"], [f"T{i}" for i in range(1, 17)])
        self.assertEqual((h["has_current"], h["has_power_switch"], h["has_screen"], h["buttons"]),
                         (False, False, False, 2))

    def test_rows_as_a_list(self):
        self.assertEqual(FakeBoard(rows=["T10", "T5", "PT+"]).hello()["rows"], ["T5", "T10", "PT+"])

    def test_bad_options(self):
        for options in ({"rows": "T0-T5"}, {"rows": ["X1"]}, {"buttons": 5}):
            with self.assertRaises(ValueError, msg=options):
                FakeBoard(**options)


class TestReading(unittest.TestCase):
    def test_fields_and_types(self):
        r = FakeBoard(seed=0).read()
        self.assertEqual(set(r), {"type", "t_ms", "power_on", "supply_v", "current_ma",
                                  "rows", "floating"})
        self.assertEqual(r["type"], "reading")
        self.assertIsInstance(r["t_ms"], int)
        self.assertIsInstance(r["current_ma"], float)
        self.assertTrue(all(isinstance(v, float) for v in r["rows"].values()))

    def test_only_measured_rows(self):
        r = FakeBoard(rows="T1-T16", seed=0).read()
        self.assertEqual(sorted(r["rows"], key=lambda n: int(n[1:])), [f"T{i}" for i in range(1, 17)])
        self.assertTrue(set(r["floating"]) <= set(r["rows"]))

    def test_current_is_null_without_a_sensor(self):
        r = FakeBoard(has_current=False, seed=0).read()
        self.assertIsNone(r["current_ma"])
        self.assertIn('"current_ma": null', json.dumps(r))

    def test_empty_rows_float(self):
        r = ideal(1, "correct")
        for name in ("T5", "T10", "T12", "PT+", "PT-"):
            self.assertNotIn(name, r["floating"])
        for name in ("T1", "T63", "B10", "PB+", "PB-"):
            self.assertIn(name, r["floating"])

    def test_time_moves_but_not_at_a_fixed_rate(self):
        board = FakeBoard(seed=3)
        times = [board.read()["t_ms"] for _ in range(30)]
        steps = {b - a for a, b in zip(times, times[1:])}
        self.assertTrue(all(s > 0 for s in steps))
        self.assertGreater(len(steps), 1)

    def test_is_one_json_line(self):
        line = json.dumps(FakeBoard(seed=0).read())
        self.assertNotIn("\n", line)

    def test_same_seed_same_readings(self):
        self.assertEqual(window(1, "correct", seed=7), window(1, "correct", seed=7))


class TestOhmsLaw(unittest.TestCase):
    """Noise-free numbers for every circuit, worked out by hand."""

    def rows(self, lesson, scenario):
        r = ideal(lesson, scenario)
        names = LESSONS[lesson]["rows"]
        return {n: (None if n in r["floating"] else r["rows"][n]) for n in names}, r

    def test_lesson_1(self):
        cases = {
            "correct": ({"T5": V, "T10": 2.0, "T12": 0.0}, (V - 2) / 220 * 1000),  # 5.9 mA
            "led_backwards": ({"T5": V, "T10": V, "T12": 0.0}, 0.0),
            "open_circuit": ({"T5": V, "T10": None, "T12": 0.0}, 0.0),
            "resistor_too_big": ({"T5": V, "T10": 2.0, "T12": 0.0}, (V - 2) / 1000 * 1000),
            "led_bypassed": ({"T5": V, "T10": 0.0, "T12": 0.0}, V / 220 * 1000),
            "resistor_150": ({"T5": V, "T10": 2.0, "T12": 0.0}, (V - 2) / 150 * 1000),
        }
        for scenario, (rows, current) in cases.items():
            with self.subTest(scenario=scenario):
                got, r = self.rows(1, scenario)
                for name, want in rows.items():
                    if want is None:
                        self.assertIsNone(got[name])
                    else:
                        self.assertAlmostEqual(got[name], want, places=2)
                self.assertAlmostEqual(r["current_ma"], current, places=2)

    def test_no_resistor_sags_the_supply(self):
        got, r = self.rows(1, "no_resistor")
        self.assertAlmostEqual(r["current_ma"], CURRENT_LIMIT_MA)
        self.assertLess(r["supply_v"], P.low_supply_v())
        self.assertAlmostEqual(got["T5"], got["T10"], places=2)

    def test_lesson_2(self):
        cases = {
            "correct": (1.65, V / 20000 * 1000),
            "bottom_1k": (V * 1 / 11, V / 11000 * 1000),
            "top_1k": (V * 10 / 11, V / 11000 * 1000),
            "bottom_4k7": (V * 4.7 / 14.7, V / 14700 * 1000),
            "bottom_missing": (V, 0.0),
            "top_missing": (0.0, 0.0),
            "middle_floating": (None, 0.165),
        }
        for scenario, (middle, current) in cases.items():
            with self.subTest(scenario=scenario):
                got, r = self.rows(2, scenario)
                self.assertAlmostEqual(got["T5"], V, places=2)
                self.assertAlmostEqual(got["T15"], 0.0, places=2)
                if middle is None:
                    self.assertIsNone(got["T10"])
                else:
                    self.assertAlmostEqual(got["T10"], middle, places=2)
                self.assertAlmostEqual(r["current_ma"], current, places=3)


class TestAgainstTheLessonFiles(unittest.TestCase):
    def test_every_mistake_has_a_circuit_and_back(self):
        for lid, lesson in LESSONS.items():
            mistake_ids = {m["id"] for m in lesson["mistakes"]}
            sim_ids = set(SCENARIOS[lid]) - {"correct"} - set(UNLISTED[lid])
            self.assertEqual(mistake_ids, sim_ids, f"lesson {lid}")

    def expected(self, lid, scenario, has_current):
        if scenario == "correct":
            return "ok"
        if scenario == "resistor_150":
            return "unknown" if has_current else "ok"
        if scenario in UNLISTED[lid]:
            return "unknown"
        if scenario == "resistor_too_big" and not has_current:
            return "ok"  # cannot be seen without a current sensor (docs/LESSONS.md)
        return scenario

    def test_every_circuit_with_noise_and_both_sensor_options(self):
        for lid, scenarios in SCENARIOS.items():
            if LESSONS[lid].get("states"):
                continue  # a lesson with steps (lesson 4): tests/test_button_lesson.py
            for scenario in scenarios:
                for has_current in (True, False):
                    for seed in range(8):
                        with self.subTest(lesson=lid, scenario=scenario,
                                          current=has_current, seed=seed):
                            s = summarize(window(lid, scenario, seed, has_current=has_current),
                                          LESSONS[lid])
                            r = check(s, LESSONS[lid])
                            got = r.get("mistake_id") or r["status"]
                            self.assertEqual(got, self.expected(lid, scenario, has_current))

    def test_calculated_current_matches_ohms_law(self):
        s = summarize(window(1, "correct", has_current=False), LESSONS[1])
        self.assertEqual(s["current_source"], "calculated")
        self.assertAlmostEqual(s["current_ma"], 5.9, delta=0.3)


class TestCommands(unittest.TestCase):
    def test_cut_and_restore(self):
        board = FakeBoard(1, "correct", seed=0)
        self.assertEqual(board.send("CUT"), {"type": "ack", "cmd": "CUT"})
        r = board.read()
        self.assertFalse(r["power_on"])
        self.assertEqual((r["current_ma"], r["supply_v"]), (0.0, 0.0))
        self.assertEqual(board.send("RESTORE")["type"], "ack")
        self.assertTrue(board.read()["power_on"])

    def test_no_power_switch(self):
        board = FakeBoard(has_power_switch=False)
        for cmd in ("CUT", "RESTORE"):
            self.assertEqual(board.send(cmd),
                             {"type": "error", "cmd": cmd, "msg": "no power switch"})
        self.assertTrue(board.read()["power_on"])

    def test_lights(self):
        board = FakeBoard()
        self.assertEqual(board.send("LED T10 red"), {"type": "ack", "cmd": "LED T10 red"})
        self.assertEqual(board.send("led b3 BLUE")["cmd"], "LED B3 blue")
        self.assertEqual(board.lights, {"T10": "red", "B3": "blue"})
        board.send("LED T10 off")
        self.assertEqual(board.lights, {"B3": "blue"})
        self.assertEqual(board.send("LED CLEAR")["type"], "ack")
        self.assertEqual(board.lights, {})

    def test_every_color(self):
        board = FakeBoard()
        for color in P.COLORS:
            self.assertEqual(board.send(f"LED T1 {color}")["type"], "ack")

    def test_bad_lights(self):
        board = FakeBoard()
        for bad in ("LED T10 purple", "LED PT+ red", "LED T64 red", "LED T10", "LED"):
            with self.subTest(cmd=bad):
                self.assertEqual(board.send(bad)["type"], "error")

    def test_screen(self):
        board = FakeBoard()
        self.assertEqual(board.send("SHOW Step 1 of 4|Red wire")["type"], "ack")
        self.assertEqual(board.screen, ["Step 1 of 4", "Red wire"])
        too_long = "SHOW " + "x" * (P.SCREEN_CHARS + 1)
        self.assertEqual(board.send(too_long)["msg"], "too long")
        self.assertEqual(board.send("SHOW a|b|c|d|e")["msg"], "too long")
        self.assertEqual(FakeBoard(has_screen=False).send("SHOW hi")["msg"], "no screen")

    def test_buttons(self):
        board = FakeBoard()
        self.assertEqual(board.events(), [])
        self.assertEqual(board.send("PRESS 3")["type"], "ack")
        self.assertEqual(board.events(), [{"type": "button", "id": 3}])
        self.assertEqual(board.events(), [])  # read once
        self.assertEqual(board.send("PRESS 5")["type"], "error")
        self.assertEqual(FakeBoard(buttons=2).send("PRESS 3")["type"], "error")

    def test_hello_command(self):
        self.assertEqual(FakeBoard().send("HELLO")["type"], "hello")

    def test_scenario_switch(self):
        board = FakeBoard(1, "correct", noise=False)
        self.assertEqual(board.send("SCENARIO 1 led_backwards")["type"], "ack")
        self.assertAlmostEqual(board.read()["rows"]["T10"], V)
        self.assertEqual(board.send("SCENARIO 9 x")["type"], "error")

    def test_bad_commands(self):
        board = FakeBoard()
        for bad in ["FOO", "CUT now", "PRESS", "PRESS x"]:
            with self.subTest(cmd=bad):
                self.assertEqual(board.send(bad)["type"], "error")

    def test_an_empty_line_is_a_keep_alive_with_no_answer(self):
        board = FakeBoard()
        for blank in ("", "   ", "\t"):
            self.assertIsNone(board.send(blank))


class TestCommandLine(unittest.TestCase):
    def run_sim(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "sim" / "fake_board.py"), *args],
            capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
        )

    def test_hello_then_readings(self):
        out = self.run_sim("--lesson", "2", "--scenario", "bottom_1k", "--rows", "T1-T16",
                           "--no-current", "--count", "3", "--interval", "0", "--seed", "1")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = [json.loads(l) for l in out.stdout.strip().splitlines()]
        self.assertEqual(lines[0]["type"], "hello")
        self.assertEqual(len(lines[0]["rows"]), 16)
        self.assertFalse(lines[0]["has_current"])
        self.assertEqual([l["type"] for l in lines[1:]], ["reading"] * 3)
        self.assertIsNone(lines[1]["current_ma"])

    def test_list(self):
        self.assertIn("led_backwards", self.run_sim("--list").stdout)

    def test_supply_option(self):
        out = self.run_sim("--count", "1", "--interval", "0", "--seed", "1")
        self.assertEqual(json.loads(out.stdout.splitlines()[0])["supply_v"], 3.3)
        out = self.run_sim("--supply", "5", "--count", "1", "--interval", "0", "--seed", "1")
        lines = [json.loads(l) for l in out.stdout.strip().splitlines()]
        self.assertEqual(lines[0]["supply_v"], 5.0)
        self.assertAlmostEqual(lines[1]["supply_v"], 5.0, delta=0.1)

    def test_bad_scenario_fails(self):
        self.assertEqual(self.run_sim("--scenario", "nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
