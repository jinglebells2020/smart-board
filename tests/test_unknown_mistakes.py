"""Tests for the mistakes added in pull request #3 and for the clue on
unknown mistakes, now with row voltages (protocol version 2).

The goal: the board always explains. A listed mistake gets its hint and
mini-lesson; anything else gets a clue from the numbers, never just
"check your wiring", and never a guessed fix. Run from the repository:

    python -m unittest -v
"""

import re
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.lessons import SAFETY_MAX_MA, check, clue, load_lessons, summarize
from agent.mock_model import ScriptedTutorModel
from agent.prompts import SYSTEM_PROMPT
from agent.session import Session
from protocol import SUPPLY_V as V
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from tests.test_mini_lessons import FIX_WORDS, HARD_WORDS, has_word, reading_grade
from tests.test_rows_checking import reading

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
NEW_MISTAKES = {1: ["resistor_too_big", "led_bypassed"],
                2: ["top_1k", "bottom_missing", "top_missing"]}


def summary(lesson_id, rows, current=None, supply=V, floating=()):
    """A summary for hand-made row voltages (current None = no sensor)."""
    rows = dict(rows)
    for name in floating:
        rows[name] = None
    r = reading(rows, current if current is not None else 0.0, supply,
                with_sensor=current is not None)
    return summarize([r], LESSONS[lesson_id])


class TestNewMistakesInTheLessons(unittest.TestCase):
    def test_new_mistakes_exist_with_all_fields(self):
        for lid, ids in NEW_MISTAKES.items():
            have = {m["id"]: m for m in LESSONS[lid]["mistakes"]}
            for mid in ids:
                with self.subTest(lesson=lid, mistake=mid):
                    m = have[mid]
                    for field in ("description", "rows", "detect", "action", "hint",
                                  "explain", "mini_lesson"):
                        self.assertIn(field, m)
                    self.assertEqual(m["action"], "hint")  # none of these is dangerous
                    self.assertIn(mid, SCENARIOS[lid])


class TestDetection(unittest.TestCase):
    def test_a_10k_resistor_is_also_too_big(self):
        # (3.3 - 2) / 10000 = 0.13 mA with a current sensor; row 10 still 2 V.
        for current in (0.13, 0.5, 1.3):
            s = summary(1, {"T5": V, "T10": 2.0, "T12": 0}, current=current)
            self.assertEqual(check(s, LESSONS[1]).get("mistake_id"), "resistor_too_big")

    def test_led_bypassed_is_not_cut(self):
        board = FakeBoard(1, "led_bypassed", seed=4)
        s, result, safety_cut = Session(board, lesson=1).measure()
        self.assertEqual(result["mistake_id"], "led_bypassed")
        self.assertFalse(safety_cut)
        self.assertTrue(board.power_on)
        self.assertLess(V / 220 * 1000, SAFETY_MAX_MA)  # 15 mA is below the cut

    def test_led_bypassed_without_a_sensor(self):
        # Calculated: (3.3 - 0) / 220 = 15 mA, so it is still found.
        s = summary(1, {"T5": V, "T10": 0.0, "T12": 0})
        self.assertAlmostEqual(s["current_ma"], 15.0, places=1)
        self.assertEqual(check(s, LESSONS[1]).get("mistake_id"), "led_bypassed")


class TestBottom1kQuestion(unittest.TestCase):
    def test_question_does_not_give_away_which_resistor_is_wrong(self):
        q = next(m for m in LESSONS[2]["mistakes"] if m["id"] == "bottom_1k")["mini_lesson"]
        text = " ".join([q["question"], *q["options"]]).lower()
        for giveaway in ("1k", "10k", "top", "bottom"):
            self.assertNotIn(giveaway, text)

    def test_no_question_names_resistor_values(self):
        for lid, lesson in LESSONS.items():
            for m in lesson["mistakes"]:
                ml = m["mini_lesson"]
                text = " ".join([ml["question"], *ml["options"]])
                with self.subTest(lesson=lid, mistake=m["id"]):
                    self.assertNotRegex(text, r"\b\d+(\.\d+)?\s*k\b|\bohm")


