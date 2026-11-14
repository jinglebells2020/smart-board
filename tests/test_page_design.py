"""The friendly page: what the server adds for it (the lesson layout for
the drawn breadboard, the lesson path with stars, Zip's mood and the
celebration), and checks on the page file itself. No new rules: stars and
moods are worked out from what already happens."""

import re
import tempfile
import unittest
from pathlib import Path

from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
QUIZ = {m["id"]: m["mini_lesson"] for m in LESSONS[1]["mistakes"]}


class Case(unittest.TestCase):
    BOARD = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = FakeBoard(1, "correct", seed=3, **self.BOARD)
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel())

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, scenario):
        self.app.build(scenario)

    def check(self, text="Check my circuit"):
        return self.app.ask(text)

    def quiz(self, right):
        card = self.app.card
        ml = QUIZ[card["mistake"]]
        choice = ml["answer"] if right else (ml["answer"] + 1) % len(ml["options"])
        return self.app.quiz(card["occurrence"], choice)

    def stars(self, lesson=1):
        return next(p for p in self.app.state()["path"] if p["id"] == lesson)

    def latest(self):
        return [m for m in self.app.log if m["who"] == "tutor"][-1]


class TestBreadboardData(Case):
    def test_state_has_the_lesson_layout(self):
        s = self.app.state()
        self.assertEqual(s["lesson"]["layout"], LESSONS[1]["layout"])
        self.assertEqual(s["lesson"]["rows"], LESSONS[1]["rows"])
        self.assertIn("lights", s["board"])
        self.assertIn("step", s["board"])


class TestStars(Case):
    def test_one_path_stop_per_lesson(self):
        path = self.app.state()["path"]
        self.assertEqual([p["id"] for p in path], sorted(LESSONS))
        self.assertTrue(path[0]["current"])
        self.assertEqual([p["stars"] for p in path], [0] * len(LESSONS))

    def test_clean_pass_gets_three_stars(self):
        self.check()
        p = self.stars(1)
        self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"], p["stars"]),
                         (True, True, True, 3))
        self.assertTrue(self.stars(2)["current"])

    def test_mistake_with_quiz_right_still_three(self):
        self.build("led_backwards")
        self.check()
        self.quiz(right=True)
        self.assertEqual(self.stars(1)["stars"], 0)  # not passed yet
        self.build("correct")
        self.check("I fixed it, check again")
        self.assertEqual(self.stars(1)["stars"], 3)

    def test_quiz_wrong_or_not_answered_loses_the_third_star(self):
        for answer in (False, None):
            with self.subTest(answer=answer):
                self.setUp()
                self.build("led_backwards")
                self.check()
                if answer is not None:
                    self.quiz(right=answer)
                self.build("correct")
                self.check("I fixed it, check again")
                p = self.stars(1)
                self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"]),
                                 (True, True, False))
                self.assertEqual(p["stars"], 2)
                self.tearDown()

    def test_same_mistake_twice_loses_the_second_star(self):
        for _ in range(2):
            self.build("led_backwards")
            self.check()
            self.quiz(right=True)
        self.build("correct")
        self.check("I fixed it, check again")
        # Twice the same mistake: the lesson is practised again, but it was
        # passed, so it keeps star 1, and star 3 for the right quizzes.
        p = self.stars(1)
        self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"], p["stars"]),
                         (True, False, True, 2))

    def test_stars_come_from_the_saved_history(self):
        self.check()
        again = App(FakeBoard(1, "correct", seed=3), lesson=1,
                    history_path=Path(self.tmp.name) / "h.json", model=ScriptedTutorModel())
        self.assertEqual(next(p for p in again.state()["path"] if p["id"] == 1)["stars"], 3)


class TestZipsMood(Case):
    def test_starts_happy(self):
        self.assertEqual(self.app.state()["mood"], "happy")

    def test_mistake_is_curious_not_scary(self):
        self.build("led_backwards")
        self.check()
        self.assertEqual(self.latest()["mood"], "curious")
        self.assertEqual(self.app.state()["mood"], "curious")
        self.assertFalse(self.latest()["celebrate"])

    def test_danger_is_careful(self):
        self.build("no_resistor")
        self.check()
        self.assertEqual(self.latest()["mood"], "careful")
        self.assertEqual(self.app.state()["mood"], "careful")

    def test_unknown_mistake_and_detective_are_curious(self):
        self.build("resistor_150")
        out = self.check()
        self.assertIn("task", out["tools"])
        self.assertEqual(self.latest()["mood"], "curious")

    def test_correct_is_happy_and_celebrates_once(self):
        self.check()
        self.assertEqual(self.latest()["mood"], "happy")
        self.assertTrue(self.latest()["celebrate"])
        self.check("I need a hint")  # a hint never measures: nothing to celebrate
        self.assertFalse(self.latest()["celebrate"])

    def test_hint_button_is_curious(self):
        self.board.send("PRESS 2")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertEqual(self.latest()["mood"], "curious")

    def test_power_off_is_careful(self):
        self.board.send("PRESS 4")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertEqual(self.latest()["mood"], "careful")
        self.assertEqual(self.app.state()["mood"], "careful")

    def test_thinking_while_the_tutor_works(self):
        with self.app.agent_lock:
            self.assertEqual(self.app.mood(self.board.read(), True), "thinking")

    def test_only_the_four_moods(self):
        for scenario in ("correct", "led_backwards", "no_resistor", "resistor_150",
                         "open_circuit"):
            self.build(scenario)
            self.check()
            self.assertIn(self.app.state()["mood"], App.MOODS)
        self.assertEqual(App.MOODS, ("happy", "thinking", "curious", "careful"))


class TestNoPowerSwitchMood(Case):
    BOARD = {"has_power_switch": False}

    def test_switch_off_by_hand_is_careful(self):
        self.build("no_resistor")
        self.check()
        self.assertEqual(self.latest()["mood"], "careful")


class TestPageFile(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_zip_has_four_moods(self):
        self.assertIn('id="guide"', self.html)
        for mood in App.MOODS:
            self.assertIn(f"mood-{mood}", self.html)
            self.assertIn(f'[data-mood="{mood}"]', self.html)

    def test_drawn_breadboard_and_path(self):
        for needed in ['id="bb"', "function buildBreadboard", "L.layout", 'id="path"',
                       "state.path", "@keyframes glow"]:
            self.assertIn(needed, self.html)

    def test_teacher_view_is_closed_by_default(self):
        self.assertRegex(self.html, r'<aside[^>]*id="teacher"[^>]*\bhidden\b')
        tag = re.search(r'<aside[^>]*id="teacher".*?</aside>', self.html, re.S).group(0)
        for inside in ['id="sim"', 'id="history"', 'id="tutor-did"', 'id="boardline"',
                       'id="reset"', "Mistake history"]:
            self.assertIn(inside, tag)

    def test_celebration_is_short_and_silent(self):
        self.assertIn("function celebrate()", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertNotRegex(self.html, r"<audio|new Audio|AudioContext")

    def test_one_file_no_internet(self):
        self.assertNotRegex(self.html, r'<script[^>]+src=|<link[^>]+href=|@import|url\(\s*["\']?http')
        # Only the SVG namespace name (a label, not a download) and localhost.
        self.assertNotRegex(self.html, r"https?://(?!localhost|www\.w3\.org/2000/svg)")

    def test_no_other_brand(self):
        self.assertNotRegex(self.html.lower(), r"scratch|sprite")


if __name__ == "__main__":
    unittest.main()
