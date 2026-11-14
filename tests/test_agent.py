"""Tests for agent/: the deep agent, its tools, and the lesson rules.

They use the offline ScriptedTutorModel and sim/fake_board.py, so no
API key, internet or hardware is needed. Run from the repository folder:

    python -m unittest -v
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.build import ask, build_agent, make_model
from agent.lessons import SAFETY_MAX_MA, load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from agent.tools import make_tools
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard

LESSONS = load_lessons()


def setup(lesson=1, scenario="correct", history_path=None):
    board = FakeBoard(lesson, scenario, seed=3)
    session = Session(board, lesson=lesson, history_path=history_path)
    agent = build_agent(session, ScriptedTutorModel())
    return board, session, agent


def tool_calls(messages):
    """[(tool name, args)] in the order the agent called them."""
    return [(c["name"], c["args"]) for m in messages
            for c in (getattr(m, "tool_calls", None) or [])]


def mistake(lesson_id, mistake_id):
    return next(m for m in LESSONS[lesson_id]["mistakes"] if m["id"] == mistake_id)


class TestModelSwitch(unittest.TestCase):
    def test_no_key_uses_mock(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            model, real = make_model()
        self.assertFalse(real)
        self.assertIsInstance(model, ScriptedTutorModel)

    def test_key_uses_claude(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-not-real"}, clear=True):
            model, real = make_model()
        self.assertTrue(real)
        self.assertEqual(model, "anthropic:claude-opus-5-5")

    def test_model_can_be_changed(self):
        env = {"ANTHROPIC_API_KEY": "test-not-real", "SMART_BOARD_MODEL": "claude-sonnet-5-5"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(make_model()[0], "anthropic:claude-sonnet-5-5")

    def test_agent_gets_the_claude_md_tools(self):
        names = {f.__name__ for f in make_tools(Session(FakeBoard()))}
        for required in ["read_voltages", "read_current", "cut_power",
                         "light_row_led", "log_mistake", "next_lesson"]:
            self.assertIn(required, names)


class TestLessonFiles(unittest.TestCase):
    def test_every_mistake_has_hint_and_explain(self):
        for lesson in LESSONS.values():
            for m in lesson["mistakes"]:
                self.assertTrue(m["hint"] and m["explain"], m["id"])
                self.assertNotEqual(m["hint"], m["explain"], m["id"])


class TestEveryLessonAndMistake(unittest.TestCase):
    """Run the whole deep agent once for every circuit the sim can build."""

    def test_correct_circuits(self):
        for lesson_id in LESSONS:
            with self.subTest(lesson=lesson_id):
                board, session, agent = setup(lesson_id, "correct")
                reply, new = ask(agent, [], "Can you check my circuit?")
                names = [n for n, _ in tool_calls(new)]
                self.assertIn("next_lesson", names)
                self.assertNotIn("log_mistake", names)
                self.assertIn("correct", reply)
                self.assertEqual(session.history["mistakes"], [])
                self.assertTrue(board.power_on)

    def test_each_mistake(self):
        for lesson_id in LESSONS:
            for scenario in SCENARIOS[lesson_id]:
                if scenario == "correct" or scenario in UNLISTED[lesson_id]:
                    continue  # unlisted circuits get a clue: test_unknown_mistakes.py
                with self.subTest(lesson=lesson_id, mistake=scenario):
                    board, session, agent = setup(lesson_id, scenario)
                    reply, new = ask(agent, [], "Can you check my circuit?")
                    calls = tool_calls(new)
                    m = mistake(lesson_id, scenario)

                    self.assertIn(("log_mistake", {"mistake_id": scenario}), calls)
                    self.assertNotIn("next_lesson", [n for n, _ in calls])
                    self.assertEqual(session.history["mistakes"][-1]["mistake"], scenario)
                    # First try: hint only, never the full explanation.
                    self.assertIn(m["hint"], reply)
                    self.assertNotIn(m["explain"], reply)

                    if m["action"] == "cut_power":
                        self.assertIn("cut_power", [n for n, _ in calls])
                        self.assertFalse(board.power_on)
                    else:
                        self.assertTrue(board.power_on)

                    if m.get("show_row"):
                        row = LESSONS[lesson_id]["probe1_row"]
                        self.assertIn(("light_row_led", {"row": row}), calls)
                        self.assertEqual(board.led_row, row)
                    else:
                        self.assertIsNone(board.led_row)


class TestHintsFirst(unittest.TestCase):
    def test_second_time_explains(self):
        for lesson_id in LESSONS:
            for m in LESSONS[lesson_id]["mistakes"]:
                with self.subTest(lesson=lesson_id, mistake=m["id"]):
                    board, session, agent = setup(lesson_id, m["id"])
                    history = []
                    first, _ = ask(agent, history, "check please")
                    if not board.power_on:
                        board.send("RESTORE")  # student "tries again" the same way
                    second, _ = ask(agent, history, "I think I fixed it, check again")
                    self.assertIn(m["hint"], first)
                    self.assertNotIn(m["explain"], first)
                    self.assertIn(m["explain"], second)


class TestSafety(unittest.TestCase):
    def test_power_cut_without_any_ai(self):
        # Call the tool directly: the safety net does not depend on the model.
        board = FakeBoard(1, "no_resistor", seed=0)
        tools = {f.__name__: f for f in make_tools(Session(board, lesson=1))}
        out = json.loads(tools["read_current"]())
        self.assertTrue(out["safety_cut"])
        self.assertFalse(out["power_on"])
        self.assertFalse(board.power_on)
        self.assertGreater(out["current_ma"], SAFETY_MAX_MA)
        self.assertEqual(out["lesson_check"]["mistake_id"], "no_resistor")

    def test_restore_after_fix(self):
        board, session, agent = setup(1, "no_resistor")
        history = []
        ask(agent, history, "check")
        self.assertFalse(board.power_on)
        board.send("SCENARIO 1 correct")  # student adds the resistor
        reply, new = ask(agent, history, "I added the resistor")
        self.assertIn("restore_power", [n for n, _ in tool_calls(new)])
        self.assertTrue(board.power_on)
        self.assertIn("correct", reply)

    def test_restore_into_same_mistake_cuts_again(self):
        board, session, agent = setup(1, "no_resistor")
        history = []
        ask(agent, history, "check")
        ask(agent, history, "fixed it")  # but they did not
        self.assertFalse(board.power_on)


class TestNextLesson(unittest.TestCase):
    def pass_lesson(self, session, lesson_id):
        session.board.set_scenario(lesson_id, "correct")
        session.measure()
        return session.next_lesson()

    def test_cannot_skip_before_passing(self):
        session = Session(FakeBoard(1, "led_backwards"), lesson=1)
        session.measure()
        self.assertEqual(session.next_lesson()["lesson"], 1)

    def test_clean_pass_moves_on(self):
        session = Session(FakeBoard(1, "correct"), lesson=1)
        self.assertEqual(self.pass_lesson(session, 1)["lesson"], 2)

    def test_repeated_mistake_repeats_lesson_once(self):
        session = Session(FakeBoard(1, "correct"), lesson=1)
        session.log_mistake("led_backwards")
        session.log_mistake("led_backwards")
        first = self.pass_lesson(session, 1)
        self.assertEqual(first["lesson"], 1)
        self.assertEqual(first["repeated"], ["led_backwards"])
        # The reason is shown to students: plain words, no code names.
        self.assertNotIn("led_backwards", first["reason"])
        self.assertIn("practice", first["reason"])
        self.assertEqual(self.pass_lesson(session, 1)["lesson"], 2)

    def test_one_mistake_does_not_repeat(self):
        session = Session(FakeBoard(1, "correct"), lesson=1)
        session.log_mistake("open_circuit")
        self.assertEqual(self.pass_lesson(session, 1)["lesson"], 2)

    def test_all_passed_reviews_most_mistakes(self):
        session = Session(FakeBoard(1, "correct"), lesson=1)
        session.log_mistake("open_circuit")
        self.pass_lesson(session, 1)
        session.log_mistake("bottom_1k")
        session.log_mistake("middle_floating")
        result = self.pass_lesson(session, 2)
        self.assertEqual(result["lesson"], 2)
        self.assertIn("most mistakes", result["reason"])

    def test_all_passed_no_mistakes_is_done(self):
        session = Session(FakeBoard(1, "correct"), lesson=1)
        self.pass_lesson(session, 1)
        self.assertIsNone(self.pass_lesson(session, 2)["lesson"])

    def test_agent_switches_lesson(self):
        board, session, agent = setup(1, "correct")
        ask(agent, [], "check")
        self.assertEqual(session.lesson_id, 2)


class TestHistory(unittest.TestCase):
    def test_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            _, session, agent = setup(1, "led_backwards", history_path=path)
            ask(agent, [], "check")
            self.assertTrue(path.exists())
            again = Session(FakeBoard(), history_path=path)
            self.assertEqual(again.times_seen(1, "led_backwards"), 1)
            # A new session remembers: second time explains straight away.
            self.assertEqual(again.log_mistake("led_backwards")["level"], "explain")

    def test_unknown_mistake_is_rejected(self):
        session = Session(FakeBoard(), lesson=1)
        self.assertIn("error", session.log_mistake("bottom_1k"))  # lesson 2 mistake
        self.assertEqual(session.history["mistakes"], [])


if __name__ == "__main__":
    unittest.main()