class TestClue(unittest.TestCase):
    def test_the_example_from_the_task(self):
        # 1.3 mA measured and row 10 too high: on no list, so a clue.
        s = summary(1, {"T5": V, "T10": 2.7, "T12": 0}, current=1.3)
        result = check(s, LESSONS[1])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("The current is much lower than it should be. Something is holding "
                      "it back more than the 220 ohm resistor would.", result["clue"]["text"])

    def test_clue_names_the_row_and_direction(self):
        s = summary(2, {"T5": V, "T10": 2.3, "T15": 0})
        d = check(s, LESSONS[2])["clue"]["details"][0]
        self.assertEqual((d["row"], d["direction"], d["expected"]), ("T10", "higher", 1.65))

    def test_almost_none_and_almost_all(self):
        c = clue(summary(1, {"T5": 0.05, "T10": 2.0, "T12": 0}), LESSONS[1])
        self.assertIn("almost none", [d["direction"] for d in c["details"]])
        c = clue(summary(2, {"T5": V, "T10": 3.25, "T15": 0}), LESSONS[2])
        self.assertEqual(c["details"][0]["direction"], "almost all")

    def test_low_power_voltage(self):
        c = clue(summary(1, {"T5": 2.6, "T10": 2.0, "T12": 0}, supply=2.6), LESSONS[1])
        self.assertIn("power voltage", [d["number"] for d in c["details"]])
        self.assertIn("lower than 3.3 volts", c["text"])

    def test_calculated_current_is_not_used_as_a_clue(self):
        # Without a sensor the current comes from these same rows: no extra clue.
        c = clue(summary(1, {"T5": V, "T10": 2.7, "T12": 0}), LESSONS[1])
        self.assertNotIn("current", [d["number"] for d in c["details"]])

    def test_clue_is_plain_short_and_never_a_fix(self):
        texts = set()
        for lid in LESSONS:
            names = LESSONS[lid]["rows"]
            for v in (0.0, 1.0, 2.3, 3.25):
                for current in (None, 0.03, 4.0, 11.0, 0.6):
                    rows = {n: v if n == names[1] else (V if n == names[0] else 0) for n in names}
                    c = clue(summary(lid, rows, current=current), LESSONS[lid])
                    texts.add((lid, c["text"]))
                    texts.update((lid, d["meaning"]) for d in c["details"])
        for lid, text in texts:
            with self.subTest(text=text):
                self.assertLessEqual(reading_grade(text), 7.0)
                self.assertLessEqual(len(re.split(r"(?<=[.!?])\s+", text)), 4)
                for word in FIX_WORDS + HARD_WORDS:
                    self.assertFalse(has_word(text, word), word)
                rest = text.replace(LESSONS[lid]["resistor_words"], "")
                self.assertNotRegex(rest, r"\b\d+\s*k\b|\bohm|brown|orange|\bred\b")


class TestTutorSaysTheClue(unittest.TestCase):
    def test_prompt_asks_for_the_clue(self):
        self.assertIn("clue", SYSTEM_PROMPT)
        self.assertNotIn("check their wiring", SYSTEM_PROMPT)
        self.assertIn("Never guess a specific", SYSTEM_PROMPT)

    def test_unlisted_circuits_get_a_clue(self):
        cases = [(1, "resistor_150", True), (2, "bottom_4k7", True), (2, "bottom_4k7", False)]
        for lid, name, has_current in cases:
            with self.subTest(lesson=lid, circuit=name, current=has_current):
                board = FakeBoard(lid, name, seed=2, has_current=has_current)
                session = Session(board, lesson=lid)
                agent = build_agent(session, ScriptedTutorModel())
                reply, new = ask(agent, [], "Check my circuit")
                self.assertTrue(reply.startswith("This is not a mistake I know yet"), reply)
                self.assertIn("than it should", reply)
                self.assertNotIn("check your wiring", reply.lower())
                self.assertIsNone(session.last_logged)  # nothing logged, no card
                tools = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
                self.assertNotIn("log_mistake", tools)
                self.assertTrue(board.power_on)

    def test_clue_rows_light_yellow(self):
        board = FakeBoard(2, "bottom_4k7", seed=2)
        agent = build_agent(Session(board, lesson=2), ScriptedTutorModel())
        ask(agent, [], "Check my circuit")
        self.assertEqual(board.lights, {"T10": "yellow"})  # yellow = careful


class TestPageLabels(unittest.TestCase):
    def test_every_simulator_circuit_has_a_plain_button_label(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        block = re.search(r"const SCENARIO_NAMES = \{(.*?)\};", html, re.S).group(1)
        labels = dict(re.findall(r'(\w+): "([^"]+)"', block))
        for lid, scenarios in SCENARIOS.items():
            for scenario in scenarios:
                with self.subTest(scenario=scenario):
                    self.assertIn(scenario, labels)
                    for word in HARD_WORDS:
                        self.assertFalse(has_word(labels[scenario], word))


if __name__ == "__main__":
    unittest.main()
