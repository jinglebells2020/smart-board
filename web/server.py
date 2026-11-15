"""Local web server for web/index.html.

    python web/server.py            then open http://localhost:8000

The page cannot talk to the board or the agent by itself, so this
server sits in between. It uses only Python's built-in http.server.

    GET  /            the page
    GET  /api/state   lesson, latest reading, mistake history (polled ~4x a second)
    POST /api/ask     {"text": "..."}      -> the tutor's reply
    POST /api/build   {"scenario": "...", "place": "T 15", "parts": 2} -> fake board only: change the circuit
                      ("parts": only the first n parts are in, or "all")
                      (place, optional: build it in other rows, top T or bottom B;
                      or "copies": ["T 0", "T 15"]: the circuit at each place, lesson 3;
                      or "rows": ["T20", "T27", "T29"]: on these rows, any spacing)
    POST /api/quiz    {"occurrence": n, "choice": 0..2} -> answer the mini-lesson question
    POST /api/press   {"id": 1..4}         -> fake board only: press a board button
    POST /api/reset   start again with a new student (a fresh notebook)
    POST /api/restart-demo  start the demo again: Maya, lesson 1, fresh history and notebook
    POST /api/power-answer {"yes": true|false} -> answer "Turn the power back on?"
    POST /api/hold    {"on": true|false}   -> fake board only: hold the breadboard's push button (lesson 4)
    POST /api/instant {"on": true|false}   -> instant help: react when the board changes, without Check
    POST /api/experiment {"done": true|false} -> answer Zip's experiment card ("done" or "can't")
    POST /api/netlist {"parts": [...]}   -> fake board only (free build): build exactly these
                      parts, e.g. {"kind": "resistor", "rows": ["T5", "T10"], "ohms": 220}
    POST /api/random-circuit {"seed": n, "parts": 2..5} -> fake board only: a random judge
                      circuit on usable rows. The answer has the true netlist (for testing;
                      it is never sent to the tutor)
    POST /api/tray    {"parts": [{"name": "red LED", "count": 1}, ...], "goal": "..."}
                      -> the parts tray: what the student used, what it should do
    GET  /api/notebook the student's notebook, as plain text
"""

import argparse
import copy
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so "python web/server.py" can import agent and sim

from agent.boardlink import BoardLink  # noqa: E402
from agent.build import CLAUDE_TIMEOUT, POWER_QUESTION, build_agent, make_model  # noqa: E402
from agent.fallback import OFFLINE_NOTE, STATE, SafeReplies  # noqa: E402
from agent.live import ChangeDetector, classify  # noqa: E402
from agent.physics import observations  # noqa: E402
from agent.buttons import handle_button  # noqa: E402
from agent.lessons import FREE_ID, summarize  # noqa: E402
from agent.tray import TRAY_PARTS, clean_tray  # noqa: E402
from agent.notebook import Notebook  # noqa: E402
from agent.activity import Activity  # noqa: E402
from agent.trace import describe_step, describe_steps  # noqa: E402
from agent.serial_board import BoardError, SerialBoard, choose_port  # noqa: E402
from agent.session import Session  # noqa: E402
from protocol import (BUTTON_COLORS, BUTTON_LABELS, COLORS, DEFAULT_BAUD,  # noqa: E402
                      SCREEN_CHARS, SCREEN_LINES, SUPPLY_V, USABLE_ROWS_ENV, button_words,
                      is_row, parse_usable, power_cut_words, row_label)
from sim.fake_board import SCENARIOS, SIM_EXTRA, FakeBoard  # noqa: E402
from sim.random_circuit import random_circuit  # noqa: E402
from sim.solver import NetlistError  # noqa: E402

PAGE = Path(__file__).resolve().parent / "index.html"


