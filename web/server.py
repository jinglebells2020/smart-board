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
    POST /api/reset   start again with a new student (a fresh notebook)
    POST /api/power-answer {"yes": true|false} -> answer "Turn the power back on?"
    GET  /api/notebook the student's notebook, as plain text
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
from agent.build import POWER_QUESTION, answer_power, ask, build_agent, make_model  # noqa: E402
from agent.buttons import handle_button  # noqa: E402
from agent.lessons import summarize  # noqa: E402
from agent.notebook import Notebook  # noqa: E402
from agent.trace import describe_steps  # noqa: E402
from agent.serial_board import BoardError, SerialBoard, choose_port  # noqa: E402
from agent.session import Session  # noqa: E402
from protocol import (BUTTON_LABELS, COLORS, DEFAULT_BAUD, SCREEN_CHARS,  # noqa: E402
                      SCREEN_LINES, power_cut_words, row_label)
from sim.fake_board import SCENARIOS, FakeBoard  # noqa: E402

PAGE = Path(__file__).resolve().parent / "index.html"


class App:
    def __init__(self, board, lesson=1, history_path=None, model=None, notebook=None,
                 confirm_power=False):
        self.is_sim = isinstance(board, FakeBoard)
        self.raw_board = board
        # One shared handle (with a lock) for the page, the tutor and buttons.
        self.board = BoardLink(board)
        self.start_lesson = lesson
        self.history_path = history_path
        self.notebook = notebook  # the student's notebook (agent/notebook.py) or None
        # Ask "Turn the power back on?" before the tutor turns power on
        # (deepagents human in the loop). The real page turns this on.
        self.confirm_power = confirm_power
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
        self.session = Session(self.board, lesson=lesson, history_path=self.history_path,
                               notebook=self.notebook)
        self.agent = build_agent(self.session, self.model, ask_before_power=self.confirm_power)
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
            if self.notebook is not None:
                self.notebook.start_over()  # the old one is kept under a dated name
            if self.board.has_power_switch:
                self.board.restore()
            self.board.clear_lights()
            self._new_session(self.start_lesson)
            if self.is_sim:  # the pretend student starts with a correct build
                self.board.send(f"SCENARIO {self.session.lesson_id} correct")
            self._welcome()
        return {"ok": True}

    def student_name(self):
        return self.notebook.student if self.notebook is not None else "the student"

    def _welcome(self):
        l = self.session.lesson
        what = "Tap the holes!" if l.get("kind") == "explore" else "Press 3 to start."
        self.board.show(f"Genesis|Lesson {l['id']}: {l['title']}|{what}")

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
        if button_id == 4 and self.waiting_for_power_answer():
            return self.answer_power(True)  # button 4 counts as Yes
        try:
            result = handle_button(button_id, self.session,
                                   lambda text: self.ask(text).get("reply", ""))
        except BoardError as e:
            self.log.append({"who": "tutor", "text": str(e), "time": time.time(), "tools": []})
            return {"error": str(e)}
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
        if action == "hint":
            mood = "curious"
        elif action == "power" and not result.get("power_on"):
            mood = "careful"
        else:
            mood = "happy"
        self.log.append({"who": "tutor", "text": text, "time": time.time(), "tools": [],
                         "mood": mood})
        return result

    @staticmethod
    def _where(name):
        return {"PT+": "the top + line (5 V)", "PT-": "the top - line (ground)"}.get(
            name, row_label(name))

    def current_card(self):
        """The card on the page: the latest mistake's, or in lesson 0 (no
        circuit) the lesson's own "Which holes are joined?" card."""
        if self.card:
            return self.card
        if self.session.is_explore():
            return {"lesson": self.session.lesson_id, "mistake": "intro",
                    "occurrence": "intro", "intro": True}
        return None

    def _card_view(self):
        """The mini-lesson card as the page needs it. The right answer and
        the "why" line are only included once the student has answered."""
        card = self.current_card()
        if not card:
            return None
        saved_card, self.card = self.card, card
        try:
            return self._card_view_of()
        finally:
            self.card = saved_card

    def _card_view_of(self):
        spec = self.session._mistake_spec(self.card["lesson"], self.card["mistake"])
        ml = spec["mini_lesson"]
        # A mini-lesson the lesson writer wrote (and Python accepted) for this
        # mistake replaces the built-in one, with its own quiz.
        ai = self.session.ai_lesson(self.card["occurrence"]) if self.card.get("ai") else None
        if ai is not None:
            ml = ai
        saved = self.session.quiz_result(self.card["occurrence"], ai=ai is not None)
        answered = None
        if saved:
            answered = {"choice": saved["choice"], "correct": saved["correct"],
                        "answer": ml["answer"], "why": ml["why"]}
        return {"occurrence": self.card["occurrence"], "lesson": self.card["lesson"],
                "mistake": self.card["mistake"], "title": ml["title"],
                "idea": ml["idea"], "question": ml["question"],
                "options": list(ml["options"]), "answered": answered,
                "written_by_zip": ai is not None, "intro": bool(self.card.get("intro")),
                # Where the drawing should point (e.g. the LED's long leg).
                "point_at": spec.get("point_at")}

    def quiz(self, occurrence, choice, written_by_zip=False):
        """Save the answer to the mini-lesson card that is on the page.
        written_by_zip says which card was clicked (the built-in one, or the
        one the lesson writer wrote), so a late click never lands on the
        other card."""
        with self.agent_lock:
            card = self.current_card()
            if card is None or occurrence != card["occurrence"]:
                return {"error": "that question is no longer on the screen"}
            ai = bool(written_by_zip) and self.session.ai_lesson(occurrence) is not None
            saved = self.session.quiz_result(occurrence, ai=ai)
            if saved:  # one answer per card; a second click changes nothing
                ml = (self.session.ai_lesson(occurrence) if ai else
                      self.session._mistake_spec(card["lesson"], card["mistake"])["mini_lesson"])
                return {"choice": saved["choice"], "correct": saved["correct"],
                        "answer": ml["answer"], "why": ml["why"], "already_answered": True}
            result = self.session.record_quiz(card["lesson"], card["mistake"],
                                              choice, occurrence, ai=ai)
        if result.get("correct") is False and not ai and card["mistake"] != "intro":
            # A wrong answer: Zip asks the lesson writer for another way to
            # explain it (in the background, so the page keeps updating).
            t = threading.Thread(target=self.ask, daemon=True,
                                 args=("I got the quiz wrong. Can you explain it another way?",))
            self.button_threads.append(t)
            t.start()
        return result

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

    def board_status(self):
        """For the page: which board, and is it answering?"""
        status = getattr(self.raw_board, "status", None)
        if callable(status):
            return status()
        return {"kind": "fake", "connected": True, "text": "Fake board (simulator)"}

    # --- For the page's guide and lesson path (computed, no new rules) -------
    # The guide's moods: happy (correct), thinking (checking), curious (a hint
    # or a question), careful (danger or power off).
    MOODS = ("happy", "thinking", "curious", "careful")

    @staticmethod
    def turn_mood(result, tools):
        """The guide's mood after a tutor reply. result is the check made
        in this reply (or None), tools the tools the tutor used."""
        status = (result or {}).get("status")
        if result and (result.get("action") == "cut_power" or result.get("manual_power_off")
                       or result.get("board_cut") or status == "power_off"):
            return "careful"
        if status == "ok":
            return "happy"
        if status in ("mistake", "unknown", "missing_rows", "quiz_first") or "get_hint" in tools \
                or "task" in tools:
            return "curious"
        return "happy"

    def mood(self, reading, reading_ok):
        """The guide's mood right now."""
        if self.agent_lock.locked():
            return "thinking"
        if self.waiting_for_power_answer():
            return "curious"  # Zip is asking a question
        if not reading_ok or not reading.get("power_on"):
            return "careful"  # no board, or power is off
        latest = next((m for m in reversed(self.log) if m["who"] == "tutor"), None)
        return (latest or {}).get("mood", "happy")

    def progress(self):
        """The lesson path: for each lesson, its stars, from the history.
        Star 1: passed. Star 2: passed with no mistake made twice in that
        lesson. Star 3: passed, and every mini-lesson quiz from that lesson
        answered right (a lesson with no mistakes has no quiz to miss)."""
        s = self.session
        passes = s.history.get("passes", {})
        out = []
        for lid in sorted(s.lessons):
            mistakes = [(i, m) for i, m in enumerate(s.history["mistakes"])
                        if m["lesson"] == lid]
            ids = [m["mistake"] for _, m in mistakes]
            passed = passes.get(str(lid), 0) > 0
            no_repeat = passed and len(ids) == len(set(ids))
            # The latest answer counts: the lesson writer's card if it was answered.
            quizzes = [s.quiz_result(i, ai=True) or s.quiz_result(i) for i, _ in mistakes]
            quiz_right = passed and all(q is not None and q["correct"] for q in quizzes)
            out.append({"id": lid, "title": s.lessons[lid]["title"],
                        "current": lid == s.lesson_id, "passed": passed,
                        "no_repeat": no_repeat, "quiz_right": quiz_right,
                        "stars": int(passed) + int(no_repeat) + int(quiz_right)})
        return out

    # A reading to show while a real board is not answering: nothing measured.
    NO_READING = {"type": "reading", "t_ms": 0, "power_on": False, "supply_v": 0.0,
                  "current_ma": None, "rows": {}, "floating": []}

    def state(self):
        self.pump_events()  # a real board's buttons arrive with the readings
        s = self.session
        lesson = s.lesson
        reading_ok = True
        try:
            reading = self.board.read()
        except BoardError:
            reading, reading_ok = dict(self.NO_READING), False
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
                       ("id", "title", "circuit", "rows", "expected", "accept", "layout",
                        "kind", "explore")},
            "path": self.progress(),
            "mood": self.mood(reading, reading_ok),
            "lesson_count": len(s.lessons),
            "passes": s.history.get("passes", {}),
            "reading": reading,
            "shown": self._shown(reading, summary),
            "board": self._board_view(reading, summary),
            "board_status": self.board_status(),
            "reading_ok": reading_ok,  # False: the board is not answering, numbers unknown
            "mini_lesson": self._card_view(),
            "history": history,
            "log": self.log,
            "model": self.model_name,
            "sim": self.is_sim,
            "scenarios": SCENARIOS.get(s.lesson_id, []) if self.is_sim else [],
            "scenario": self.raw_board.scenario if self.is_sim else None,
            "busy": self.agent_lock.locked(),
            # Zip is asking "Turn the power back on?" (Yes / No on the page).
            "asks_power": self.waiting_for_power_answer(),
        }

    def waiting_for_power_answer(self):
        """Is a reply paused on "Turn the power back on?"?"""
        return getattr(self.agent, "smart_board_pending", None) is not None

    def ask(self, text):
        text = (text or "").strip()[:500]
        if not text:
            return {"error": "empty message"}
        with self.agent_lock:
            if self.waiting_for_power_answer():
                # A new message instead of Yes or No: that is a no.
                self._turn("No", lambda: answer_power(self.agent, self.chat, False), resume=True)
            return self._turn(text, lambda: ask(self.agent, self.chat, text))

    def answer_power(self, yes):
        """The student's Yes or No to "Turn the power back on?"."""
        with self.agent_lock:
            if not self.waiting_for_power_answer():
                return {"error": "Zip is not asking about the power right now"}
            return self._turn("Yes" if yes else "No",
                              lambda: answer_power(self.agent, self.chat, bool(yes)), resume=True)

    def _turn(self, text, run, resume=False):
        """Run one tutor reply (or the rest of a paused one) and log it."""
        self.log.append({"who": "student", "text": text, "time": time.time()})
        if not resume:
            self._turn_start = (self.session.lesson_id, self.session.last_check)
            self.session.last_logged = None
        before, check_before = getattr(self, "_turn_start", (self.session.lesson_id, None))
        try:
            reply, new = run()
        except Exception as e:  # show the problem on the page, keep running
            reply, new = f"Sorry, the tutor hit an error: {e}", []
            self.agent.smart_board_pending = None
        tools = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
        steps = describe_steps(self.session.trace, self.student_name())
        if self.waiting_for_power_answer():
            # Paused: Zip asks before the power comes back on.
            self.log.append({"who": "tutor", "text": reply, "time": time.time(), "steps": steps,
                             "tools": tools, "mood": "curious", "asks_power": True,
                             "celebrate": False})
            return {"reply": reply, "tools": tools, "pending": True}
        # A mistake was logged in this reply: show its mini-lesson card.
        # Otherwise (circuit correct, power off, ...) the old card goes away,
        # except after asking the lesson writer: then the card stays.
        old_card = self.card
        self.card = dict(self.session.last_logged) if self.session.last_logged else None
        if self.session.new_ai_lesson is not None:
            # The lesson writer's card was accepted: show it instead.
            occ = self.session.new_ai_lesson
            spec = self.session.ai_lesson(occ)
            self.card = {"lesson": spec["lesson"], "mistake": spec["mistake"],
                         "occurrence": occ, "ai": True}
            self.session.new_ai_lesson = None
        elif self.card is None and old_card is not None and "task" in tools \
                and self.session.last_check is check_before:  # no new check this reply
            self.card = old_card
        checked = self.session.last_check is not check_before
        result = self.session.last_check[1] if checked else None
        mood = self.turn_mood(result, tools)
        self.log.append({"who": "tutor", "text": reply, "time": time.time(),
                         # "How Zip worked": the recorded steps of this reply.
                         "steps": steps,
                         "tools": tools, "mood": mood,
                         # The page shows a short celebration for this reply.
                         "celebrate": bool(result and result.get("status") == "ok")})
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
            elif self.path == "/api/notebook":
                text = app.notebook.text() if app.notebook else "No notebook for this session.\n"
                self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
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
            elif self.path == "/api/power-answer":
                self._send(200, app.answer_power(bool(body.get("yes"))))
            elif self.path == "/api/quiz":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.quiz(body.get("occurrence"), body.get("choice"),
                                         body.get("written_by_zip", False)))
            elif self.path == "/api/reset":
                self._send(200, app.reset())
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass  # keep the terminal quiet (the page polls 4x a second)

    return Handler


