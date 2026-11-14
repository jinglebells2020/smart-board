"""Tests for web/server.py: the page and its API, with the fake board.

Starts the real server on a free port, so it also checks the HTTP part.
"""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from web.server import App, make_server

LESSONS = load_lessons()


class TestWebServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(FakeBoard(1, "correct", seed=2), lesson=1,
                       history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel())
        self.server = make_server(self.app, port=0)  # 0 = any free port
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def state(self):
        return json.loads(self.get("/api/state")[2])

    def test_page_is_served(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Mistake history", body)

    def test_state_has_lesson_and_live_reading(self):
        s = self.state()
        self.assertEqual(s["lesson"]["id"], 1)
        self.assertEqual(s["lesson"]["accept"], LESSONS[1]["accept"])
        self.assertTrue(s["sim"])
        self.assertIn("led_backwards", s["scenarios"])
        t1 = s["reading"]["t_ms"]
        self.assertGreater(self.state()["reading"]["t_ms"], t1)  # live: time moves

    def test_mistake_shows_hint_and_history(self):
        self.assertEqual(self.post("/api/build", {"scenario": "led_backwards"})["type"], "ack")
        out = self.post("/api/ask", {"text": "Check my circuit"})
        hint = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")["hint"]
        self.assertIn(hint, out["reply"])
        self.assertIn("log_mistake", out["tools"])
        s = self.state()
        self.assertEqual(s["history"][0]["mistake"], "led_backwards")
        self.assertEqual(s["history"][0]["given"], "hint")
        self.assertEqual([m["who"] for m in s["log"]], ["student", "tutor"])
        self.post("/api/ask", {"text": "check again"})
        self.assertEqual(self.state()["history"][1]["given"], "full explanation")

    def test_power_cut_shows_in_reading(self):
        self.post("/api/build", {"scenario": "no_resistor"})
        self.post("/api/ask", {"text": "check"})
        self.assertFalse(self.state()["reading"]["power_on"])

    def test_passing_moves_board_to_next_lesson(self):
        self.post("/api/ask", {"text": "check"})
        s = self.state()
        self.assertEqual(s["lesson"]["id"], 2)
        self.assertEqual(s["scenario"], "correct")
        self.assertEqual(s["passes"], {"1": 1})

    def test_reset(self):
        self.post("/api/build", {"scenario": "no_resistor"})
        self.post("/api/ask", {"text": "check"})
        self.post("/api/reset", {})
        s = self.state()
        self.assertEqual((s["history"], s["log"], s["lesson"]["id"]), ([], [], 1))
        self.assertTrue(s["reading"]["power_on"])

    def test_bad_input(self):
        self.assertEqual(self.post("/api/build", {"scenario": "nope"})["type"], "error")
        self.assertIn("error", self.post("/api/ask", {"text": "   "}))
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/nothing-here")


if __name__ == "__main__":
    unittest.main()
