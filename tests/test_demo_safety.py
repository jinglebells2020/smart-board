"""A safe demo: when Claude fails, is slow or there is no internet, the
offline tutor answers that one message, the page says so, and the next
message tries Claude again. Safety never waits for Claude.

"Claude" here is FakeClaude: a stand-in that can fail, be slow, or work
(then it answers like the offline tutor). No API key, no internet.
"""

import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from agent.build import CLAUDE_TIMEOUT, POWER_QUESTION
from agent.deadline import ReplyTimeout, run_with_timeout
from agent.fallback import OFFLINE_NOTE, why_failed
from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
HINT = "Look at the LED's long leg"  # the first hint for "LED backwards"


class FakeClaude(BaseChatModel):
    """Stands in for Claude: fails, is slow, or answers like the offline tutor."""
    fail: bool = False
    fail_after: int = -1  # this many calls work, then every call fails (-1: never)
    sleep: float = 0.0
    calls: int = 0

    @property
    def _llm_type(self):
        return "fake-claude"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        if self.fail or 0 <= self.fail_after < self.calls:
            raise ConnectionError("no internet")
        return ScriptedTutorModel()._generate(messages)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def app(self, scenario="led_backwards", lesson=1, confirm_power=False, timeout=CLAUDE_TIMEOUT,
            **claude):
        self.board = FakeBoard(lesson, scenario, seed=1)
        self.claude = FakeClaude(**claude)
        return App(self.board, lesson=lesson, history_path=Path(self.tmp.name) / "h.json",
                   model=self.claude, confirm_power=confirm_power, log=self.lines.append,
                   timeout=timeout)


class TestOfflineFallback(Base):
    def test_no_internet_the_offline_tutor_answers_and_the_page_says_so(self):
        app = self.app(fail=True)
        out = app.ask("Check my circuit")
        self.assertIn(HINT, out["reply"])
        self.assertTrue(out["offline"])
        latest = app.state()["log"][-1]
        self.assertTrue(latest["offline"])
        self.assertEqual(app.state()["offline_note"], "Zip is in offline mode for this answer")
        self.assertEqual(len(app.session.history["mistakes"]), 1)
        self.assertEqual(self.board.lights, {"T10": "red", "T12": "red"})
        self.assertEqual(len(self.lines), 1)
        self.assertRegex(self.lines[0], r"Claude failed after \d+\.\d s \(could not reach "
                                        r"Claude.*\)\. The offline tutor answered")

    def test_the_next_message_tries_claude_again(self):
        app = self.app(fail=True)
        app.ask("Check my circuit")
        self.claude.fail = False
        self.board.send("SCENARIO 1 correct")
        before = self.claude.calls
        out = app.ask("I fixed it, check again")
        self.assertGreater(self.claude.calls, before)  # Claude was asked again
        self.assertFalse(out["offline"])
        self.assertFalse(app.state()["log"][-1]["offline"])
        self.assertRegex(self.lines[-1], r"Claude reply took \d+\.\d s$")

    def test_a_reply_that_fails_halfway_counts_nothing_twice(self):
        for works in range(1, 7):
            with self.subTest(calls_that_work=works):
                app = self.app(fail_after=works)
                out = app.ask("Check my circuit")
                self.assertIn(HINT, out["reply"])
                self.assertEqual(len(app.session.history["mistakes"]), 1)
                self.assertEqual(app.state()["mini_lesson"]["title"], "LEDs are one-way doors")
                saved = json.loads((Path(self.tmp.name) / "h.json").read_text())
                self.assertEqual(len(saved["mistakes"]), 1)
                (Path(self.tmp.name) / "h.json").unlink()

    def test_slow_claude_is_given_up_after_the_time_limit(self):
        app = self.app(sleep=1.5, timeout=0.3)
        start = time.monotonic()
        out = app.ask("Check my circuit")
        self.assertLess(time.monotonic() - start, 1.4)  # did not wait for Claude
        self.assertTrue(out["offline"])
        self.assertIn(HINT, out["reply"])
        self.assertRegex(self.lines[0], r"no answer within 0\.3 seconds")
        lights, trace = dict(self.board.lights), list(app.session.trace)
        time.sleep(2.5)  # the given-up reply wakes up: it must do nothing now
        self.assertEqual(self.board.lights, lights)
        self.assertEqual(app.session.trace, trace)
        self.assertEqual(len(app.session.history["mistakes"]), 1)

    def test_the_default_time_limit_is_25_seconds(self):
        self.assertEqual(CLAUDE_TIMEOUT, 25)
        self.assertEqual(App(FakeBoard(1, seed=1), lesson=1, model=ScriptedTutorModel(),
                             history_path=Path(self.tmp.name) / "x.json").timeout, 25)

    def test_without_a_key_nothing_is_logged_and_no_note(self):
        app = App(FakeBoard(1, "led_backwards", seed=1), lesson=1, model=ScriptedTutorModel(),
                  history_path=Path(self.tmp.name) / "h.json", log=self.lines.append)
        out = app.ask("Check my circuit")
        self.assertFalse(out["offline"])
        self.assertEqual(self.lines, [])