class App:
    def __init__(self, board, lesson=1, history_path=None, model=None, notebook=None,
                 confirm_power=False, log=None, timeout=CLAUDE_TIMEOUT, demo=None,
                 instant=False, usable_rows=None, free_build=False):
        self.is_sim = isinstance(board, FakeBoard)
        # Free build (--free-build): a lesson-like mode with no plan, after lesson 4.
        self.free_build = bool(free_build)
        self.tray = None  # the parts tray: what the student used (free build)
        # Instant help (agent/live.py): react the moment the board changes.
        # The server turns it on (--no-instant turns it off); off by default
        # here, so a test or a script decides.
        self.instant = bool(instant)
        self.live = ChangeDetector()
        self.instant_inline = False  # tests: handle a change now, not in a thread
        self.auto_inline = False     # tests: run the automatic check now, not in a thread
        self.instant_mistake = None  # the last mistake instant help told about
        self.building = None         # {"parts_in", "parts"} from the latest instant check
        self._auto_guard = threading.Lock()
        self._auto_running = False
        self._auto_again = False
        self._auto_done = threading.Event()
        self._auto_done.set()
        self._auto_last = None
        self._watching = None      # the lesson instant help is watching (a change resets it)
        self._changes = 0          # steady board changes handled so far
        self._passed_at = None     # _changes when an automatic check last passed a lesson
        self._auto_context = None  # what the next automatic check is about (the trigger)
        self._auto_kind = "check"  # the next automatic turn: "check" or a silent "guide"
        self._auto_current = None  # the kind of the automatic turn running now
        self.raw_board = board
        # One shared handle (with a lock) for the page, the tutor and buttons.
        self.board = BoardLink(board, usable_rows)  # only the usable rows (default: all)
        self.start_lesson = lesson
        self.history_path = history_path
        self.notebook = notebook  # the student's notebook (agent/notebook.py) or None
        # Ask "Turn the power back on?" before the tutor turns power on
        # (deepagents human in the loop). The real page turns this on.
        self.confirm_power = confirm_power
        # Where "Restart demo" starts: {"student", "lesson", "history"}.
        self.demo = demo
        # One line per Claude reply in the server window (how long it took,
        # or why the offline tutor answered). None: quiet (tests).
        self.log_line = log
        self.timeout = timeout  # seconds Claude may take before the offline tutor answers
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
                               notebook=self.notebook, free_build=self.free_build)
        self.session.tray = self.tray
        self.agent = build_agent(self.session, self.model, ask_before_power=self.confirm_power)
        # Claude's reply, or the offline tutor's when Claude fails or is slow.
        self.replies = SafeReplies(self.session, ask_before_power=self.confirm_power,
                                   timeout=self.timeout, log=self.log_line)
        self.chat = []  # agent message history
        # The judges' "Agent activity" panel: everything the agents do, live
        # (agent/activity.py). It only reads what happens.
        self.activity = Activity()
        self.session.on_safety = self._on_safety
        self.log = []   # what the page shows: [{"who", "text", "time"}]
        # The mistake whose mini-lesson card is on the page, or None:
        # {"lesson", "mistake", "occurrence"}. Set after a reply that logged a
        # mistake; cleared by the next reply that did not.
        self.card = None

    def reset(self):
        with self.agent_lock:
            self._reset()
        return {"ok": True}

    def restart_demo(self):
        """Teacher view → Restart demo: the student "Maya" at lesson 1, with a
        fresh history and notebook (the old notebook is kept under a dated
        name), the pretend board back to a correct build, power on."""
        demo = self.demo or {
            "student": "Maya", "lesson": 1,
            "history": str((Path(self.history_path).parent if self.history_path
                            else ROOT / "agent") / "history_maya.json")}
        with self.agent_lock:
            self.history_path = demo["history"]
            self.start_lesson = demo["lesson"]
            if self.notebook is not None:
                self.notebook = Notebook(self.notebook.folder, demo["student"])
            self._reset()
        return {"ok": True, "student": demo["student"], "lesson": demo["lesson"]}

    def _reset(self):
        """Start again (called with agent_lock held)."""
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
        self.watch_new_lesson()  # instant help: a fresh start, nothing to react to yet
        self._passed_at = None
        self._welcome()

    def student_name(self):
        return self.notebook.student if self.notebook is not None else "the student"

    def _welcome(self):
        l = self.session.lesson
        what = {"explore": "Tap the holes!", "free": "Build anything!"}.get(
            l.get("kind"), f"Press {BUTTON_COLORS[3]} to start.")
        name = l["title"] if l.get("kind") == "free" else f"Lesson {l['id']}: {l['title']}"
        self.board.show(f"Genesis|{name}|{what}")

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
                    self.activity.add("Python safety", "The board switched the power off by "
                                      "itself", "act on board", "cut")
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
                text = (f"All parts are in place. Press {button_words(1)} (Check my circuit) "
                        "when you are ready.")
            else:
                text = (f"Step {result['step']} of {result['of']}: {result['part']}. "
                        f"Put {Session.where_legs(result['legs'], self.board.supply_v)}. "
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

    def _where(self, name):
        return {"PT+": f"the top + line ({self.board.supply_v:g} V)",
                "PT-": "the top - line (ground)"}.get(name, row_label(name))

    def current_card(self):
        """The card on the page: the latest mistake's, or in lesson 0 (no
        circuit) the lesson's own "Which holes are joined?" card."""
        if self.card:
            return self.card
        if self.session.is_explore() or "intro_lesson" in self.session.lesson:
            # Lesson 0's "Which holes are joined?", lesson 4's drawbridge.
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
        names = self.session.lesson["rows"]
        if self.session.is_free():  # no plan: every usable row the board measures
            names = [r for r in self.board.usable_rows if r in reading["rows"]]
        for name in names:
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
                # The station's button colours (yellow, white, blue, red), for
                # the pretend board's buttons on the page.
                "button_colors": {str(i): BUTTON_COLORS[i] for i in range(1, b.buttons + 1)},
                "supply_v": b.supply_v,
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
        if status in ("mistake", "unknown", "missing_rows", "quiz_first", "press_first") \
                or "get_hint" in tools \
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
                        "kind": s.lessons[lid].get("kind"),
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
        if s.button_step:  # lesson 4: the rules and numbers of the current step
            lesson = s._step_lesson(lesson)
        reading_ok = True
        try:
            reading = self.board.read()
        except BoardError:
            reading, reading_ok = dict(self.NO_READING), False
        summary = summarize([reading], lesson)
        if reading_ok and self.instant:
            self.instant_tick(reading)  # instant help sees the same reading
        counts = {}
        history = []
        for i, m in enumerate(s.history["mistakes"]):
            key = (m["lesson"], m["mistake"])
            counts[key] = counts.get(key, 0) + 1
            spec = s._mistake_spec(m["lesson"], m["mistake"]) or {}
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
                        "kind", "explore", "copies", "states")},
            # Lesson 4 (the button): which step, and the rules of that step.
            "button_step": s.button_step,
            # Lessons with copies: how many LED circuits the latest check found.
            "copies_found": ((s.last_check or ({}, {}))[1] or {}).get("count")
                            if s.lesson.get("copies") else None,
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
            "offline_note": OFFLINE_NOTE,
            "sim": self.is_sim,
            "scenarios": (SCENARIOS.get(s.lesson_id, []) + SIM_EXTRA.get(s.lesson_id, [])
                          if self.is_sim else []),
            "scenario": self.raw_board.scenario if self.is_sim else None,
            "place": list(getattr(self.raw_board, "place", ("T", 0))) if self.is_sim else None,
            "held": bool(getattr(self.raw_board, "held", False)) if self.is_sim else None,
            "busy": self.agent_lock.locked(),
            # Instant help: on or off, and how far the build is.
            "instant": self.instant,
            "building": self.building,
            # Zip is asking "Turn the power back on?" (Yes / No on the page).
            "asks_power": self.waiting_for_power_answer(),
            # Zip asks the student to do one thing, then measures again.
            "experiment": self.waiting_for_experiment(),
            # Rows that work on this board (--usable-rows; default every row).
            "usable_rows": self.board.usable_rows,
            "free_build": self.free_build,
            # The parts tray (free build): what the student used, what it should do.
            "tray": self.tray,
            "tray_parts": TRAY_PARTS,
            # Free build: what Zip thinks was built, and how well physics agreed.
            "circuit_guess": self._guess_view(),
            # The judges' panel: the last 50 things the agents did, and counts.
            **self._activity_view(),
        }

    # --- Agent activity (agent/activity.py): only reads what happens ----------
    def _collect(self):
        """Add the steps recorded so far in this reply (live, while it runs)."""
        self.activity.read_trace(self.session.trace, self._auto_current or "reply",
                                 self.student_name())

    def _activity_view(self):
        self._collect()
        return self.activity.view()

    def _on_safety(self, text):
        self._collect()  # what led to it comes first
        self.activity.add("Python safety", text, "act on board", "cut")

    def waiting_for_power_answer(self):
        """Is a reply paused on "Turn the power back on?"?"""
        pending = self.replies.pending(self.agent)
        return pending is not None and pending.get("kind") != "experiment"

    def waiting_for_experiment(self):
        """The experiment card Zip is waiting on ("done" or "can't"), or None."""
        pending = self.replies.pending(self.agent)
        return pending.get("experiment") if pending and pending.get("kind") == "experiment" \
            else None

    def answer_experiment(self, done):
        """The student did the experiment ("done") or could not ("can't")."""
        with self.agent_lock:
            exp = self.waiting_for_experiment()
            if exp is None:
                return {"error": "Zip is not waiting for an experiment right now"}
            return self._turn(exp["done_label"] if done else exp["cant_label"],
                              lambda: self.replies.answer_experiment(self.agent, self.chat,
                                                                     bool(done)),
                              resume=True)

    # --- Instant help (agent/live.py) ------------------------------------------
    CHECK_TEXT = "Check my circuit"

    def set_instant(self, on):
        self.instant = bool(on)
        self.live.reset()  # start again from the board as it is now
        self.building = None
        return {"ok": True, "instant": self.instant}

    def instant_tick(self, reading=None):
        """Give instant help one reading (the server's own, or a new one).
        Returns the change event, if this reading completed one."""
        if not self.instant or self.session.is_explore() or self.session.is_free():
            return None  # (free build: instant help comes with the AI reader, phase 2)
        if self.session.lesson_id != self._watching:
            self.watch_new_lesson()
        if reading is None:
            try:
                reading = self.board.read()
            except BoardError:
                return None
        event = self.live.feed(reading)
        if event is not None:
            if self.instant_inline:
                self._instant(event)
            else:
                threading.Thread(target=self._instant, args=(event,), daemon=True).start()
        return event

    def watch_new_lesson(self):
        """A new lesson (a pass, or any set_lesson): forget the old board
        state, take the board as it is as the new starting point (silently),
        and wait for the student to change it. On a real board the old
        circuit would otherwise be checked against the new lesson at once."""
        self._watching = self.session.lesson_id
        self.live.reset()
        self.instant_mistake = None
        self.building = None
        self._auto_context = None

    def _instant(self, event):
        """One steady change: a quick check in plain Python (no AI, the
        lesson's state is put back after), then decide what to say."""
        self._changes += 1
        if self.replies.paused(self.agent) is not None or not self.agent_lock.acquire(blocking=False):
            with self._auto_guard:  # a check is running: it sees the board anyway,
                if self._auto_running:  # and an automatic one runs once more after
                    self._auto_again = True
            return
        try:
            s = self.session
            saved = {k: copy.deepcopy(getattr(s, k)) for k in STATE}
            diagnoser, s.diagnoser = s.diagnoser, None  # instant help never waits for AI
            try:
                summary, result, cut = s.measure()
                lesson = s._step_lesson(s.lesson) if s.button_step else s.lesson
                facts = observations(summary, lesson, extra_rows=result.get("extra_rows"),
                                     safety_cut=cut)
                plan = dict(lesson, rows=lesson.get("accept", {}).get("driven") or lesson["rows"])
                found = classify(result, summary, facts, plan)
            finally:
                for k, v in saved.items():  # instant help never logs, passes or moves on
                    setattr(s, k, v)
                s.diagnoser = diagnoser
        finally:
            self.agent_lock.release()
        self._act(result, cut, found, event)

    def _act(self, result, cut, found, event):
        before = (self.building or {}).get("parts_in", 0)
        self.building = {"parts_in": found["parts_in"], "parts": found["parts"]}
        if found["any_driven"] or cut:
            self.activity.add("Instant help", "Saw the board change and checked it: "
                              f"{found['parts_in']} of {found['parts']} parts in", "check")
        if not found["any_driven"] and not cut:
            # An empty board (cleared): nothing to say, start again from here.
            self.instant_mistake = None
            self._auto_context = None
            self.board.clear_lights()
            return
        if cut:  # safety came first: say why, at once
            self.instant_mistake = found
            self._say("I switched the power off to keep your parts safe. " + found["text"],
                      "careful")
            return
        if found["kind"] == "mistake":
            if self.instant_mistake and self.instant_mistake["rows"] == found["rows"] \
                    and self.instant_mistake["text"] == found["text"]:
                return  # already said
            self.instant_mistake = found
            self.board.clear_lights()
            if found["rows"]:
                self.board.set_lights(found["rows"], "yellow")  # careful: look here
                self.activity.add("Instant help", describe_step(
                    {"who": "tutor", "tool": "light_row",
                     "args": {"rows": found["rows"], "color": "yellow"}}), "act on board",
                    "light_row")
            self._say(found["text"], "curious")
            # The automatic check explains this trigger first, and while the
            # build is not finished, empty rows are not mistakes.
            building = found["parts_in"] < found["parts"] or bool(found["empty"])
            self._auto_context = {
                "trigger": {"fact": found["fact"], "rows": found["rows"], "text": found["text"]},
                "building": self.building if building else None,
                "empty_rows": list(found["empty"])}
            self.trigger_auto()  # then Zip explains it, hint first
            return
        if self.instant_mistake is not None:
            # The trigger is gone, and something is still plugged in: fixed.
            self.instant_mistake = None
            self._auto_context = None
            self.board.clear_lights()
            self._say("That fixed it.", "happy")
            if found["kind"] == "complete" and result.get("status") in ("ok", "step_ok"):
                self.trigger_auto()  # complete and right: the normal praise and pass
            return
        if found["kind"] == "complete":
            self._auto_context = None
            self.trigger_auto()  # every part is in: a normal check decides
        elif found["kind"] == "building" and found["parts_in"] > before:
            self.trigger_auto("guide")  # a part went in: Zip shows where the next one goes

    def _say(self, text, mood):
        self.activity.add("Instant help", f"Said at once: {text}", "check")
        self.log.append({"who": "tutor", "text": text, "time": time.time(), "instant": True,
                         "steps": [], "tools": [], "mood": mood, "celebrate": False})

    GUIDE_TEXT = "Show me where the next part goes."

    def trigger_auto(self, kind="check"):
        """The AI layer: the same path as the Check button (or a short silent
        "guide" turn while building), in a worker thread. One at a time; a
        change while it runs makes it run once more after (a check wins over
        a guide)."""
        with self._auto_guard:
            if kind == "check" or not self._auto_running:
                self._auto_kind = kind
            if self._auto_running:
                self._auto_again = True
                return
            self._auto_running = True
            self._auto_done.clear()
        if self.auto_inline:
            self._auto_loop()
        else:
            threading.Thread(target=self._auto_loop, daemon=True).start()

    def _auto_loop(self):
        try:
            while True:
                with self._auto_guard:
                    kind, self._auto_kind = self._auto_kind, "guide"
                text = self.GUIDE_TEXT if kind == "guide" else self.CHECK_TEXT
                if kind == "check" and self._passed_at is not None \
                        and self._passed_at == self._changes:
                    pass  # an automatic check passed a lesson and the board has not
                    # changed since: never pass the next lesson on the same board
                elif self.replies.paused(self.agent) is None and \
                        self.agent_lock.acquire(blocking=False):
                    lesson = self.session.lesson_id
                    self.session.instant_context = self._auto_context
                    self._auto_current = kind
                    try:
                        out = self._turn(text, lambda: self.replies.ask(self.agent, self.chat, text),
                                         auto=True, silent=kind == "guide")
                        if kind == "check":
                            self._auto_last = out
                    finally:
                        self._auto_current = None
                        self.session.instant_context = None
                        self.agent_lock.release()
                    if self.session.lesson_id != lesson:
                        self._passed_at = self._changes  # passed: wait for a board change
                        self.watch_new_lesson()
                # (else the student pressed Check: that check sees the same board)
                with self._auto_guard:
                    if not self._auto_again:
                        return
                    self._auto_again = False
        finally:
            with self._auto_guard:
                self._auto_running = False
                self._auto_again = False
                self._auto_done.set()

    def wait_for_auto(self, timeout=30):
        return self._auto_done.wait(timeout)

    @staticmethod
    def is_check(text):
        return (text or "").strip().lower().startswith("check")

    def ask(self, text):
        text = (text or "").strip()[:500]
        if not text:
            return {"error": "empty message"}
        if self.is_check(text) and not self._auto_done.is_set():
            # An automatic turn is running. If it is a check of this same board,
            # its answer is this check's answer (no second reply).
            merge = self._auto_current == "check"
            self._auto_done.wait(60)
            if merge and self._auto_last is not None:
                return dict(self._auto_last, merged=True)
        with self.agent_lock:
            if self.waiting_for_power_answer():
                # A new message instead of Yes or No: that is a no.
                self._turn("No", lambda: self.replies.answer_power(self.agent, self.chat, False),
                           resume=True)
            elif self.waiting_for_experiment():
                # A new message instead of "done": the student could not do it.
                self._turn(self.waiting_for_experiment()["cant_label"],
                           lambda: self.replies.answer_experiment(self.agent, self.chat, False),
                           resume=True)
            return self._turn(text, lambda: self.replies.ask(self.agent, self.chat, text))

    def answer_power(self, yes):
        """The student's Yes or No to "Turn the power back on?"."""
        with self.agent_lock:
            if not self.waiting_for_power_answer():
                return {"error": "Zip is not asking about the power right now"}
            return self._turn("Yes" if yes else "No",
                              lambda: self.replies.answer_power(self.agent, self.chat, bool(yes)),
                              resume=True)

    def _turn(self, text, run, resume=False, auto=False, silent=False):
        """Run one tutor reply (or the rest of a paused one) and log it.
        auto: an automatic check started by instant help (tagged on the page).
        silent: a guide turn (lights and the board screen only, no chat)."""
        if not silent:
            self.log.append({"who": "student", "text": text, "time": time.time(), "auto": auto})
        if not resume:
            self.activity.reply()
            self._turn_start = (self.session.lesson_id, self.session.last_check)
            self.session.last_logged = None
        before, check_before = getattr(self, "_turn_start", (self.session.lesson_id, None))
        try:
            reply, new = run()
        except Exception as e:  # show the problem on the page, keep running
            reply, new = f"Sorry, the tutor hit an error: {e}", []
            self.replies.forget_pause(self.agent)
        self._collect()  # every step of this reply, before the next one starts
        # The offline tutor answered because Claude failed or was slow.
        offline = self.replies.offline
        tools = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
        steps = describe_steps(self.session.trace, self.student_name())
        experiment = self.waiting_for_experiment()
        if experiment is not None:
            # Paused: Zip asks the student to do one thing (the experiment card).
            self.log.append({"who": "tutor", "text": reply, "time": time.time(), "steps": steps,
                             "tools": tools, "mood": "curious", "experiment": experiment,
                             "celebrate": False, "offline": offline, "auto": auto})
            return {"reply": reply, "tools": tools, "pending": True, "experiment": experiment,
                    "offline": offline}
        if silent and not self.waiting_for_power_answer():
            return {"reply": reply, "tools": tools, "silent": True}
        if self.waiting_for_power_answer():
            # Paused: Zip asks before the power comes back on.
            self.log.append({"who": "tutor", "text": reply, "time": time.time(), "steps": steps,
                             "tools": tools, "mood": "curious", "asks_power": True,
                             "celebrate": False, "offline": offline})
            return {"reply": reply, "tools": tools, "pending": True, "offline": offline}
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
                         "celebrate": bool(result and result.get("status") == "ok"),
                         "offline": offline, "auto": auto})
        if self.is_sim and self.session.lesson_id != before:
            # New lesson: the pretend student starts with a correct build, or,
            # with instant help, with an empty board (the student clears it).
            self.board.send(f"SCENARIO {self.session.lesson_id} correct")
            if self.instant:
                self.board.send("BUILD 0")
        return {"reply": reply, "tools": tools, "offline": offline}

    # --- Free build ------------------------------------------------------------
    def _guess_view(self):
        """For the page: the circuit reader's guess from the latest check
        (part words, and a check mark when the physics check matched it)."""
        s = self.session
        if not s.is_free() or not s.last_check:
            return None
        result = s.last_check[1] or {}
        guess = result.get("circuit_guess")
        if not guess:
            return None
        return {"parts": [{"words": p["words"], "ok": bool(p.get("ok")), "sure": p["sure"]}
                          for p in guess["parts"]],
                "score": result.get("guess_score"), "matched": result.get("guess_matched"),
                "not_sure": (result.get("main_problem") or {}).get("kind") == "not_sure"}

    def netlist(self, parts):
        """Fake board only: the pretend student built exactly these parts."""
        if not self.is_sim:
            return {"error": "only the fake board can build a netlist"}
        try:
            with self.board.lock:
                self.raw_board.set_netlist(parts)
        except (NetlistError, ValueError) as e:
            return {"error": str(e)}
        bad = sorted({r for p in self.raw_board.netlist for r in p["rows"]
                      if is_row(r) and not self.board.is_usable(r)})
        return {"ok": True, "parts": len(self.raw_board.netlist),
                **({"not_usable": bad} if bad else {})}

    def random_circuit(self, seed=None, parts=3):
        """Fake board only: a random judge circuit on usable rows. The answer
        carries the true netlist for testing: it never goes to the tutor."""
        if not self.is_sim:
            return {"error": "only the fake board can build a random circuit"}
        try:
            n = int(parts)
            if not 2 <= n <= 5:
                raise ValueError
        except (TypeError, ValueError):
            return {"error": "parts must be 2 to 5"}
        try:
            made = random_circuit(seed, n, self.board.usable_rows)
        except ValueError as e:
            return {"error": str(e)}
        with self.board.lock:
            self.raw_board.set_netlist(made["parts"])
        return {"ok": True, "seed": made["seed"], "mistake": made["mistake"],
                "netlist": made["parts"], "note": "for testing only: never sent to the tutor"}

    def set_tray(self, parts, goal=""):
        """The parts tray: what the student says they used, and the goal."""
        try:
            self.tray = clean_tray(parts, goal)
        except ValueError as e:
            return {"error": str(e)}
        self.session.tray = self.tray
        return {"ok": True, "tray": self.tray}

    def hold(self, on):
        """Fake board only: hold (or let go of) the push button on the
        breadboard, like the student's finger (lesson 4)."""
        if not self.is_sim:
            return {"error": "only the fake board has a pretend button to hold"}
        return self.board.send(f"HOLD {'on' if on else 'off'}")

    def build(self, scenario, place=None, copies=None, rows=None, parts=None):
        """Fake board only: change the circuit. place, like "T 15" or "B 0",
        builds it somewhere else on the board (None: leave it where it is).
        copies, like ["T 0", "T 15"] or ["T 0", "T20/T27/T29"], builds the
        circuit at each place (the scenario is the last copy; the others are
        correct). rows, like ["T20", "T27", "T29"] (the lesson's rows in
        order), builds it on those rows: any spacing."""
        if not self.is_sim:
            return {"error": "only the fake board can change circuits"}
        # Build part by part (only the first n parts are in), or, when a
        # practice circuit is picked, the whole of it.
        answer = self.board.send(f"BUILD {'ALL' if parts in (None, 'all') else parts}")
        if answer.get("type") == "error":
            return answer
        if rows:
            if not isinstance(rows, list):
                return {"type": "error", "msg": "rows must be a list like [\"T20\", \"T27\", \"T29\"]"}
            answer = self.board.send("MAP " + " ".join(str(r) for r in rows))
            if answer.get("type") == "error":
                return answer
        if place:
            answer = self.board.send(f"PLACE {place}")
            if answer.get("type") == "error":
                return answer
        if copies:
            if not isinstance(copies, list):
                return {"type": "error", "msg": "copies must be a list like [\"T 0\", \"T 15\"]"}
            answer = self.board.send("COPIES " + " ".join(str(c).replace(" ", "")
                                                          for c in copies))
            if answer.get("type") == "error":
                return answer
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
                self._send(200, app.build(str(body.get("scenario", "")),
                                          body.get("place") and str(body["place"]),
                                          body.get("copies"), body.get("rows"),
                                          body.get("parts")))
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
            elif self.path == "/api/instant":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.set_instant(bool(body.get("on"))))
            elif self.path == "/api/experiment":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.answer_experiment(bool(body.get("done"))))
            elif self.path == "/api/hold":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.hold(bool(body.get("on"))))
            elif self.path == "/api/netlist":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.netlist(body.get("parts")))
            elif self.path == "/api/random-circuit":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.random_circuit(body.get("seed"), body.get("parts", 3)))
            elif self.path == "/api/tray":
                if not isinstance(body, dict):
                    return self._send(400, {"error": "bad request"})
                self._send(200, app.set_tray(body.get("parts", []), body.get("goal", "")))
            elif self.path == "/api/restart-demo":
                self._send(200, app.restart_demo())
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass  # keep the terminal quiet (the page polls 4x a second)

    return Handler


