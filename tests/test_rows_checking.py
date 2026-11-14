"""Tests for Part 2: lesson layouts and checking from row voltages.

Readings here are written by hand in protocol version 2 format, with the
voltages worked out with Ohm's law, so these tests do not depend on the
fake board.
"""

import unittest

from agent.lessons import calculate_current, check, clue, load_lessons, matches, summarize
from protocol import ALL_NODES, TOP_ROWS, is_node, is_row, parse_rows
from tests.test_mini_lessons import FIX_WORDS, HARD_WORDS, has_word, reading_grade

LESSONS = load_lessons()
FLOAT = None  # marks a floating (empty) row below

# Ideal row voltages for every circuit, in volts, worked out by hand.
# Lesson 1 rows: T5 (after the red wire), T10 (resistor/LED long leg),
# T12 (LED short leg / black wire). Lesson 2: T5, T10 (middle), T15.
CIRCUITS = {
    1: {
        "correct":          ({"T5": 5, "T10": 2.0, "T12": 0}, (5 - 2) / 220 * 1000, 5.0),
        "led_backwards":    ({"T5": 5, "T10": 5.0, "T12": 0}, 0.0, 5.0),
        "no_resistor":      ({"T5": 2.2, "T10": 2.2, "T12": 0}, 100.0, 2.2),
        "open_circuit":     ({"T5": 5, "T10": FLOAT, "T12": 0}, 0.0, 5.0),
        "resistor_too_big": ({"T5": 5, "T10": 2.0, "T12": 0}, (5 - 2) / 1000 * 1000, 5.0),
        "led_bypassed":     ({"T5": 5, "T10": 0.0, "T12": 0}, 5 / 220 * 1000, 5.0),
        "resistor_150":     ({"T5": 5, "T10": 2.0, "T12": 0}, (5 - 2) / 150 * 1000, 5.0),
    },
    2: {
        "correct":          ({"T5": 5, "T10": 2.5, "T15": 0}, 5 / 20000 * 1000, 5.0),
        "bottom_1k":        ({"T5": 5, "T10": 5 / 11, "T15": 0}, 5 / 11000 * 1000, 5.0),
        "middle_floating":  ({"T5": 5, "T10": FLOAT, "T15": 0}, 0.25, 5.0),
        "top_1k":           ({"T5": 5, "T10": 50 / 11, "T15": 0}, 5 / 11000 * 1000, 5.0),
        "bottom_missing":   ({"T5": 5, "T10": 5.0, "T15": 0}, 0.0, 5.0),
        "top_missing":      ({"T5": 5, "T10": 0.0, "T15": 0}, 0.0, 5.0),
        "bottom_4k7":       ({"T5": 5, "T10": 5 * 4.7 / 14.7, "T15": 0}, 5 / 14700 * 1000, 5.0),
    },
}
UNLISTED = {"resistor_150", "bottom_4k7"}


def reading(rows, current, supply, board_rows=None, with_sensor=True, t_ms=0, power_on=True):
    """One version 2 reading. Floating rows read a meaningless 1.7 V."""
    board_rows = board_rows if board_rows is not None else ALL_NODES
    out_rows, floating = {}, []
    for name in board_rows:
        if name in rows:
            if rows[name] is FLOAT:
                out_rows[name] = 1.7
                floating.append(name)
            else:
                out_rows[name] = float(rows[name])
        else:
            out_rows[name] = 0.4
            floating.append(name)  # empty row
    return {"type": "reading", "t_ms": t_ms, "power_on": power_on, "supply_v": supply,
            "current_ma": float(current) if with_sensor else None,
            "rows": out_rows, "floating": floating}


def measure(lesson_id, circuit, with_sensor=True, board_rows=None, n=5):
    rows, current, supply = CIRCUITS[lesson_id][circuit]
    readings = [reading(rows, current, supply, board_rows, with_sensor, t_ms=i * 137)
                for i in range(n)]
    return summarize(readings, LESSONS[lesson_id])


def outcome(result):
    return result.get("mistake_id") or result["status"]


