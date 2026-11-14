"""Lessons before the start lesson are skipped, never passed.

Starting with --lesson 1 (or --demo) skips lesson 0. After the last
lesson Zip offers it, and only says "You passed every lesson" when every
lesson really was passed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from sim.fake_board import FakeBoard
from web.server import App

LESSONS = load_lessons()


class TestSkippedLessons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "h.json"

    def tearDown(self):
        self.tmp.cleanup()

    def pass_lesson(self, session):
        session.passed_current = True
        return session.next_lesson()

    def test_skipped_lesson_zero_is_offered_after_the_last_lesson(self):
        s = Session(FakeBoard(1), lesson=1, history_path=self.path)
        self.assertEqual(self.pass_lesson(s)["lesson"], 2)
        out = self.pass_lesson(s)
        self.assertEqual(out["lesson"], 0)
        self.assertIn("You skipped lesson 0, How a breadboard connects", out["reason"])
        self.assertNotIn("passed every lesson", out["reason"])
        self.assertEqual(s.lesson_id, 0)
        self.assertNotIn("0", json.loads(self.path.read_text())["passes"])  # never marked passed

    def test_starting_at_lesson_two_offers_one_then_zero(self):
        s = Session(FakeBoard(2), lesson=2, history_path=self.path)
        self.assertEqual(self.pass_lesson(s)["lesson"], 1)  # a building lesson first
        self.assertEqual(self.pass_lesson(s)["lesson"], 0)
        out = self.pass_lesson(s)
        self.assertIsNone(out["lesson"])
        self.assertIn("You passed every lesson", out["reason"])

    def test_never_claims_every_lesson_while_one_is_not_passed(self):
        for start in LESSONS:
            with self.subTest(start=start):
                self.path.unlink(missing_ok=True)
                s = Session(FakeBoard(1), lesson=start, history_path=self.path)
                for _ in range(len(LESSONS) + 2):
                    out = self.pass_lesson(s)
                    passes = s.history["passes"]
                    if "every lesson" in out["reason"]:
                        self.assertEqual({str(l) for l in LESSONS} - set(passes), set())
                        break
                else:
                    self.fail("never finished")

    def test_the_tutor_says_it_after_lesson_two(self):
        board = FakeBoard(1, "correct", seed=1)
        app = App(board, lesson=1, history_path=self.path, model=ScriptedTutorModel())
        app.ask("Check my circuit")                   # lesson 1
        reply = app.ask("Check my circuit")["reply"]  # lesson 2
        self.assertIn("You skipped lesson 0", reply)
        self.assertNotIn("every lesson", reply)
        self.assertEqual(app.state()["lesson"]["id"], 0)
        self.assertEqual(board.lesson, 0)  # the pretend board shows lesson 0
        path = {p["id"]: p["passed"] for p in app.state()["path"]}
        self.assertEqual(path, {0: False, 1: True, 2: True})
        app.quiz("intro", LESSONS[0]["intro_lesson"]["answer"])
        reply = app.ask("Check my circuit")["reply"]
        self.assertIn("You passed every lesson", reply)
        self.assertTrue(all(p["passed"] for p in app.state()["path"]))

    def test_new_student_still_starts_at_zero_and_is_never_sent_back(self):
        s = Session(FakeBoard(1, "correct", seed=1), lesson="start", history_path=self.path)
        agent = build_agent(s, ScriptedTutorModel())
        self.assertEqual(s.lesson_id, 0)
        s.record_quiz(0, "intro", 0, "intro")
        ask(agent, [], "Check my circuit")
        self.assertEqual(s.lesson_id, 1)
        self.assertEqual(self.pass_lesson(s)["lesson"], 2)
        self.assertIn("every lesson", self.pass_lesson(s)["reason"])


if __name__ == "__main__":
    unittest.main()
