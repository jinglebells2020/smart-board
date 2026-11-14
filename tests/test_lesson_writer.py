"""Part 3: the lesson_writer subagent, its grader (RubricMiddleware) and the
plain Python check. All offline, with the scripted writer and grader."""

import tempfile
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.lesson_writer import (ALT_LESSONS, BAD_LESSON, CRITERIA, RUBRIC,
                                 ScriptedLessonWriterModel, check_lesson, validate_lesson)
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.notebook import Notebook
from agent.session import Session
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
BUILT_IN = {m["id"]: m["mini_lesson"] for l in LESSONS.values() for m in l["mistakes"]}


class TestTheRules(unittest.TestCase):
    def test_rubric_has_the_four_rules(self):
        self.assertEqual(len(CRITERIA), 4)
        for words in ("3 short sentences", "6th grader", "never says what to change",
                      "Exactly 3 options"):
            self.assertIn(words, RUBRIC)

    def test_every_offline_lesson_is_valid_and_new(self):
        for mistake, lesson in ALT_LESSONS.items():
            self.assertTrue(validate_lesson(lesson)[0], mistake)
            self.assertNotEqual(lesson["question"], BUILT_IN[mistake]["question"])
            self.assertNotIn(lesson["comparison"].split()[-1], BUILT_IN[mistake]["idea"].lower())
        self.assertEqual(set(ALT_LESSONS), set(BUILT_IN))  # one for every mistake

    def test_each_rule_catches_its_mistake(self):
        good = ALT_LESSONS["no_resistor"]
        cases = {
            CRITERIA[0]: dict(good, idea="One. Two. Three. Four."),
            CRITERIA[1]: dict(good, idea="Current leaves the anode first."),
            CRITERIA[2]: dict(good, idea="Flip the LED and it works."),
            CRITERIA[3]: dict(good, options=["a", "b", "c", "d"]),
        }
        for criterion, lesson in cases.items():
            failed = [name for name, ok, _ in check_lesson(lesson) if not ok]
            self.assertEqual(failed, [criterion])
        self.assertFalse(validate_lesson(dict(good, answer=3))[0])
        self.assertFalse(validate_lesson(dict(good, options=["a", "a", "b"]))[0])
        self.assertFalse(validate_lesson({k: v for k, v in good.items() if k != "why"})[0])
        self.assertFalse(validate_lesson(good, used_comparisons=["a faucet"])[0])
        self.assertFalse(validate_lesson(None)[0])

    def test_the_bad_lesson_breaks_rules(self):
        self.assertFalse(validate_lesson(BAD_LESSON)[0])


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.board = FakeBoard(1, "led_backwards", seed=1)
        self.app = App(self.board, lesson=1, history_path=self.dir / "h.json",
                       model=ScriptedTutorModel(), notebook=Notebook(self.dir / "nb", "Maya"))

    def tearDown(self):
        self.app.wait_for_buttons()
        self.tmp.cleanup()

    def bad_writer(self):
        self.app.agent = build_agent(self.app.session, ScriptedTutorModel(),
                                     writer_model=ScriptedLessonWriterModel(mode="bad"))

    def steps(self):
        return [m for m in self.app.log if m["who"] == "tutor"][-1]["steps"]