class TestLayouts(unittest.TestCase):
    def test_every_lesson_has_a_layout_in_build_order(self):
        for lid, lesson in LESSONS.items():
            if lesson.get("kind") == "explore":
                continue  # lesson 0: nothing to build
            with self.subTest(lesson=lid):
                self.assertGreaterEqual(len(lesson["layout"]), 3)
                for part in lesson["layout"]:
                    self.assertTrue(part["part"])
                    self.assertEqual(len(part["legs"]), 2)
                    for leg in part["legs"]:
                        self.assertTrue(is_node(leg["row"]), leg)
                # first part brings power in, last part goes to ground
                self.assertIn("PT+", [l["row"] for l in lesson["layout"][0]["legs"]])
                self.assertIn("PT-", [l["row"] for l in lesson["layout"][-1]["legs"]])

    def test_lesson_rows_come_from_the_layout(self):
        for lid, lesson in LESSONS.items():
            layout_rows = {l["row"] for p in lesson["layout"] for l in p["legs"] if is_row(l["row"])}
            self.assertEqual(set(lesson["rows"]), layout_rows)
            self.assertEqual(set(lesson["expected"]["rows"]), set(lesson["rows"]))

    def test_rows_fit_a_16_row_board(self):
        first16 = set(parse_rows("T1-T16"))
        for lesson in LESSONS.values():
            self.assertTrue(set(lesson["rows"]) <= first16, lesson["rows"])

    def test_rules_only_use_lesson_rows(self):
        for lid, lesson in LESSONS.items():
            rules = [lesson["accept"]] + [m["detect"] for m in lesson["mistakes"]]
            for rule in rules:
                used = set(rule.get("rows", {})) | set(rule.get("floating", [])) | set(rule.get("driven", []))
                self.assertTrue(used <= set(lesson["rows"]), (lid, used))
            for m in lesson["mistakes"]:
                self.assertTrue(set(m["rows"]) <= set(lesson["rows"]), m["id"])
                # A loose leg is in whichever row flickers, so its rows are found
                # while measuring; every other mistake names its rows.
                self.assertTrue(m["rows"] or m["detect"].get("any_flickering"), m["id"])
            for r in lesson["current_from"]:
                self.assertTrue(set(r["rows"]) <= set(lesson["rows"]))
                self.assertGreater(r["ohms"], 0)


class TestCalculatedCurrent(unittest.TestCase):
    def test_lesson_1_from_the_220_ohm_resistor(self):
        s = measure(1, "correct", with_sensor=False)
        self.assertEqual(s["current_source"], "calculated")
        self.assertAlmostEqual(s["current_ma"], (5 - 2) / 220 * 1000, places=2)  # 13.6 mA

    def test_lesson_2_both_resistors_agree(self):
        s = measure(2, "correct", with_sensor=False)
        self.assertEqual(s["current_source"], "calculated")
        self.assertAlmostEqual(s["current_ma"], 0.25, places=3)

    def test_disagreeing_resistors_give_no_guess(self):
        for circuit in ("top_1k", "bottom_1k", "top_missing", "bottom_missing", "bottom_4k7"):
            with self.subTest(circuit=circuit):
                s = measure(2, circuit, with_sensor=False)
                self.assertIsNone(s["current_ma"])
                self.assertEqual(s["current_source"], "unknown")
                self.assertIn("do not agree", s["current_note"])

    def test_floating_end_means_no_current(self):
        value, _ = calculate_current({"T5": 5.0, "T10": 1.7}, ["T10"],
                                     [{"rows": ["T5", "T10"], "ohms": 220}])
        self.assertEqual(value, 0.0)

    def test_sensor_wins_when_there_is_one(self):
        s = measure(1, "resistor_too_big", with_sensor=True)
        self.assertEqual(s["current_source"], "measured")
        self.assertAlmostEqual(s["current_ma"], 3.0, places=2)

    def test_needs_both_rows(self):
        value, note = calculate_current({"T5": 5.0}, [], [{"rows": ["T5", "T10"], "ohms": 220}])
        self.assertIsNone(value)
        self.assertIn("T10", note)


class TestDetection(unittest.TestCase):
    def expected(self, lid, circuit, with_sensor):
        if circuit == "correct":
            return "ok"
        if circuit == "resistor_150":
            return "unknown" if with_sensor else "ok"  # rows alone look right
        if circuit in UNLISTED:
            return "unknown"  # 4.7k: the middle row is off, rows show it
        if circuit == "resistor_too_big" and not with_sensor:
            return "ok"  # needs a current sensor, see docs/LESSONS.md
        return circuit

    def test_every_circuit_with_and_without_a_current_sensor(self):
        for lid, circuits in CIRCUITS.items():
            for circuit in circuits:
                for with_sensor in (True, False):
                    with self.subTest(lesson=lid, circuit=circuit, sensor=with_sensor):
                        result = check(measure(lid, circuit, with_sensor), LESSONS[lid])
                        self.assertEqual(outcome(result), self.expected(lid, circuit, with_sensor))

    def test_every_mistake_matches_only_itself(self):
        for lid, circuits in CIRCUITS.items():
            for circuit in circuits:
                for with_sensor in (True, False):
                    s = measure(lid, circuit, with_sensor)
                    hits = [m["id"] for m in LESSONS[lid]["mistakes"]
                            if matches(s, m["detect"])]
                    if circuit in UNLISTED or circuit == "correct":
                        self.assertEqual(hits, [], (lid, circuit, with_sensor))
                    elif circuit == "resistor_too_big" and not with_sensor:
                        self.assertEqual(hits, [], (lid, circuit))
                    else:
                        self.assertEqual(hits, [circuit], (lid, circuit, with_sensor))

    def test_mistake_names_its_rows(self):
        result = check(measure(1, "led_backwards"), LESSONS[1])
        self.assertEqual(result["rows"], ["T10", "T12"])

    def test_no_resistor_without_a_sensor_is_still_dangerous(self):
        result = check(measure(1, "no_resistor", with_sensor=False), LESSONS[1])
        self.assertEqual((result["mistake_id"], result["action"]), ("no_resistor", "cut_power"))

    def test_not_checked_without_a_sensor_is_said_plainly(self):
        result = check(measure(1, "correct", with_sensor=False), LESSONS[1])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["not_checked"], ["resistor_too_big"])
        self.assertNotIn("not_checked", check(measure(1, "correct"), LESSONS[1]))

    def test_led_backwards_still_names_two_causes(self):
        m = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")
        self.assertIn("may", m["description"])
        self.assertIn("cannot tell", m["explain"])

    def test_power_off(self):
        rows, cur, sup = CIRCUITS[1]["correct"]
        s = summarize([reading(rows, cur, sup, power_on=False)], LESSONS[1])
        self.assertEqual(check(s, LESSONS[1])["status"], "power_off")

    def test_floating_needs_most_readings(self):
        rows, cur, sup = CIRCUITS[1]["correct"]
        readings = [reading(rows, cur, sup) for _ in range(10)]
        for r in readings[:3]:
            r["floating"].append("T10")  # 3 of 10 readings only
        s = summarize(readings, LESSONS[1])
        self.assertNotIn("T10", s["floating"])