def default_history(student):
    """The mistake history file: the old name for the default student, so
    nothing is lost, and one file per other student."""
    from agent.notebook import safe_name
    name = safe_name(student)
    folder = ROOT / "agent"
    return folder / ("student_history.json" if name == "student" else f"history_{name}.json")


def open_board(args):
    """The fake board, or with --serial a real one. Problems are printed
    in plain words and give None."""
    if not args.serial:
        return FakeBoard(args.lesson if args.lesson is not None else 1, args.scenario, rows=args.rows,
                         has_current=not args.no_current,
                         has_power_switch=not args.no_power_switch,
                         has_screen=not args.no_screen)
    try:
        port = choose_port(args.serial)
        print(f"Connecting to the board on {port} at {args.baud} baud...")
        return SerialBoard(port, args.baud)
    except BoardError as e:
        print(f"Could not use the real board. {e}")
        return None


def make_server(app, port=8000, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), make_handler(app))


def main(argv=None):
    p = argparse.ArgumentParser(description="Genesis: the smart breadboard web page")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lesson", type=int, default=None,
                   help="lesson to start at (default: lesson 0 for a new student)")
    p.add_argument("--scenario", default="correct", help="fake board starting circuit")
    p.add_argument("--history", default=None,
                   help="file that keeps the mistake history (default: one per student)")
    p.add_argument("--student", default="student",
                   help="the student's name: their notebook is notebooks/<name>.md")
    p.add_argument("--notebooks", default=str(ROOT / "notebooks"),
                   help="folder for the student notebooks")
    p.add_argument("--fresh", action="store_true", help="forget the old mistake history")
    p.add_argument("--rows", default="all", help='rows the fake board measures, e.g. "T1-T16"')
    p.add_argument("--no-current", action="store_true", help="fake board has no current sensor")
    p.add_argument("--no-power-switch", action="store_true",
                   help="fake board cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="fake board has no screen")
    p.add_argument("--serial", metavar="PORT",
                   help='use a real board on this port (COM3, /dev/ttyUSB0) or "auto"')
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   help=f"serial speed for --serial (default {DEFAULT_BAUD})")
    args = p.parse_args(argv)
    if args.history is None:
        args.history = str(default_history(args.student))

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()
    board = open_board(args)
    if board is None:
        return 2
    notebook = Notebook(args.notebooks, args.student)
    app = App(board, lesson="start" if args.lesson is None else args.lesson,
              history_path=args.history, notebook=notebook,
              confirm_power=True)
    if app.is_sim and app.session.lesson_id != board.lesson:
        # A new student starts at lesson 0: the pretend board shows that lesson.
        board.send(f"SCENARIO {app.session.lesson_id} correct")
        app._welcome()
    server = make_server(app, args.port)
    caps = app.board.capabilities()
    where = f"real board on {board.port}" if args.serial else "fake board"
    print(f"Tutor: {app.model_name}. Board: {where}, {caps['rows']} rows measured, "
          f"current sensor {'yes' if caps['has_current'] else 'no'}, "
          f"power switch {'yes' if caps['has_power_switch'] else 'no'}.")
    print(f"Notebook for {args.student}: {notebook.path}")
    print(f"Open http://localhost:{args.port} in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
