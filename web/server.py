"""Local web server for web/index.html.

    python web/server.py            then open http://localhost:8000

The page cannot talk to the board or the agent by itself, so this
server sits in between. It uses only Python's built-in http.server.

    GET  /            the page
    GET  /api/state   lesson, latest reading, mistake history (polled ~4x a second)
    POST /api/ask     {"text": "..."}      -> the tutor's reply
    POST /api/build   {"scenario": "..."}  -> fake board only: change the circuit
    POST /api/reset   start again with a new student
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so "python web/server.py" can import agent and sim

from agent.build import ask, build_agent, make_model  # noqa: E402
from agent.session import Session  # noqa: E402
from sim.fake_board import SCENARIOS, FakeBoard  # noqa: E402

PAGE = Path(__file__).resolve().parent / "index.html"


class LockedBoard:
    """Lets the page and the agent share one board safely."""

    def __init__(self, board):
        self.inner = board
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return self.inner.read()

    def send(self, line):
        with self.lock:
            return self.inner.send(line)


class App:
    def __init__(self, board, lesson=1, history_path=None, model=None):
        self.is_sim = isinstance(board, FakeBoard)
        self.board = LockedBoard(board)
        self.start_lesson = lesson
        self.history_path = history_path
        if model is None:
            model, is_real = make_model()
        else:
            is_real = isinstance(model, str)
        self.model = model
        self.model_name = model if is_real else "offline practice tutor"
        self.agent_lock = threading.Lock()  # one question at a time
        self._new_session(lesson)

    def _new_session(self, lesson):
        self.session = Session(self.board, lesson=lesson, history_path=self.history_path)
        self.agent = build_agent(self.session, self.model)
        self.chat = []  # agent message history
        self.log = []   # what the page shows: [{"who", "text", "time"}]

    def reset(self):
        with self.agent_lock:
            if self.history_path and Path(self.history_path).exists():
                Path(self.history_path).unlink()
            self.board.send("RESTORE")
            if self.is_sim:
                self.board.send(f"SCENARIO {self.start_lesson} correct")
                self.board.inner.led_row = None
            self._new_session(self.start_lesson)
        return {"ok": True}

    def state(self):
        s = self.session
        lesson = s.lesson
        reading = self.board.read()
        counts = {}
        history = []
        for m in s.history["mistakes"]:
            key = (m["lesson"], m["mistake"])
            counts[key] = counts.get(key, 0) + 1
            spec = next((x for x in s.lessons[m["lesson"]]["mistakes"]
                         if x["id"] == m["mistake"]), {})
            history.append({
                "lesson": m["lesson"], "mistake": m["mistake"],
                "description": spec.get("description", m["mistake"]),
                "try": counts[key],
                "given": "hint" if counts[key] == 1 else "full explanation",
                "time": m["time"],
            })
        return {
            "lesson": {k: lesson.get(k) for k in
                       ("id", "title", "circuit", "probe1", "expected", "accept")},
            "lesson_count": len(s.lessons),
            "passes": s.history.get("passes", {}),
            "reading": reading,
            "history": history,
            "log": self.log,
            "model": self.model_name,
            "sim": self.is_sim,
            "scenarios": SCENARIOS.get(s.lesson_id, []) if self.is_sim else [],
            "scenario": self.board.inner.scenario if self.is_sim else None,
            "busy": self.agent_lock.locked(),
        }

    def ask(self, text):
        text = (text or "").strip()[:500]
        if not text:
            return {"error": "empty message"}
        with self.agent_lock:
            self.log.append({"who": "student", "text": text, "time": time.time()})
            before = self.session.lesson_id
            try:
                reply, new = ask(self.agent, self.chat, text)
            except Exception as e:  # show the problem on the page, keep running
                reply, new = f"Sorry, the tutor hit an error: {e}", []
            tools = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
            self.log.append({"who": "tutor", "text": reply, "time": time.time(),
                             "tools": tools})
            if self.is_sim and self.session.lesson_id != before:
                # New lesson: the pretend student starts with a correct build.
                self.board.send(f"SCENARIO {self.session.lesson_id} correct")
        return {"reply": reply, "tools": tools}

    def build(self, scenario):
        if not self.is_sim:
            return {"error": "only the fake board can change circuits"}
        return self.board.send(f"SCENARIO {self.session.lesson_id} {scenario}")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(200, app.state())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            if self.path == "/api/ask":
                self._send(200, app.ask(body.get("text")))
            elif self.path == "/api/build":
                self._send(200, app.build(str(body.get("scenario", ""))))
            elif self.path == "/api/reset":
                self._send(200, app.reset())
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass  # keep the terminal quiet (the page polls 4x a second)

    return Handler


def make_server(app, port=8000, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), make_handler(app))


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart breadboard web page")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lesson", type=int, default=1)
    p.add_argument("--scenario", default="correct", help="fake board starting circuit")
    p.add_argument("--history", default=str(ROOT / "agent" / "student_history.json"))
    p.add_argument("--fresh", action="store_true", help="forget the old mistake history")
    args = p.parse_args(argv)

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()
    app = App(FakeBoard(args.lesson, args.scenario), lesson=args.lesson,
              history_path=args.history)
    server = make_server(app, args.port)
    print(f"Tutor: {app.model_name}. Board: fake board (simulator).")
    print(f"Open http://localhost:{args.port} in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