class TestSafetyNeverWaitsForClaude(Base):
    def test_dangerous_circuit_cuts_power_with_claude_down(self):
        for opts in ({}, {"has_current": False}):  # board cuts by itself / laptop cuts
            with self.subTest(**opts):
                self.board = FakeBoard(1, "no_resistor", seed=1, **opts)
                self.claude = FakeClaude(fail=True)
                app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "d.json",
                          model=self.claude)
                out = app.ask("Check my circuit")
                self.assertFalse(self.board.power_on)
                self.assertRegex(out["reply"], r"cut the power|switched the power off")
                self.assertTrue(out["offline"])
                (Path(self.tmp.name) / "d.json").unlink()

    def test_slow_claude_still_cuts_power(self):
        app = self.app(scenario="no_resistor", sleep=1.0, timeout=0.2)
        app.ask("Check my circuit")
        self.assertFalse(self.board.power_on)
        time.sleep(1.5)
        self.assertFalse(self.board.power_on)  # the given-up reply did not turn it on

    def test_power_only_on_when_asked_and_measured_at_once(self):
        app = self.app(scenario="no_resistor", fail=True, confirm_power=True)
        app.ask("Check my circuit")
        self.assertFalse(self.board.power_on)
        app.ask("What is a resistor?")  # not asking for power
        self.assertFalse(self.board.power_on)
        out = app.ask("I fixed it")  # still no resistor
        self.assertEqual(out["reply"], POWER_QUESTION)
        self.assertTrue(out["offline"])
        out = app.answer_power(True)
        self.assertFalse(self.board.power_on)  # measured at once, cut again
        self.assertIn("still not safe", out["reply"])
        self.board.send("SCENARIO 1 correct")
        app.ask("I fixed it")
        app.answer_power(True)
        self.assertTrue(self.board.power_on)

    def test_claude_fails_after_asking_about_power(self):
        for yes in (True, False):
            with self.subTest(yes=yes):
                (Path(self.tmp.name) / "h.json").unlink(missing_ok=True)  # a new student each time
                app = self.app(scenario="no_resistor", confirm_power=True)
                app.ask("Check my circuit")
                self.board.send("SCENARIO 1 correct")
                out = app.ask("I fixed it")
                self.assertEqual(out["reply"], POWER_QUESTION)
                self.assertFalse(out["offline"])  # Claude asked
                self.claude.fail = True
                out = app.answer_power(yes)
                self.assertTrue(out["offline"])
                self.assertEqual(self.board.power_on, yes)
                self.assertFalse(app.waiting_for_power_answer())

    def test_hint_button_never_asks_claude(self):
        app = self.app(fail=True)
        app.ask("Check my circuit")
        calls, mistakes = self.claude.calls, len(app.session.history["mistakes"])
        app.press(2)
        app.wait_for_buttons()
        self.assertEqual(self.claude.calls, calls)
        self.assertEqual(len(app.session.history["mistakes"]), mistakes)
        self.assertTrue(self.board.power_on)


class TestLooseLegWords(Base):
    def test_loose_leg_reply_says_yellow_light(self):
        app = self.app(scenario="loose_led_leg")
        reply = app.ask("Check my circuit")["reply"]
        self.assertIn("yellow light", reply)
        self.assertNotIn("red light", reply)
        self.assertEqual(self.board.lights, {"T10": "yellow"})
        screen = app.state()["board"]["screen"]
        self.assertIn("Yellow: look here.", screen)
        self.assertNotIn("Red", " ".join(screen))

    def test_other_mistakes_still_say_red_lights(self):
        reply = self.app().ask("Check my circuit")["reply"]
        self.assertIn("The red lights show where to look.", reply)


