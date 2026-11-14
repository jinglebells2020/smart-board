"""Part 2: Zip asks "Turn the power back on?" before the tutor turns power
on (deepagents interrupt_on, human in the loop, with a checkpointer)."""

import tempfile
import unittest
from pathlib import Path

from agent.build import POWER_QUESTION, answer_power, ask, build_agent
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent


class PageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = FakeBoard(1, "no_resistor", seed=1)
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel(), confirm_power=True)
        self.app.ask("Check my circuit")  # too much current: power is cut
        self.assertFalse(self.board.power_on)

    def tearDown(self):
        self.tmp.cleanup()

    def texts(self):
        return [s["text"] for s in [m for m in self.app.log if m["who"] == "tutor"][-1]["steps"]]


class TestYesAndNo(PageCase):
    def test_zip_asks_before_power_comes_on(self):
        self.board.set_scenario(1, "correct")
        out = self.app.ask("I fixed it")
        self.assertTrue(out["pending"])
        self.assertEqual(out["reply"], POWER_QUESTION)
        self.assertFalse(self.board.power_on)          # nothing switched yet
        state = self.app.state()
        self.assertTrue(state["asks_power"])
        self.assertEqual(state["mood"], "curious")
        self.assertNotIn("Turned the power back on and measured it at once", self.texts())

    def test_yes_turns_power_on_and_measures_at_once(self):
        self.board.set_scenario(1, "correct")
        self.app.ask("I fixed it")
        out = self.app.answer_power(True)
        self.assertTrue(self.board.power_on)
        self.assertIn("correct", out["reply"].lower())
        self.assertFalse(self.app.state()["asks_power"])
        texts = self.texts()
        self.assertIn("Asked you: turn the power back on? You said yes", texts)
        self.assertIn("Turned the power back on and measured it at once", texts)
        self.assertLess(texts.index("Asked you: turn the power back on? You said yes"),
                        texts.index("Turned the power back on and measured it at once"))

    def test_no_keeps_power_off_and_the_tool_never_runs(self):
        self.board.set_scenario(1, "correct")
        self.app.ask("I fixed it")
        out = self.app.answer_power(False)
        self.assertFalse(self.board.power_on)
        self.assertIn("stays off", out["reply"])
        texts = self.texts()
        self.assertIn("Asked you: turn the power back on? You said no", texts)
        self.assertNotIn("Turned the power back on and measured it at once", texts)
        self.assertFalse(self.app.state()["asks_power"])

    def test_yes_into_a_still_unsafe_circuit_cuts_again(self):
        self.app.ask("I fixed it")                     # still no resistor
        out = self.app.answer_power(True)
        self.assertFalse(self.board.power_on)          # measured at once, cut again
        self.assertIn("still not safe", out["reply"])

    def test_board_button_4_counts_as_yes(self):
        self.board.set_scenario(1, "correct")
        self.app.ask("I fixed it")
        self.board.send("PRESS 4")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertTrue(self.board.power_on)
        self.assertFalse(self.app.state()["asks_power"])

    def test_check_my_circuit_while_off_also_asks(self):
        self.board.set_scenario(1, "correct")
        out = self.app.ask("Check my circuit")
        self.assertTrue(out.get("pending"))

    def test_a_new_message_instead_of_an_answer_means_no(self):
        self.board.set_scenario(1, "correct")
        self.app.ask("I fixed it")
        self.app.ask("I need a hint")
        self.assertFalse(self.board.power_on)
        self.assertIn("No", [m["text"] for m in self.app.log if m["who"] == "student"])

    def test_no_answer_waiting(self):
        self.assertIn("error", self.app.answer_power(True))


class TestOfflineModelUsesTheInterrupt(unittest.TestCase):
    def test_interrupt_and_resume_with_the_scripted_model(self):
        board = FakeBoard(1, "no_resistor", seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(board, lesson=1, history_path=Path(tmp) / "h.json")
            agent = build_agent(s, ScriptedTutorModel(), ask_before_power=True)
            history = []
            ask(agent, history, "Check my circuit")
            board.set_scenario(1, "correct")
            reply, new = ask(agent, history, "I fixed it")
            self.assertEqual(reply, POWER_QUESTION)
            self.assertIsNotNone(agent.smart_board_pending)
            calls = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
            self.assertEqual(calls[-1], "restore_power")  # paused right before it ran
            reply, _ = answer_power(agent, history, True)
            self.assertIsNone(agent.smart_board_pending)
            self.assertTrue(board.power_on)
            with self.assertRaises(ValueError):
                answer_power(agent, history, True)

    def test_page_has_the_yes_and_no_buttons(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needed in ('id="ask-power"', 'id="power-yes"', 'id="power-no"', '"/api/power-answer"',
                       "state.asks_power"):
            self.assertIn(needed, html)


if __name__ == "__main__":
    unittest.main()