DEMO_STUDENT = "Maya"
DEMO_LESSON = 1


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
        try:
            return FakeBoard(args.lesson if args.lesson is not None else 1, args.scenario,
                             rows=args.rows, has_current=not args.no_current,
                             has_power_switch=not args.no_power_switch,
                             has_screen=not args.no_screen, supply_v=args.supply)
        except ValueError as e:
            print(f"Could not start the fake board: {e}")
            return None
    try:
        port = choose_port(args.serial)
        print(f"Connecting to the board on {port} at {args.baud} baud...")
        return SerialBoard(port, args.baud)
    except BoardError as e:
        print(f"Could not use the real board. {e}")
        return None


def tracing_line(environ=None):
    """One line for the start-up message when LangSmith tracing is asked for
    (LANGSMITH_TRACING=true), else None. LangChain reads the variables by
    itself: this only says so. Never stops the server."""
    environ = os.environ if environ is None else environ
    if str(environ.get("LANGSMITH_TRACING", "")).strip().lower() not in ("true", "1", "yes"):
        return None
    try:
        import langsmith  # noqa: F401  (comes with langchain)
    except ImportError:
        return ("LangSmith tracing was asked for, but the langsmith library is not installed. "
                "Run  pip install langsmith  to use it. Zip works the same without it.")
    line = f"LangSmith tracing: on (project {environ.get('LANGSMITH_PROJECT') or 'default'})"
    if not environ.get("LANGSMITH_API_KEY"):
        line += ". LANGSMITH_API_KEY is not set, so nothing can be sent."
    return line