class TestHelpers(unittest.TestCase):
    def test_reasons_in_plain_words(self):
        self.assertEqual(why_failed(ReplyTimeout("x"), 25), "no answer within 25 seconds")
        self.assertIn("internet", why_failed(ConnectionError("down")))
        self.assertIn("API key", why_failed(type("AuthenticationError", (Exception,), {})("401")))

    def test_time_limit(self):
        self.assertEqual(run_with_timeout(lambda: 7, 1), 7)
        with self.assertRaises(ReplyTimeout):
            run_with_timeout(lambda: time.sleep(0.5), 0.05)
        with self.assertRaises(KeyError):
            run_with_timeout(lambda: {}["x"], 1)


class TestRestartDemo(Base):
    def test_restart_demo_starts_maya_at_lesson_one(self):
        from agent.notebook import Notebook
        board = FakeBoard(1, "led_backwards", seed=1)
        app = App(board, lesson="start", history_path=Path(self.tmp.name) / "h.json",
                  model=ScriptedTutorModel(),
                  notebook=Notebook(Path(self.tmp.name) / "nb", "Sam"),
                  demo={"student": "Maya", "lesson": 1,
                        "history": str(Path(self.tmp.name) / "maya.json")})
        app.ask("Check my circuit")
        out = app.restart_demo()
        self.assertEqual(out, {"ok": True, "student": "Maya", "lesson": 1})
        state = app.state()
        self.assertEqual(state["lesson"]["id"], 1)
        self.assertEqual(state["log"], [])
        self.assertEqual(state["history"], [])
        self.assertEqual(state["scenario"], "correct")
        self.assertEqual(app.notebook.student, "Maya")
        self.assertEqual(app.session.notebook.student, "Maya")
        self.assertTrue(board.power_on)
        self.assertEqual(state["board"]["screen"][0], "Genesis")
        self.assertIn("Lesson 1", state["board"]["screen"][1])

    def test_page_has_the_note_and_the_button(self):
        self.assertIn(f'id="offline-note" role="status" hidden>{OFFLINE_NOTE}</p>', HTML)
        self.assertIn('id="restart-demo"', HTML)
        self.assertIn(">Restart demo</button>", HTML)
        self.assertIn('post("/api/restart-demo")', HTML)
        self.assertIn('$("offline-note").hidden = busy || !(latest && latest.offline)', HTML)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDemoCommand(unittest.TestCase):
    def test_one_command_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            nb = Path(tmp) / "nb"
            nb.mkdir()
            (nb / "maya.md").write_text("# Notebook: Maya\n\n- old line\n", encoding="utf-8")
            hist = Path(tmp) / "maya.json"
            hist.write_text(json.dumps({"mistakes": [{"lesson": 1, "mistake": "led_backwards",
                                                      "time": 1}], "passes": {}, "quiz": []}))
            port = free_port()
            env = {k: v for k, v in __import__("os").environ.items() if k != "ANTHROPIC_API_KEY"}
            proc = subprocess.Popen(
                [sys.executable, "web/server.py", "--demo", "--port", str(port),
                 "--notebooks", str(nb), "--history", str(hist)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                out = ""
                deadline = time.time() + 60
                while "Demo URL" not in out and time.time() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    out += line
                self.assertIn(f"Demo URL: http://localhost:{port}", out)
                self.assertIn("student Maya, lesson 1", out)
                state = json.loads(urllib.request.urlopen(
                    f"http://localhost:{port}/api/state", timeout=10).read())
                self.assertEqual(state["lesson"]["id"], 1)
                self.assertEqual(state["history"], [])  # fresh history
                notebook = urllib.request.urlopen(
                    f"http://localhost:{port}/api/notebook", timeout=10).read().decode()
                self.assertIn("Notebook: Maya", notebook)
                self.assertNotIn("old line", notebook)  # a fresh notebook
                self.assertEqual(len(list(nb.glob("maya.*.md"))), 1)  # the old one is kept
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
