"""Tests for Part 4: the pretend board on the web page.

Starts the real web server with the fake board and uses the page's API,
the same way the page does.
"""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import protocol as P
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from tests.test_mini_lessons import HARD_WORDS, has_word, visible_page_text
from web.server import App, make_server

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()


class ServerCase(unittest.TestCase):
    BOARD = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = FakeBoard(1, "correct", seed=2, **self.BOARD)
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel())
        self.server = make_server(self.app, port=0)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def state(self):
        with urllib.request.urlopen(self.url + "/api/state", timeout=10) as r:
            return json.loads(r.read())

    def press(self, button):
        out = self.post("/api/press", {"id": button})
        self.wait_idle()
        return out

    def wait_idle(self):
        """Board buttons that ask the tutor run in the background."""
        self.app.wait_for_buttons()


class TestPretendBoardState(ServerCase):
    def test_board_view(self):
        b = self.state()["board"]
        self.assertTrue(b["pretend"])
        self.assertEqual(b["capabilities"]["rows"], 130)
        self.assertEqual([r["name"] for r in b["rows"]], LESSONS[1]["rows"])
        for row in b["rows"]:
            self.assertTrue(row["measured"])
            self.assertIsInstance(row["volts"], float)
            self.assertEqual(row["light"], "off")
        self.assertEqual(b["screen_size"], [P.SCREEN_LINES, P.SCREEN_CHARS])
        self.assertEqual(b["buttons"], {str(i): P.BUTTON_LABELS[i] for i in range(1, 5)})
        self.assertEqual(b["colors"], P.COLORS)
        self.assertEqual(b["screen"][0], "Genesis")  # welcome text names the app
        self.assertIn("Lesson 1", b["screen"][1])
        self.assertEqual(b["missing_rows"], [])

    def test_current_is_measured(self):
        shown = self.state()["shown"]
        self.assertEqual(shown["current_source"], "measured")
        self.assertAlmostEqual(shown["current_ma"], 13.6, delta=0.5)


class TestButtons(ServerCase):
    def test_next_step_lights_blue_and_names_the_part(self):
        layout = LESSONS[1]["layout"]
        for i, part in enumerate(layout):
            with self.subTest(step=i + 1):
                self.assertEqual(self.press(3)["handled"], [3])
                s = self.state()
                rows = [l["row"] for l in part["legs"] if P.is_row(l["row"])]
                self.assertEqual(s["board"]["lights"], {r: "blue" for r in rows})
                self.assertEqual(s["board"]["screen"][1], part["part"])
                self.assertEqual(s["board"]["step"], i + 1)
                self.assertIn(f"Step {i + 1} of {len(layout)}: {part['part']}",
                              s["log"][-1]["text"])
                lit = {r["name"]: r["light"] for r in s["board"]["rows"]}
                for r in rows:
                    self.assertEqual(lit[r], "blue")

    def test_check_button_runs_the_tutor(self):
        self.post("/api/build", {"scenario": "led_backwards"})
        self.press(1)
        s = self.state()
        hint = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")["hint"]
        self.assertIn(hint, s["log"][-1]["text"])
        self.assertEqual(s["board"]["lights"], {"T10": "red", "T12": "red"})
        self.assertIsNotNone(s["mini_lesson"])  # card and quiz still there
        self.assertEqual(s["history"][-1]["mistake"], "led_backwards")

    def test_hint_button(self):
        # No mistake yet: where the first part goes. Nothing is logged.
        self.post("/api/build", {"scenario": "open_circuit"})
        self.press(2)
        s = self.state()
        self.assertIn("Red wire", s["log"][-1]["text"])
        self.assertEqual(s["history"], [])
        # After a mistake: the full explanation, still logged only once.
        self.press(1)
        self.press(2)
        s = self.state()
        explain = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "open_circuit")["explain"]
        self.assertEqual(s["log"][-1]["text"], explain)
        self.assertEqual(len(s["history"]), 1)

    def test_power_button_into_an_unsafe_circuit(self):
        self.post("/api/build", {"scenario": "no_resistor"})
        self.press(1)
        self.press(4)
        s = self.state()
        self.assertFalse(s["reading"]["power_on"])
        self.assertIn("still not safe", s["log"][-1]["text"])

    def test_power_button_with_a_switch(self):
        self.press(4)
        self.assertFalse(self.state()["reading"]["power_on"])
        self.press(4)
        self.assertTrue(self.state()["reading"]["power_on"])

    def test_bad_presses(self):
        self.assertIn("error", self.post("/api/press", {"id": 7}))
        self.assertIn("error", self.post("/api/press", {"id": "1"}))
        self.assertIn("error", self.post("/api/press", {}))