class TestRowsTheBoardDoesNotMeasure(unittest.TestCase):
    def test_a_16_row_board_can_check_both_lessons(self):
        board = parse_rows("T1-T16")
        for lid in [lid for lid, l in LESSONS.items() if l.get("kind") != "explore"]:  # lesson 0 has no circuit
            for with_sensor in (True, False):
                s = measure(lid, "correct", with_sensor, board_rows=board)
                self.assertEqual(s["missing_rows"], [])
                self.assertEqual(check(s, LESSONS[lid])["status"], "ok")

    def test_missing_rows_are_said_clearly_not_guessed(self):
        board = parse_rows("T1-T8")
        s = measure(1, "correct", board_rows=board)
        self.assertEqual(s["missing_rows"], ["T10", "T12"])
        result = check(s, LESSONS[1])
        self.assertEqual(result["status"], "missing_rows")
        self.assertIn("T10", result["message"])
        self.assertIn("cannot check", result["message"])
        self.assertNotIn("mistake_id", result)

    def test_missing_row_for_lesson_2_only(self):
        board = parse_rows("T1-T12")  # lesson 2 needs T15
        self.assertEqual(check(measure(1, "correct", board_rows=board), LESSONS[1])["status"], "ok")
        self.assertEqual(measure(2, "correct", board_rows=board)["missing_rows"], ["T15"])


class TestRowClue(unittest.TestCase):
    def test_unlisted_4k7_gets_a_row_clue_without_a_sensor_too(self):
        for with_sensor in (True, False):
            result = check(measure(2, "bottom_4k7", with_sensor), LESSONS[2])
            self.assertEqual(result["status"], "unknown")
            d = result["clue"]["details"][0]
            self.assertEqual((d["row"], d["direction"]), ("T10", "lower"))
            self.assertIn("Row 10 has less voltage than it should.", result["clue"]["text"])

    def test_measured_current_clue_is_kept(self):
        result = check(measure(1, "resistor_150"), LESSONS[1])
        self.assertEqual(result["clue"]["details"][0]["direction"], "a little higher")

    def test_empty_row_clue(self):
        s = measure(1, "correct")
        s["floating"] = ["T12"]
        c = clue(s, LESSONS[1])
        self.assertEqual(c["details"][0]["direction"], "empty")
        self.assertIn("Row 12 has nothing connected to it", c["text"])

    def test_clue_text_is_plain_and_never_a_fix(self):
        texts = set()
        for lid, lesson in LESSONS.items():
            names = lesson["rows"]
            for v in (0.0, 0.1, 1.0, 2.0, 3.5, 4.95):
                for name in names:
                    for floating in ([], [name]):
                        s = measure(lid, "correct")
                        s["rows"] = dict(s["rows"], **{name: v})
                        s["floating"] = floating
                        for supply in (5.0, 4.2):
                            s["supply_v"] = supply
                            c = clue(s, lesson)
                            texts.add(c["text"])
                            texts.update(d["meaning"] for d in c["details"])
        self.assertGreater(len(texts), 10)
        for text in texts:
            with self.subTest(text=text):
                self.assertLessEqual(reading_grade(text), 7.0)
                for word in FIX_WORDS + HARD_WORDS:
                    self.assertFalse(has_word(text, word), word)


if __name__ == "__main__":
    unittest.main()