class TestSameMistakeTwice(Case):
    def test_first_time_uses_the_built_in_lesson(self):
        out = self.app.ask("Check my circuit")
        self.assertNotIn("task", out["tools"])
        self.assertFalse(self.app.state()["mini_lesson"]["written_by_zip"])

    def test_valid_lesson_is_shown_with_the_tag(self):
        self.app.ask("Check my circuit")
        out = self.app.ask("Check my circuit")
        self.assertIn("task", out["tools"])
        card = self.app.state()["mini_lesson"]
        self.assertTrue(card["written_by_zip"])
        self.assertEqual(card["title"], ALT_LESSONS["led_backwards"]["title"])
        self.assertEqual(card["question"], ALT_LESSONS["led_backwards"]["question"])
        self.assertIn("new mini-lesson", out["reply"])
        # The writer and the grader show in "How Zip worked", with badges.
        who = {(s["who"], s["text"]) for s in self.steps()}
        self.assertIn(("tutor", "Asked the lesson writer"), who)
        self.assertIn(("lesson writer", "Read the old mini-lesson and the comparisons already used"), who)
        self.assertIn(("grader", "Graded the new lesson: it passes every rule"), who)
        # The notebook says which comparison was used.
        self.assertIn('using the comparison "a bike tire valve"', self.app.notebook.text())

    def test_quiz_on_the_new_card_uses_its_own_answer(self):
        self.app.ask("Check my circuit")
        self.app.ask("Check my circuit")
        card = self.app.state()["mini_lesson"]
        right = ALT_LESSONS["led_backwards"]["answer"]
        out = self.app.quiz(card["occurrence"], right, written_by_zip=True)
        self.assertTrue(out["correct"])
        again = self.app.quiz(card["occurrence"], (right + 1) % 3, written_by_zip=True)
        self.assertTrue(again["already_answered"])
        self.assertTrue(self.app.state()["mini_lesson"]["answered"]["correct"])

    def test_invalid_lesson_keeps_the_built_in_one(self):
        self.bad_writer()
        self.app.ask("Check my circuit")
        out = self.app.ask("Check my circuit")
        self.assertIn("task", out["tools"])
        card = self.app.state()["mini_lesson"]
        self.assertFalse(card["written_by_zip"])
        self.assertEqual(card["title"], BUILT_IN["led_backwards"]["title"])
        self.assertNotIn("new mini-lesson", out["reply"])
        self.assertEqual(self.app.session.history["ai_lessons"], {})
        # The grader asked for a revision, then gave up.
        grades = [s["text"] for s in self.steps() if s["who"] == "grader"]
        self.assertEqual(grades, ["Graded the new lesson: it needs another try",
                                  "Graded the new lesson: it still breaks a rule"])
        self.assertNotIn("using the comparison", self.app.notebook.text())

    def test_a_comparison_is_never_used_twice(self):
        for _ in range(3):  # third time: the writer repeats its comparison
            self.app.ask("Check my circuit")
        self.assertEqual(len(self.app.session.history["ai_lessons"]), 1)
        self.assertFalse(self.app.state()["mini_lesson"]["written_by_zip"])


class TestQuizWrong(Case):
    def answer_wrong(self):
        self.app.ask("Check my circuit")
        card = self.app.state()["mini_lesson"]
        wrong = (BUILT_IN["led_backwards"]["answer"] + 1) % 3
        out = self.app.quiz(card["occurrence"], wrong)
        self.app.wait_for_buttons()  # Zip asks the lesson writer in the background
        return out, card

    def test_wrong_answer_brings_a_new_card(self):
        out, old = self.answer_wrong()
        self.assertFalse(out["correct"])
        card = self.app.state()["mini_lesson"]
        self.assertTrue(card["written_by_zip"])
        self.assertEqual(card["occurrence"], old["occurrence"])
        self.assertIsNone(card["answered"])  # a fresh question
        replies = [m["text"] for m in self.app.log if m["who"] == "tutor"]
        self.assertIn("another way", replies[-1])
        students = [m["text"] for m in self.app.log if m["who"] == "student"]
        self.assertIn("I got the quiz wrong. Can you explain it another way?", students)

    def test_wrong_answer_with_an_invalid_lesson_keeps_the_card(self):
        self.bad_writer()
        self.answer_wrong()
        card = self.app.state()["mini_lesson"]
        self.assertFalse(card["written_by_zip"])
        self.assertIsNotNone(card["answered"])  # the built-in card, already answered

    def test_right_answer_needs_no_new_lesson(self):
        self.app.ask("Check my circuit")
        card = self.app.state()["mini_lesson"]
        self.app.quiz(card["occurrence"], BUILT_IN["led_backwards"]["answer"])
        self.app.wait_for_buttons()
        self.assertEqual(len([m for m in self.app.log if m["who"] == "tutor"]), 1)

    def test_the_new_card_counts_for_the_third_star(self):
        self.answer_wrong()
        card = self.app.state()["mini_lesson"]
        self.app.quiz(card["occurrence"], ALT_LESSONS["led_backwards"]["answer"],
                      written_by_zip=True)
        self.board.set_scenario(1, "correct")
        self.app.ask("I fixed it, check again")
        lesson1 = next(p for p in self.app.state()["path"] if p["id"] == 1)
        self.assertTrue(lesson1["quiz_right"])


class TestWithTheAgentDirectly(unittest.TestCase):
    def test_offline_writer_and_grader_run_inside_the_subagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(FakeBoard(2, "top_1k", seed=1), lesson=2,
                        history_path=Path(tmp) / "h.json")
            agent = build_agent(s, ScriptedTutorModel())
            history = []
            ask(agent, history, "Check my circuit")
            reply, new = ask(agent, history, "Check my circuit")
            calls = [c for m in new for c in (getattr(m, "tool_calls", None) or [])]
            task = next(c for c in calls if c["name"] == "task")
            self.assertEqual(task["args"]["subagent_type"], "lesson_writer")
            self.assertEqual(s.ai_lesson(1)["comparison"], ALT_LESSONS["top_1k"]["comparison"])

    def test_page_has_the_tag(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needed in ("Written by Zip for you", "card.written_by_zip", "written_by_zip:"):
            self.assertIn(needed, html)


if __name__ == "__main__":
    unittest.main()
