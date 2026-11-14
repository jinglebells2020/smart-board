"""Tests for the new mistakes and for the clue on unknown mistakes.

The goal: the board always explains. A listed mistake gets its hint and
mini-lesson; anything else gets a clue from the numbers, never just
"check your wiring", and never a guessed fix. Run from the repository:

    python -m unittest -v
"""

import re
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.lessons import SAFETY_MAX_MA, check, clue, load_lessons, matches
from agent.mock_model import ScriptedTutorModel
from agent.prompts import SYSTEM_PROMPT
from agent.session import Session
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from tests.test_mini_lessons import FIX_WORDS, HARD_WORDS, has_word, reading_grade

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
NEW_MISTAKES = {1: ["resistor_too_big", "led_bypassed"],
                2: ["top_1k", "bottom_missing", "top_missing"]}


def summary(current_ma, probe1_v, spread=0.02, supply_v=5.0, power_on=True):
    return {"current_ma": current_ma, "probe1_v": probe1_v, "probe1_v_spread": spread,
            "supply_v": supply_v, "power_on": power_on}


def detected(lesson_id, s):
    r = check(s, LESSONS[lesson_id])
    return r.get("mistake_id") or r["status"]


class FixedBoard(FakeBoard):
    """A board that reads fixed numbers while power is on: a circuit the
    lessons do not know (it obeys CUT, RESTORE and LED like the real one)."""

    def __init__(self, current_ma, probe1_v, supply_v=5.0):
        super().__init__(1, "correct", seed=0)
        self.fixed = (current_ma, probe1_v, supply_v)

    def read(self):
        r = super().read()
        if r["power_on"]:
            r["current_ma"], r["voltages"]["probe1"], r["supply_v"] = self.fixed
        return r


class TestNewMistakesInTheLessons(unittest.TestCase):
    def test_new_mistakes_exist_with_all_fields(self):
        for lid, ids in NEW_MISTAKES.items():
            have = {m["id"]: m for m in LESSONS[lid]["mistakes"]}
            for mid in ids:
                with self.subTest(lesson=lid, mistake=mid):
                    m = have[mid]
                    for field in ("description", "detect", "action", "hint",
                                  "explain", "mini_lesson"):
                        self.assertIn(field, m)
                    self.assertEqual(m["action"], "hint")  # none of these is dangerous

    def test_simulator_has_every_mistake(self):
        for lid, ids in NEW_MISTAKES.items():
            for mid in ids:
                self.assertIn(mid, SCENARIOS[lid])


class TestOhmsLaw(unittest.TestCase):
    """The simulator's numbers for the new circuits, worked out by hand."""

    def measure(self, lesson, scenario):
        session = Session(FakeBoard(lesson, scenario, seed=4, noise=False), lesson=lesson)
        s, result, safety_cut = session.measure()
        return s, result, safety_cut

    def test_resistor_too_big(self):
        s, result, _ = self.measure(1, "resistor_too_big")
        self.assertAlmostEqual(s["current_ma"], (5 - 2) / 1000 * 1000, places=2)  # 3.0 mA
        self.assertAlmostEqual(s["probe1_v"], 2.0, places=2)
        self.assertEqual(result["mistake_id"], "resistor_too_big")

    def test_led_bypassed_is_not_cut(self):
        s, result, safety_cut = self.measure(1, "led_bypassed")
        self.assertAlmostEqual(s["current_ma"], 5 / 220 * 1000, places=1)  # 22.7 mA
        self.assertAlmostEqual(s["probe1_v"], 0.0, places=2)
        self.assertEqual(result["mistake_id"], "led_bypassed")
        self.assertFalse(safety_cut)  # not dangerous: below the 30 mA safety cut
        self.assertTrue(s["power_on"])

    def test_top_1k(self):
        s, result, _ = self.measure(2, "top_1k")
        self.assertAlmostEqual(s["probe1_v"], 5 * 10 / 11, places=2)  # 4.55 V
        self.assertAlmostEqual(s["current_ma"], 5 / 11000 * 1000, places=3)
        self.assertEqual(result["mistake_id"], "top_1k")

    def test_bottom_missing(self):
        s, result, _ = self.measure(2, "bottom_missing")
        self.assertEqual((s["current_ma"], s["probe1_v"], s["probe1_v_spread"]), (0.0, 5.0, 0.0))
        self.assertEqual(result["mistake_id"], "bottom_missing")

    def test_top_missing(self):
        s, result, _ = self.measure(2, "top_missing")
        self.assertEqual((s["current_ma"], s["probe1_v"], s["probe1_v_spread"]), (0.0, 0.0, 0.0))
        self.assertEqual(result["mistake_id"], "top_missing")