def usable_rows_setting(flag, environ=None):
    """The usable rows: --usable-rows if given, else SMART_BOARD_USABLE_ROWS,
    else None (every row)."""
    environ = os.environ if environ is None else environ
    return flag if flag is not None else (environ.get(USABLE_ROWS_ENV) or None)


def make_server(app, port=8000, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), make_handler(app))


def main(argv=None):
    p = argparse.ArgumentParser(description="Genesis: the smart breadboard web page")
    p.add_argument("--no-instant", action="store_true",
                   help="turn instant help off (Zip then reacts only when you check)")
    p.add_argument("--demo", action="store_true",
                   help='start the demo: student "Maya", lesson 1, fresh history and notebook')
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lesson", type=int, default=None,
                   help="lesson to start at (default: lesson 0 for a new student)")
    p.add_argument("--scenario", default="correct", help="fake board starting circuit")
    p.add_argument("--history", default=None,
                   help="file that keeps the mistake history (default: one per student)")
    p.add_argument("--student", default=None,
                   help="the student's name: their notebook is notebooks/<name>.md")
    p.add_argument("--notebooks", default=str(ROOT / "notebooks"),
                   help="folder for the student notebooks")
    p.add_argument("--fresh", action="store_true", help="forget the old mistake history")
    p.add_argument("--rows", default="all", help='rows the fake board measures, e.g. "T1-T16"')
    p.add_argument("--usable-rows", default=None,
                   help='rows that work on this board, e.g. "T3-T8,T20-T24,B40-B45" (default: '
                        f'all, or the {USABLE_ROWS_ENV} environment variable)')
    p.add_argument("--free-build", action="store_true",
                   help="free build: build anything, no plan (after lesson 4 in the path)")
    p.add_argument("--no-current", action="store_true", help="fake board has no current sensor")
    p.add_argument("--no-power-switch", action="store_true",
                   help="fake board cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="fake board has no screen")
    p.add_argument("--supply", type=float, default=SUPPLY_V,
                   help=f"fake board supply voltage (default {SUPPLY_V:g} V, like the station)")
    p.add_argument("--serial", metavar="PORT",
                   help='use a real board on this port (COM3, /dev/ttyUSB0) or "auto"')
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   help=f"serial speed for --serial (default {DEFAULT_BAUD})")
    args = p.parse_args(argv)
    args.usable_rows = usable_rows_setting(args.usable_rows)
    try:
        usable = parse_usable(args.usable_rows)
    except ValueError as e:
        print(f"--usable-rows: {e}")
        return 2
    if args.free_build and args.lesson is None:
        args.lesson = FREE_ID  # start in free build
    if args.demo:
        # One command for the demo: Maya, lesson 1, a fresh start.
        args.student = args.student or DEMO_STUDENT
        args.lesson = DEMO_LESSON if args.lesson is None else args.lesson
        args.fresh = True
    args.student = args.student or "student"
    if args.history is None:
        args.history = str(default_history(args.student))

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()
    board = open_board(args)
    if board is None:
        return 2
    notebook = Notebook(args.notebooks, args.student)
    if args.demo:
        notebook.start_over()  # a fresh notebook; the old one is kept with the date in its name
    demo = {"student": DEMO_STUDENT, "lesson": DEMO_LESSON,
            "history": str(default_history(DEMO_STUDENT))}
    app = App(board, lesson="start" if args.lesson is None else args.lesson,
              history_path=args.history, notebook=notebook,
              confirm_power=True, log=lambda line: print(line, flush=True), demo=demo,
              instant=not args.no_instant, usable_rows=usable, free_build=args.free_build)
    if app.is_sim and app.session.lesson_id != board.lesson:
        # A new student starts at lesson 0: the pretend board shows that lesson.
        board.send(f"SCENARIO {app.session.lesson_id} correct")
        app._welcome()
    server = make_server(app, args.port)
    caps = app.board.capabilities()
    where = f"real board on {board.port}" if args.serial else "fake board"
    if app.board.usable is not None:
        print(f"Usable rows: {args.usable_rows} ({len(app.board.usable_rows)} rows).")
    if tracing_line():
        print(tracing_line())
    print(f"Tutor: {app.model_name}. Board: {where}, {caps['rows']} rows measured, "
          f"current sensor {'yes' if caps['has_current'] else 'no'}, "
          f"power switch {'yes' if caps['has_power_switch'] else 'no'}, "
          f"supply {caps['supply_v']:g} V.")
    print(f"Notebook for {args.student}: {notebook.path}")
    if app.model_name == "offline practice tutor":
        print("No ANTHROPIC_API_KEY set: Zip uses the offline tutor for every answer.")
    else:
        print(f"Each Claude reply prints how long it took. After {app.timeout:g} seconds, or "
              f"with no internet, the offline tutor answers that message instead.")
    if args.demo:
        print(f"DEMO READY: student {args.student}, lesson {app.session.lesson_id}, fresh start.")
        print(f"Demo URL: http://localhost:{args.port}   (press F11 in the browser for full screen)")
        print("Press Ctrl+C here to stop.")
    else:
        print(f"Open http://localhost:{args.port} in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
