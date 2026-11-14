"""Local web server for web/index.html.

    python web/server.py            then open http://localhost:8000

The page cannot talk to the board or the agent by itself, so this
server sits in between. It uses only Python's built-in http.server.

    GET  /            the page
    GET  /api/state   lesson, latest reading, mistake history (polled ~4x a second)
    POST /api/ask     {"text": "..."}      -> the tutor's reply
    POST /api/build   {"scenario": "..."}  -> fake board only: change the circuit
    POST /api/quiz    {"occurrence": n, "choice": 0..2} -> answer the mini-lesson question
    POST /api/press   {"id": 1..4}         -> fake board only: press a board button
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

from agent.boardlink import BoardLink  # noqa: E402
from agent.build import ask, build_agent, make_model  # noqa: E402
from agent.buttons import handle_button  # noqa: E402
from agent.lessons import summarize  # noqa: E402
from agent.session import Session  # noqa: E402
from protocol import (BUTTON_LABELS, COLORS, SCREEN_CHARS, SCREEN_LINES,  # noqa: E402
                      power_cut_words, row_label)
from sim.fake_board import SCENARIOS, FakeBoard  # noqa: E402

PAGE = Path(__file__).resolve().parent / "index.html"


class App:
    def __init__(self, board, lesson=1, history_path=None, model=None):
        self.is_sim = isinstance(board, FakeBoard)
        self.raw_board = board
        # One shared handle (with a lock) for the page, the tutor and buttons.
        self.board = BoardLink(board)
        self.start_lesson = lesson
        self.history_path = history_path
        if model is None:
            model, is_real = make_model()
        else:
            is_real = isinstance(model, str)
        self.model = model
        self.model_name = model if is_real else "offline practice tutor"
        self.agent_lock = threading.Lock()  # one question at a time
        self.button_threads = []  # button presses being handled
        self._new_session(lesson)
        self._welcome()

    def _new_session(self, lesson):
        self.session = Session(self.board, lesson=lesson, history_path=self.history_path)
        self.agent = build_agent(self.session, self.model)
        self.chat = []  # agent message history
        self.log = []   # what the page shows: [{"who", "text", "time"}]
        # The mistake whose mini-lesson card is on the page, or None:
        # {"lesson", "mistake", "occurrence"}. Set after a reply that logged a
        # mistake; cleared by the next reply that did not.
        self.card = None

    def reset(self):
        with self.agent_lock:
            if self.history_path and Path(self.history_path).exists():
                Path(self.history_path).unlink()
            if self.board.has_power_switch:
                self.board.restore()
            self.board.clear_lights()
            if self.is_sim:
                self.board.send(f"SCENARIO {self.start_lesson} correct")
            self._new_session(self.start_lesson)
            self._welcome()
        return {"ok": True}

    def _welcome(self):
        l = self.session.lesson
        self.board.show(f"Lesson {l['id']}: {l['title']}|Press 3 to see where|the first part goes.")

    # --- Board buttons -------------------------------------------------
    def press(self, button_id):
        """A button on the pretend board was clicked: press it on the
        fake board, so it goes the same way as a real button press."""
        if not self.is_sim:
            return {"error": "only the fake board has buttons on the page"}
        if not isinstance(button_id, int) or isinstance(button_id, bool):
            return {"error": "id must be a number 1 to 4"}
        answer = self.board.send(f"PRESS {button_id}")
        if answer.get("type") == "error":
            return {"error": answer["msg"]}
        handled = self.pump_events()
        return {"ok": True, "handled": handled}

    def pump_events(self):
        """Handle button presses the board has sent. Questions for the
        tutor run in the background so the page keeps updating."""
        events = [e for e in self.board.events() if e.get("type") == "button"]
        # The board switched power off by itself while nobody was checking:
        # say so on the page once. (During a check the tutor says it.)
        if not self.agent_lock.locked():
            for cut in self.board.power_cuts:
                if not cut.get("explained") and not cut.get("logged"):
                    cut["logged"] = True
                    self.log.append({"who": "tutor", "text": power_cut_words(cut),
                                     "time": time.time(), "tools": []})
        self.button_threads = [t for t in self.button_threads if t.is_alive()]
        for ev in events:
            t = threading.Thread(target=self.on_button, args=(ev["id"],), daemon=True)
            self.button_threads.append(t)
            t.start()
        return [e["id"] for e in events]

    def wait_for_buttons(self, timeout=30):
        """Wait until every button press being handled is done."""
        for t in list(self.button_threads):
            t.join(timeout)

    def on_button(self, button_id):
        result = handle_button(button_id, self.session,
                               lambda text: self.ask(text).get("reply", ""))
        action = result.get("action")
        if action == "check":
            return result  # self.ask already put the tutor's reply in the log
        if action == "hint":
            text = result["say"]
        elif action == "next_step":
            if result.get("done"):
                text = "All parts are in place. Press 1 (Check my circuit) when you are ready."
            else:
                text = (f"Step {result['step']} of {result['of']}: {result['part']}. "
                        f"Put {Session.where_legs(result['legs'])}. "
                        "The blue lights show the rows.")
        elif action == "power":
            text = result.get("say") or ("Power is off." if not result.get("power_on")
                                         else "Power is on.")
        else:
            text = result.get("error", "")
        self.log.append({"who": "tutor", "text": text, "time": time.time(), "tools": []})
        return result

    @staticmethod
    def _where(name):
        return {"PT+": "the top + line (5 V)", "PT-": "the top - line (ground)"}.get(
            name, row_label(name))

    def _card_view(self):
        """The mini-lesson card as the page needs it. The right answer and
        the "why" line are only included once the student has answered."""
        if not self.card:
            return None
        spec = self.session._mistake_spec(self.card["lesson"], self.card["mistake"])
        ml = spec["mini_lesson"]
        saved = self.session.quiz_result(self.card["occurrence"])
        answered = None
        if saved:
            answered = {"choice": saved["choice"], "correct": saved["correct"],
                        "answer": ml["answer"], "why": ml["why"]}
        return {"occurrence": self.card["occurrence"], "lesson": self.card["lesson"],
                "mistake": self.card["mistake"], "title": ml["title"],
                "idea": ml["idea"], "question": ml["question"],
                "options": list(ml["options"]), "answered": answered}

    def quiz(self, occurrence, choice):
        """Save the answer to the mini-lesson card that is on the page."""
        with self.agent_lock:
            card = self.card
            if card is None or occurrence != card["occurrence"]:
                return {"error": "that question is no longer on the screen"}
            saved = self.session.quiz_result(occurrence)
            if saved:  # one answer per mistake; a second click changes nothing
                view = self._card_view()["answered"]
                return dict(view, already_answered=True)
            return self.session.record_quiz(card["lesson"], card["mistake"],
                                            choice, occurrence)

    @staticmethod
    def _clean(v, floor=None):
        """A number ready to show a student: tiny negatives are sensor
        noise around 0, and nothing shows as -0. None stays None."""
        if v is None:
            return None
        v = round(float(v), 3)
        if floor is not None:
            v = max(floor, v)
        elif -0.05 < v < 0:  # sensor noise around 0 V
            v = 0.0
        return v + 0.0  # turns -0.0 into 0.0

    def _shown(self, reading, summary):
        """Numbers for the page. Current is measured when the board has a
        sensor, otherwise calculated from the rows, or None if unknown.
        It is never shown below 0."""
        return {"current_ma": self._clean(summary["current_ma"], floor=0.0),
                "current_source": summary["current_source"],
                "supply_v": self._clean(reading["supply_v"])}

    def _board_view(self, reading, summary):
        """The pretend board for the page: the lesson's rows with their live
        voltage and light, the screen, the buttons, what the board has."""
        b = self.board
        rows = []
        for name in self.session.lesson["rows"]:
            measured = name in reading["rows"]
            rows.append({"name": name, "label": row_label(name), "measured": measured,
                         "volts": self._clean(reading["rows"][name]) if measured else None,
                         "floating": name in reading.get("floating", []),
                         "light": b.lights.get(name, "off")})
        return {"capabilities": b.capabilities(), "pretend": self.is_sim,
                "rows": rows, "missing_rows": summary["missing_rows"],
                "lights": dict(b.lights), "screen": list(b.screen), "colors": COLORS,
                "screen_size": [SCREEN_LINES, SCREEN_CHARS],
                "buttons": {str(i): BUTTON_LABELS[i] for i in range(1, b.buttons + 1)},
                "step": self.session.step + 1 if self.session.step >= 0 else None,
                "steps": len(self.session.lesson["layout"])}

    def state(self):
        self.pump_events()  # a real board's buttons arrive with the readings
        s = self.session
        lesson = s.lesson
        reading = self.board.read()
        summary = summarize([reading], lesson)
        counts = {}
        history = []
        for i, m in enumerate(s.history["mistakes"]):
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
                "quiz": (None if s.quiz_result(i) is None
                         else "right" if s.quiz_result(i)["correct"] else "wrong"),
            })
        return {
            "lesson": {k: lesson.get(k) for k in
                       ("id", "title", "circuit", "rows", "expected", "accept")},
            "lesson_count": len(s.lessons),
            "passes": s.history.get("passes", {}),
            "reading": reading,
            "shown": self._shown(reading, summary),
            "board": self._board_view(reading, summary),
            "mini_lesson": self._card_view(),
            "history": history,
            "log": self.log,
            "model": self.model_name,
            "sim": self.is_sim,
            "scenarios": SCENARIOS.get(s.lesson_id, []) if self.is_sim else [],
            "scenario": self.raw_board.scenario if self.is_sim else None,
            "busy": self.agent_lock.locked(),
        }

    def ask(self, text):
        text = (text or "").strip()[:500]
        if not text:
            return {"error": "empty message"}
        with self.agent_lock:
            self.log.append({"who": "student", "text": text, "time": time.time()})
            before = self.session.lesson_id
            self.session.last_logged = None
            try:
                reply, new = ask(self.agent, self.chat, text)
            except Exception as e:  # show the problem on the page, keep running
                reply, new = f"Sorry, the tutor hit an error: {e}", []
            # A mistake was logged in this reply: show its mini-lesson card.
            # Otherwise (circuit correct, power off, ...) the old card goes away.
            self.card = dict(self.session.last_logged) if self.session.last_logged else None
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
            elif self.path == "/api/press":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.press(body.get("id")))
            elif self.path == "/api/quiz":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.quiz(body.get("occurrence"), body.get("choice")))
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
    p.add_argument("--rows", default="all", help='rows the fake board measures, e.g. "T1-T16"')
    p.add_argument("--no-current", action="store_true", help="fake board has no current sensor")
    p.add_argument("--no-power-switch", action="store_true",
                   help="fake board cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="fake board has no screen")
    args = p.parse_args(argv)

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()
    board = FakeBoard(args.lesson, args.scenario, rows=args.rows,
                      has_current=not args.no_current,
                      has_power_switch=not args.no_power_switch,
                      has_screen=not args.no_screen)
    app = App(board, lesson=args.lesson, history_path=args.history)
    server = make_server(app, args.port)
    caps = app.board.capabilities()
    print(f"Tutor: {app.model_name}. Board: fake board, {caps['rows']} rows measured, "
          f"current sensor {'yes' if caps['has_current'] else 'no'}, "
          f"power switch {'yes' if caps['has_power_switch'] else 'no'}.")
    print(f"Open http://localhost:{args.port} in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