class TestDetection(unittest.TestCase):
    def test_a_10k_resistor_is_also_too_big(self):
        # (5 - 2) / 10000 = 0.3 mA, LED row still about 2 V.
        for current in (0.3, 0.3 * 0.9, 0.3 * 1.1):
            self.assertEqual(detected(1, summary(current, 2.0)), "resistor_too_big")
        self.assertEqual(detected(1, summary(0.64, 2.0)), "resistor_too_big")  # 4.7k

    def test_every_circuit_matches_only_itself_over_many_noise_patterns(self):
        # The agent measures 10 readings at a time; try 60 noise patterns each.
        for lid, scenarios in SCENARIOS.items():
            for scenario in scenarios:
                for seed in range(60):
                    session = Session(FakeBoard(lid, scenario, seed=seed), lesson=lid)
                    s, result, _ = session.measure()
                    got = [m["id"] for m in LESSONS[lid]["mistakes"]
                           if matches(s, m["detect"])]
                    if scenario == "correct":
                        self.assertEqual(got, [], (lid, scenario, seed, s))
                        self.assertEqual(result["status"], "ok")
                    elif scenario in UNLISTED[lid]:
                        self.assertEqual(got, [], (lid, scenario, seed, s))
                        self.assertEqual(result["status"], "unknown")
                    else:
                        self.assertEqual(got, [scenario], (lid, scenario, seed, s))

    def test_led_bypassed_stays_below_the_safety_cut(self):
        m = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_bypassed")
        self.assertLessEqual(m["detect"]["current_ma"]["max"], SAFETY_MAX_MA)
        self.assertGreater(SAFETY_MAX_MA, 5 / 220 * 1000 * 1.05)


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
    # Unknown circuits: numbers that match no listed mistake and no pass.
    UNKNOWN = [
        (1, summary(0.03, 2.0)),        # 100k resistor: almost no current, LED row 2 V
        (1, summary(9.7, 2.0)),         # just under the 10 to 17 mA range
        (1, summary(3.0, 3.0)),         # little current, row too high
        (1, summary(25.0, 2.0)),        # a lot of current, but the LED row is not 0 V
        (1, summary(0.5, 3.2)),
        (1, summary(9.8, 2.0, supply_v=4.2)),
        (2, summary(0.25, 1.9)),
        (2, summary(0.25, 3.4)),
        (2, summary(0.20, 2.1)),
        (2, summary(0.30, 4.0)),
        (2, summary(0.25, 1.0, spread=0.02)),
    ]

    def test_unknown_circuits_get_a_clue(self):
        for lid, s in self.UNKNOWN:
            with self.subTest(lesson=lid, summary=s):
                result = check(s, LESSONS[lid])
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(result["clue"]["text"])
                self.assertTrue(result["clue"]["details"])

    def test_clue_names_the_number_and_direction(self):
        c = clue(summary(9.7, 2.0), LESSONS[1])
        d = c["details"][0]
        self.assertEqual((d["number"], d["direction"]), ("current", "a little lower"))
        self.assertEqual((d["measured"], d["expected"]), (9.7, 13.6))

        c = clue(summary(0.25, 3.4), LESSONS[2])
        d = c["details"][0]
        self.assertEqual((d["number"], d["direction"]),
                         ("voltage at the measuring row", "higher"))

    def test_the_example_from_the_task(self):
        c = clue(summary(3.0, 3.0), LESSONS[1])
        self.assertIn("The current is much lower than it should be. Something is holding "
                      "it back more than the 220 ohm resistor would.", c["text"])

    def test_almost_no_current_and_almost_full_voltage(self):
        c = clue(summary(0.03, 2.0), LESSONS[1])
        self.assertEqual(c["details"][0]["direction"], "almost none")
        c = clue(summary(0.25, 4.95), LESSONS[2])
        self.assertEqual(c["details"][0]["direction"], "almost all")

    def test_jumping_reading(self):
        c = clue(summary(13.0, 2.5, spread=2.5), LESSONS[1])
        self.assertEqual(c["details"][0]["direction"], "jumping")
        self.assertIn("jumping", c["text"])

    def test_low_power_voltage(self):
        c = clue(summary(9.8, 2.0, supply_v=4.2), LESSONS[1])
        self.assertIn("power voltage", [d["number"] for d in c["details"]])

    def test_clue_is_plain_short_and_never_a_fix(self):
        # Sweep many numbers for both lessons and read every clue.
        texts = set()
        for lid, currents, volts in [(1, [0.0, 0.03, 2, 5, 9.7, 18, 25, 40], [0, 1.0, 2.0, 3.0, 4.95]),
                                     (2, [0.0, 0.1, 0.2, 0.25, 0.3, 0.6, 1.0], [0, 0.5, 1.9, 2.5, 3.4, 4.95])]:
            for cur in currents:
                for v in volts:
                    for spread in (0.02, 2.0):
                        for supply in (5.0, 4.2):
                            c = clue(summary(cur, v, spread, supply), LESSONS[lid])
                            texts.add((lid, c["text"]))
                            for d in c["details"]:
                                texts.add((lid, d["meaning"]))
        self.assertGreater(len(texts), 10)
        for lid, text in texts:
            with self.subTest(text=text):
                self.assertLessEqual(reading_grade(text), 7.0)
                self.assertLessEqual(len(re.split(r"(?<=[.!?])\s+", text)), 4)
                for word in FIX_WORDS:
                    self.assertFalse(has_word(text, word), f"fix word {word!r}")
                for word in HARD_WORDS:
                    self.assertFalse(has_word(text, word), f"hard word {word!r}")
                # It may compare with the lesson's own correct parts ("the 220
                # ohm resistor"), but never names a different part to swap in.
                rest = text.replace(LESSONS[lid]["resistor_words"], "")
                self.assertNotRegex(rest, r"\b\d+\s*k\b|\bohm|brown|orange|\bred\b")

    def test_known_mistakes_have_no_clue(self):
        s = summary(0.0, 5.0)  # LED backwards readings
        self.assertNotIn("clue", check(s, LESSONS[1]))


