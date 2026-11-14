"""Part 1: "How Zip worked" lists the real steps of the latest reply."""

import tempfile
import unittest
from pathlib import Path

from agent.mock_model import ScriptedTutorModel
from agent.notebook import Notebook
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.board = FakeBoard(1, "correct", seed=1)
        self.app = App(self.board, lesson=1, history_path=self.dir / "h.json",
                       model=ScriptedTutorModel(), notebook=Notebook(self.dir / "nb", "Maya"))

    def tearDown(self):
        self.tmp.cleanup()

    def latest(self):
        return [m for m in self.app.log if m["who"] == "tutor"][-1]


class TestSteps(Case):
    def test_unknown_mistake_shows_memory_detective_and_notebook(self):
        self.board.set_scenario(1, "resistor_150")
        out = self.app.ask("Check my circuit")
        steps = self.latest()["steps"]
        texts = [s["text"] for s in steps]
        self.assertEqual(texts[0], "Read Maya's notebook")
        for needed in ("Measured the rows", "Asked the detective",
                       "Compared the rows with the plan", "Wrote in Maya's notebook"):
            self.assertIn(needed, texts)
        self.assertLess(texts.index("Asked the detective"),
                        texts.index("Compared the rows with the plan"))
        # The detective's own step is marked as his.
        self.assertEqual(next(s for s in steps if s["text"].startswith("Compared"))["who"],
                         "detective")
        # Zip's own steps are exactly the tool calls of this reply, in order.
        tutor = [s for s in self.app.session.trace if s["who"] == "tutor"]
        self.assertEqual([s["tool"] for s in tutor], out["tools"])

    def test_mistake_steps_name_the_rows_and_mistake(self):
        self.board.set_scenario(1, "led_backwards")
        self.app.ask("Check my circuit")
        texts = [s["text"] for s in self.latest()["steps"]]
        self.assertIn("Saved the mistake (led backwards)", texts)
        self.assertIn("Lit rows 10 and 12 red", texts)
        self.assertNotIn("Asked the detective", texts)  # only for unknown mistakes

    def test_each_reply_starts_a_new_list(self):
        self.board.set_scenario(1, "resistor_150")
        self.app.ask("Check my circuit")
        self.app.ask("I need a hint")
        texts = [s["text"] for s in self.latest()["steps"]]
        self.assertEqual(texts, ["Read Maya's notebook", "Gave the next level of help"])

    def test_board_button_without_the_agent_has_no_steps(self):
        self.board.send("PRESS 3")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertNotIn("steps", self.latest())

    def test_page_shows_the_panel_with_badges(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needed in ("How Zip worked", 'id="how"', "function renderHow", "latest.steps",
                       '"lesson writer"', "detective"):
            self.assertIn(needed, html)


if __name__ == "__main__":
    unittest.main()