class TestNoSwitchNoSensorSixteenRows(ServerCase):
    BOARD = {"has_current": False, "has_power_switch": False, "rows": "T1-T16"}

    def test_state(self):
        s = self.state()
        self.assertEqual(s["board"]["capabilities"],
                         {"rows": 16, "has_current": False, "has_power_switch": False,
                          "has_screen": True, "buttons": 4})
        self.assertEqual(s["shown"]["current_source"], "calculated")

    def test_danger_on_the_page_and_the_board(self):
        self.post("/api/build", {"scenario": "no_resistor"})
        self.press(1)
        s = self.state()
        self.assertTrue(s["log"][-1]["text"].startswith("Switch the power off by hand"))
        self.assertIn("by hand", " ".join(s["board"]["screen"]))
        self.assertTrue(s["reading"]["power_on"])

    def test_power_button_says_it_cannot(self):
        self.press(4)
        s = self.state()
        self.assertIn("cannot switch the power", s["log"][-1]["text"])
        self.assertIn("No power switch.", s["board"]["screen"])

    def test_unknown_current_shows_none(self):
        self.post("/api/ask", {"text": "check"})  # pass lesson 1
        self.assertEqual(self.state()["lesson"]["id"], 2)
        self.post("/api/build", {"scenario": "top_1k"})  # the two resistors disagree
        shown = self.state()["shown"]
        self.assertIsNone(shown["current_ma"])
        self.assertEqual(shown["current_source"], "unknown")


class TestMissingRowsOnThePage(ServerCase):
    BOARD = {"rows": "T1-T8"}

    def test_says_which_rows(self):
        b = self.state()["board"]
        self.assertEqual(b["missing_rows"], ["T10", "T12"])
        measured = {r["name"]: r["measured"] for r in b["rows"]}
        self.assertEqual(measured, {"T5": True, "T10": False, "T12": False})


class TestNoScreen(ServerCase):
    BOARD = {"has_screen": False}

    def test_works_without_a_screen(self):
        self.press(3)
        s = self.state()
        self.assertEqual(s["board"]["screen"], [])
        self.assertTrue(s["board"]["lights"])  # lights still work


class TestRealBoardHasNoPretendPanel(unittest.TestCase):
    def test_not_pretend(self):
        class OtherBoard:  # any protocol 2 board that is not the fake one
            def __init__(self):
                self.inner = FakeBoard(1, "correct", seed=1)

            def hello(self):
                return self.inner.hello()

            def read(self):
                return self.inner.read()

            def send(self, line):
                return self.inner.send(line)

            def events(self):
                return self.inner.events()

        with tempfile.TemporaryDirectory() as d:
            app = App(OtherBoard(), lesson=1, history_path=Path(d) / "h.json",
                      model=ScriptedTutorModel())
            s = app.state()
            self.assertFalse(s["board"]["pretend"])
            self.assertEqual(len(s["board"]["rows"]), 3)  # rows still shown
            self.assertIn("error", app.press(1))


class TestPage(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_has_the_pretend_board(self):
        for needed in ['id="tile-board"', 'id="screen"', 'id="board-buttons"', 'id="tile-rows"',
                       '"/api/press"', "Pretend board", "The plan: where each part goes",
                       "b.pretend", "screen_size"]:
            self.assertIn(needed, self.html)

    def test_keeps_card_quiz_and_history(self):
        for needed in ['id="mini"', 'id="mini-options"', '"/api/quiz"', "Mistake history",
                       'id="mini-close"']:
            self.assertIn(needed, self.html)

    def test_plain_words(self):
        text = visible_page_text()
        self.assertIn("Pretend board", text)
        for word in HARD_WORDS:
            self.assertFalse(has_word(text, word), word)

    def test_every_tool_has_plain_words(self):
        from agent.session import Session
        from agent.tools import make_tools
        import re
        block = re.search(r"const TOOL_WORDS = \{(.*?)\};", self.html, re.S).group(1)
        words = dict(re.findall(r'(\w+): "([^"]+)"', block))
        for f in make_tools(Session(FakeBoard())):
            self.assertIn(f.__name__, words)


if __name__ == "__main__":
    unittest.main()