class TestTutorSaysTheClue(unittest.TestCase):
    def test_prompt_asks_for_the_clue(self):
        self.assertIn("clue", SYSTEM_PROMPT)
        self.assertNotIn("check their wiring", SYSTEM_PROMPT)
        self.assertIn("Never guess a specific", SYSTEM_PROMPT)

    def test_offline_tutor_says_the_clue(self):
        for lid, current, volts in [(1, 0.03, 2.0), (1, 9.7, 2.0), (2, 0.25, 3.4)]:
            with self.subTest(lesson=lid, current=current, volts=volts):
                board = FixedBoard(current, volts)
                session = Session(board, lesson=lid)
                agent = build_agent(session, ScriptedTutorModel())
                reply, new = ask(agent, [], "Check my circuit")
                expected = clue(session.measure()[0], LESSONS[lid])["text"]
                self.assertTrue(reply.startswith("This is not a mistake I know yet"), reply)
                self.assertIn(expected, reply)
                self.assertNotIn("check your wiring", reply.lower())
                tools = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
                self.assertNotIn("log_mistake", tools)   # nothing to log: no known mistake
                self.assertNotIn("next_lesson", tools)
                self.assertTrue(board.power_on)


class TestUnlistedCircuitsInTheSimulator(unittest.TestCase):
    """The practice-mode circuits that are on no list: the tutor explains
    them with a clue, and no mini-lesson card is shown."""

    def test_tutor_gives_the_clue_for_each(self):
        for lid, names in UNLISTED.items():
            for name in names:
                with self.subTest(lesson=lid, circuit=name):
                    board = FakeBoard(lid, name, seed=2)
                    session = Session(board, lesson=lid)
                    agent = build_agent(session, ScriptedTutorModel())
                    reply, _ = ask(agent, [], "Check my circuit")
                    self.assertTrue(reply.startswith("This is not a mistake I know yet"))
                    self.assertIn("than it should be", reply)
                    self.assertNotIn("check your wiring", reply.lower())
                    self.assertIsNone(session.last_logged)  # nothing logged, no card
                    self.assertTrue(board.power_on)

    def test_150_ohm_clue_says_current_a_little_higher(self):
        session = Session(FakeBoard(1, "resistor_150", seed=3), lesson=1)
        _, result, _ = session.measure()
        self.assertEqual(result["clue"]["details"][0]["direction"], "a little higher")


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
