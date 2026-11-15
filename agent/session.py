"""One student's session: the board, the current lesson, and the history.

Everything here is plain Python and gives the same answer every time.
The AI decides *how to talk* to the student. The rules that must never
go wrong (power off on danger, hints before answers, picking the next
lesson, what the board screen says when the student must act) live
here, so a confused model cannot break them.
"""

import json
import re
import time
from pathlib import Path

from agent.boardlink import BoardLink
from agent.deadline import RunAbandoned
from agent.diagnoser import compare_with_rules, lights_from, validate_diagnosis
from agent.lessons import SAFETY_MAX_MA, calculate_current, check, load_lessons, summarize
from agent.notebook import entry_start, mistake_label
from agent.physics import circuit_facts, extra_rows_fact, number, power_fact
from agent.placement import (HOME, ROW, describe, driven_rows, find_all_placements,
                             find_placement, is_row_map, shift_lesson, shift_name)
from protocol import is_row, power_cut_words, row_label

WINDOW = 10  # readings per measurement (about 1 second on the fake board)
REPEAT_IF_SEEN = 2  # same mistake this many times -> practice the lesson again

# Board screen text when the student must switch power off by hand
# (a board with no power switch). fit_screen keeps it to the screen size.
MANUAL_OFF_SCREEN = "DANGER!|Switch the power OFF|by hand now.|Then fix the circuit."
NO_SWITCH_SCREEN = "No power switch.|Use the switch on|the power supply."
STILL_UNSAFE_SCREEN = "Still unsafe!|Power is off again.|Fix the circuit|first."

# A student message that asks for power to come back on: they say they
# fixed something, or ask for the power. Only then may the AI turn power
# on (button 4 always may). Every new message replaces the answer.
ASKS_FOR_POWER = re.compile(
    r"\b(fix(ed|es)?|repair(ed)?|added|replaced|swapped|changed|moved|flipped"
    r"|turned (it|the \w+) around|(turn|switch|put) (the )?power (back )?on"
    r"|power (back )?on)\b", re.IGNORECASE)


def _short(name):
    """Very short row name for the small screen: T10 -> "row 10"."""
    return {"PT+": "top +", "PT-": "top -", "PB+": "bottom +",
            "PB-": "bottom -"}.get(name, row_label(name).replace(" (lower half)", "b"))


class Session:
    def __init__(self, board, lesson=1, history_path=None, lessons=None, notebook=None):
        self.board = board if isinstance(board, BoardLink) else BoardLink(board)
        self.lessons = lessons or load_lessons()
        self.history_path = Path(history_path) if history_path else None
        self.history = {"mistakes": [], "passes": {}, "quiz": []}
        if self.history_path and self.history_path.exists():
            self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.history.setdefault("quiz", [])  # history files from before quizzes
        # Mini-lessons the lesson writer wrote (and Python accepted), by the
        # occurrence of the mistake they are for: {"3": {...lesson...}}.
        self.history.setdefault("ai_lessons", {})
        # lesson="start": a new student starts at lesson 0 ("How a breadboard
        # connects"); a returning one at the first lesson not passed yet.
        self.lesson_id = self.start_lesson() if lesson == "start" else lesson
        self.passed_current = False
        self.step = -1  # building: -1 = not started; else index into layout
        # May the AI turn power on? Only while handling a student message
        # that asks for it (see hear()). Button 4 does not need this.
        self.restore_allowed = False
        # The most recent mistake not yet fixed, and the help given for it:
        # {"lesson", "mistake", "level": "hint" | "explain"}. The hint button
        # uses it; a correct check or a new lesson clears it.
        self.open_mistake = None
        # The most recent log_mistake call: {"lesson", "mistake", "occurrence"}.
        # The web page uses it to show the matching mini-lesson card.
        self.last_logged = None
        # The student's notebook (agent/notebook.py), or None for no notebook.
        self.notebook = notebook
        if notebook is not None:
            notebook.ensure()
        # The latest check: (summary, result). The detective looks at it.
        self.last_check = None
        # What Zip did in the current reply, as it happens (agent/trace.py).
        self.trace = []
        # The mistake a new mini-lesson is wanted for (set by plain Python:
        # the same mistake a second time, or its quiz answered wrong), and the
        # occurrence a lesson was just accepted for (for the page's card).
        self.lesson_request = None
        self.new_ai_lesson = None
        # The facts of the latest check, waiting to be written in the
        # notebook (by the agent, or by notebook_after_turn if it forgot).
        self.pending_entry = None
        # Where the student built the circuit (agent/placement.py): HOME is
        # the rows the lesson file names. measure() moves it when the same
        # circuit is clearly built somewhere else on the board.
        self.placement = HOME
        self._shifted = {}
        # A lesson with "states" (lesson 4, the button): which step the student
        # is on, "pressed" (hold the button) then "released" (let go); None
        # for other lessons. press_reminded: the first "nothing flows" at the
        # pressed step only reminds the student to hold the button.
        self.button_step = self._first_step(self.lesson_id)
        self.press_reminded = False
        # The AI diagnoser for this reply: a function payload -> Diagnosis,
        # set by agent/build.py when the tutor is Claude; None offline.
        self.diagnoser = None

    # --- Lessons -------------------------------------------------------
    @property
    def lesson(self):
        """The current lesson, with its rows moved to where the student built it."""
        key = (self.lesson_id, tuple(self.placement))
        if key not in self._shifted:
            self._shifted[key] = shift_lesson(self.lessons[self.lesson_id], self.placement)
        return self._shifted[key]

    def lesson_info(self):
        l = self.lesson
        info = {"id": l["id"], "title": l["title"], "circuit": l["circuit"],
                "rows": list(l["rows"]), "parts": [p["part"] for p in l["layout"]]}
        if self.button_step:
            info["button_step"] = self.button_step
        return info

    # --- Lessons with steps (lesson 4: press, then let go) -------------------
    PRESS = "Press and hold the button, then check."
    LET_GO = "Now let go of the button and check again."

    def _first_step(self, lesson_id):
        return "pressed" if self.lessons.get(lesson_id, {}).get("states") else None

    def _step_lesson(self, lesson):
        """The lesson as the current step checks it: that step's accept rules
        and expected numbers, and only the mistakes that belong to that step
        (or to every step)."""
        state = lesson["states"][self.button_step]
        return dict(lesson, accept=state["accept"], expected=state.get("expected", lesson["expected"]),
                    check_step=self.button_step,
                    mistakes=[m for m in lesson["mistakes"]
                              if m.get("step") in (None, self.button_step)])

    @staticmethod
    def _current_flows(readings, placement, lesson):
        """Does current flow (lesson 4: the button closes the loop)? With a
        current sensor: more than 1 mA. Without: every lesson row, at that
        place, has something connected (a let-go button leaves rows empty)."""
        on = [r for r in readings if r.get("power_on")]
        currents = [r.get("current_ma") for r in on]
        if currents and all(c is not None for c in currents):
            return sum(currents) / len(currents) > 1.0
        driven = driven_rows(on)
        return bool(on) and all(shift_name(n, placement) in driven for n in lesson.get("rows", []))

    def _button_steps(self, result):
        """Walk the steps: pressed ok -> let go next; released ok -> the lesson
        passes. A mistake keeps the step. The first "nothing flows" at the
        pressed step is most likely a button that is not held yet, so it is a
        reminder, not a mistake; the second one in a row is the mistake."""
        step = self.button_step
        if step == "pressed" and result.get("mistake_id") == "button_not_connecting" \
                and not self.press_reminded:
            self.press_reminded = True
            result = {"status": "press_first",
                      "message": "No current is flowing yet. Press and hold the button down, "
                                 "and keep holding it while I check."}
        elif result.get("mistake_id") != "button_not_connecting":
            self.press_reminded = False
        if step == "pressed" and result["status"] == "ok":
            self.button_step = "released"
            result = {"status": "step_ok", "next_step": self.LET_GO}
        result["step"] = step
        if result["status"] != "ok" and "next_step" not in result:
            result["next_step"] = self.PRESS if self.button_step == "pressed" else self.LET_GO
        return result

    def set_lesson(self, lesson_id):
        if lesson_id != self.lesson_id:
            # A new lesson starts with dark lights: old green or red lights
            # would point at rows that mean something else now.
            self.board.clear_lights()
            self.open_mistake = None
        if lesson_id != self.lesson_id:
            self.placement = HOME  # a new lesson: guide the student to its own rows
        self.lesson_id = lesson_id
        self.passed_current = False
        self.step = -1
        self.button_step = self._first_step(lesson_id)  # lesson 4: press first
        self.press_reminded = False

    # --- What the student said -----------------------------------------
    def hear(self, text):
        """Called with every student message, before the AI sees it.
        Decides, in plain Python, whether this message asks for power."""
        self.restore_allowed = bool(ASKS_FOR_POWER.search(text or ""))

    # --- Measuring -----------------------------------------------------
    def measure(self):
        """Take a window of readings and check the circuit.

        Safety does not wait for the AI:
        - a measured current above SAFETY_MAX_MA cuts power at once
          (when the board has a power switch);
        - a dangerous mistake found from the rows cuts power too, or, on a
          board with no power switch, puts "switch the power off by hand"
          on the board screen at once.
        Returns (summary, result, safety_cut).
        """
        board = self.board
        board.poll()
        cuts_before = len(board.power_cuts)
        readings = []
        safety_cut = False
        for _ in range(WINDOW):
            r = board.read()
            readings.append(r)
            current = r.get("current_ma")
            if (r["power_on"] and current is not None and current > SAFETY_MAX_MA
                    and board.has_power_switch):
                board.cut()
                safety_cut = True
                break
        board.poll()
        # Judge the circuit from readings taken while power was on. If the
        # board switched power off by itself (a power_cut line), say so, and
        # if no reading here was on, use the ones from just before the cut:
        # they show what the student built.
        on = [r for r in readings if r["power_on"]]
        board_cut = None
        cut = board.last_cut
        if len(board.power_cuts) > cuts_before:
            board_cut = cut  # the board cut power during this measurement
        elif cut is not None and not cut.get("explained") and not on:
            # The board cut power earlier (between checks) and nobody has
            # explained it yet. Use the readings from just before the cut,
            # once: after that the student may have changed the circuit.
            board_cut = cut
            if board.before_cut:
                on = list(board.before_cut)
        moved = None
        data = on or readings
        lesson_file = self.lessons[self.lesson_id]
        copies = []
        if lesson_file.get("copies") and not self.is_explore():
            # A lesson with copies (lesson 3: 1 to 3 LED circuits): find every
            # copy, anywhere on the board, and check each one.
            copies = find_all_placements(data, lesson_file, lesson_file["copies"].get("max", 1))
            if copies and tuple(copies[0]) != tuple(self.placement):
                self.placement = copies[0]  # the drawing shows the first copy
                board.clear_lights()
        elif not self.is_explore() and self.button_step != "released":
            # Built the same circuit somewhere else? Then check it there. (With
            # the button let go no current flows, so a "released" check keeps
            # the place found while the button was held.)
            new = find_placement(data, lesson_file, self.placement)
            if self.button_step and not self._current_flows(data, new, lesson_file):
                new = self.placement  # the button is not closing the loop yet: keep the place
            if tuple(new) != tuple(self.placement):
                self.placement = new
                board.clear_lights()  # old lights point at the old rows
                if tuple(new) != HOME:  # back where the lesson shows: nothing to say
                    moved = describe(new, lesson_file)
        if copies:
            summary, result = self._check_copies(data, lesson_file, copies)
        else:
            summary = summarize(data, self.lesson)
            result = check(summary, self._step_lesson(self.lesson) if self.button_step
                           else self.lesson)
        if not self.is_explore() and result["status"] != "power_off":
            result = self._extra_parts(data, result, copies)
        if self.button_step and result["status"] != "power_off":
            result = self._button_steps(result)
        if moved:
            if is_row_map(self.placement):  # any spacing
                result["moved"] = (f"You built it in {moved}, with the parts spaced "
                                   "differently from the lesson. That is fine: I am "
                                   "checking it there.")
            else:
                result["moved"] = (f"You built it in {moved}, not where the lesson shows. "
                                   "That is fine: I am checking it there.")
        if result["status"] == "ok" and self.is_explore():
            result["explore"] = True  # lesson 0: nothing was built, the quiz was answered
        if result["status"] == "ok" and self.is_explore() and not self.intro_answered():
            # Lesson 0 has no circuit: it passes once its question is answered.
            result = {"status": "quiz_first",
                      "message": "Before we go on: answer the question about which holes "
                                 "are joined. Press Learn on the laptop to see it."}
        if board_cut is not None:
            board_cut["explained"] = True
            result["board_cut"] = {"reason": board_cut.get("reason"),
                                   "current_ma": board_cut.get("current_ma"),
                                   "say": power_cut_words(board_cut)}
            safety_cut = True  # power is off: the board did it
        if result["status"] == "ok":
            self.passed_current = True
            self.open_mistake = None  # nothing left to give hints about
        if result.get("action") == "cut_power" and summary["power_on"] and not safety_cut:
            if board.has_power_switch:
                board.cut()
                safety_cut = True
            else:
                result["manual_power_off"] = True
                board.show(MANUAL_OFF_SCREEN)
        if self.diagnoser is not None and not self.is_explore():
            # Claude is available: physics facts + the diagnoser. Only after
            # the safety cut above, so a slow answer never delays it.
            result = self._diagnose(data, summary, result, copies, safety_cut)
        if safety_cut:
            summary["power_on"] = False  # it is off now
        self.last_check = (summary, result)
        entry = entry_start(self.lesson_id, result, self.lesson)
        if entry is not None:
            self.pending_entry = entry
        return summary, result, safety_cut

    # --- The AI diagnoser (agent/physics.py, agent/diagnoser.py) ---------------
    NO_DIAGNOSIS = ("power_off", "missing_rows", "quiz_first")

    def _diagnose(self, readings, summary, result, copies, safety_cut):
        """Physics facts from the measurements, then the diagnoser's answer,
        checked by plain Python. The rule check's status (and so PASS, the
        lights' meaning and the power) is not changed; this adds
        result["facts"], ["diagnosis"], ["lights"] and ["rule_match"]."""
        lesson_file = self.lessons[self.lesson_id]
        if copies:
            lessons = [shift_lesson(lesson_file, p) for p in copies]
        else:
            lessons = [self._step_lesson(self.lesson) if self.button_step else self.lesson]
        facts = [power_fact(summary, safety_cut)]
        if summary.get("power_on") or safety_cut:
            for i, lesson in enumerate(lessons):
                s = summarize(readings, lesson) if copies else summary
                label = f"LED circuit {i + 1}" if len(lessons) > 1 else ""
                facts += circuit_facts(s, lesson, label)
            if result.get("extra_rows"):
                facts.append(extra_rows_fact(result["extra_rows"]))
        number(facts)
        result["facts"] = facts
        self.trace.append({"who": "physics", "tool": "facts", "args": {"count": len(facts)}})
        if result["status"] in self.NO_DIAGNOSIS:
            return result
        valid_rows = {f_row for f in facts for f_row in f["rows"]}
        for lesson in lessons:
            valid_rows |= set(lesson.get("rows", []))
            valid_rows |= {leg["row"] for part in lesson.get("layout", []) for leg in part["legs"]}
        payload = {
            "lesson": {k: lessons[0].get(k) for k in ("id", "title", "circuit", "rows", "layout",
                                                      "expected")},
            "copies": len(lessons) if copies else None,
            "step": result.get("step"),
            "facts": facts,
            "rule_match": result.get("mistake_id"),
            "rule_status": result["status"],
            "recent_mistakes": [m["mistake"] for m in self.history["mistakes"][-3:]],
        }
        self.trace.append({"who": "diagnoser", "tool": "diagnose"})
        try:
            raw = self.diagnoser(payload)
        except RunAbandoned:
            raise  # this reply was given up on: stop here
        except Exception as e:  # no answer: the check goes on as before
            result["diagnosis_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            return result
        accept_passed = result["status"] in ("ok", "step_ok")
        diagnosis, dropped = validate_diagnosis(raw, facts, valid_rows, accept_passed)
        self.trace.append({"who": "diagnoser", "tool": "validate",
                           "args": {"kept": len(diagnosis["causes"]), "dropped": dropped}})
        result["diagnosis"] = diagnosis
        result["lights"] = lights_from(diagnosis)
        result["rule_match"] = compare_with_rules(result, diagnosis)
        if not result["rule_match"]["agrees"]:
            self.trace.append({"who": "diagnoser", "tool": "compare",
                               "args": {"rule": result.get("mistake_id") or result["status"]}})
        return result

    def _check_copies(self, readings, lesson, places):
        """Check every copy of a lesson with "copies" (lesson 3).

        Each copy is checked with its own row voltages. With one copy the
        current is the board's current, exactly like a lesson without
        copies. With more, each copy's current is worked out from its own
        resistor (Ohm's law); then the board's total current must be
        N x the accepted range for one copy. If it is not, but every copy
        looks right by its voltages, each copy is checked again with the
        total current / N (they all look the same, so they share it).
        Returns (summary of all copies, result)."""
        n = len(places)
        shifted = [shift_lesson(lesson, p) for p in places]
        merged = summarize(readings, {"rows": [r for l in shifted for r in l["rows"]]})
        measured = merged["current_source"] == "measured"
        total = merged["current_ma"] if measured else None

        def check_all(share=None):
            out = []
            for l in shifted:
                s = summarize(readings, l)
                if share is not None:
                    s.update(current_ma=share, current_source="measured",
                             current_note="the board's current, shared by the copies")
                elif n > 1:
                    value, note = calculate_current(s["rows"], s["floating"],
                                                    l.get("current_from", []))
                    s.update(current_ma=value, current_note=note,
                             current_source="calculated" if value is not None else "unknown")
                out.append((s, check(s, l)))
            return out

        checked = check_all()
        per_copy = lesson.get("accept", {}).get("current_ma")
        fits_total = (total is None or per_copy is None
                      or n * per_copy.get("min", 0) <= total <= n * per_copy.get("max", 1e9))
        shared = n > 1 and not fits_total and all(r["status"] == "ok" for _, r in checked)
        if shared:
            checked = check_all(share=round(total / n, 3))
        if not measured:  # no current sensor: add up what each copy's resistor says
            parts = [s["current_ma"] for s, _ in checked]
            if parts and all(c is not None for c in parts):
                merged.update(current_ma=round(sum(parts), 3), current_source="calculated",
                              current_note="calculated for each LED circuit and added up")

        # The result: the most urgent copy (a power cut first), else all ok.
        rank = {"power_off": 0, "missing_rows": 1, "mistake": 2, "unknown": 3, "ok": 4}
        order = sorted(range(n), key=lambda i: (
            rank.get(checked[i][1]["status"], 3),
            checked[i][1].get("action") != "cut_power", i))
        worst = checked[order[0]][1]
        if worst["status"] == "ok":
            # With a current sensor the total current was checked above (a
            # too-big resistor shows there), so nothing was left unchecked.
            not_checked = [] if measured else sorted(
                {m for _, r in checked for m in r.get("not_checked", [])})
            result = {"status": "ok", **({"not_checked": not_checked} if not_checked else {})}
        else:
            result = dict(worst)
            if shared and worst["status"] == "mistake":
                # Every copy looked the same, so the board cannot tell which one
                # it is: light the rows of every copy with this mistake.
                result["rows"] = [row for _, r in checked
                                  if r.get("mistake_id") == worst["mistake_id"]
                                  for row in r.get("rows", [])]
        result["count"] = n
        result["copies"] = [{"rows": describe(p, lesson), "status": r["status"],
                             "mistake_id": r.get("mistake_id")}
                            for p, (_, r) in zip(places, checked)]
        where = " and ".join(c["rows"] for c in result["copies"])
        result["found"] = (f"I found {n} LED circuit{'s' if n != 1 else ''}: {where}."
                           if n else "")
        return merged, result

    def _extra_parts(self, readings, result, copies):
        """Something plugged into a row that is not in the plan (any lesson
        but lesson 0): those rows light yellow, no power cut. When the check
        already found a mistake, that mistake stays (a power cut must not be
        hidden) and the extra rows are added for the tutor to mention first."""
        plan = set()
        for l in ([shift_lesson(self.lessons[self.lesson_id], p) for p in copies]
                  or [self.lesson]):
            plan |= set(l.get("rows", []))
            plan |= {leg["row"] for part in l.get("layout", []) for leg in part["legs"]}
        extra = sorted((n for n in driven_rows(readings) if ROW.match(n) and n not in plan),
                       key=lambda n: (n[0] != "T", int(n[1:])))
        if not extra:
            return result
        if result["status"] in ("ok", "unknown"):
            keep = {k: result[k] for k in ("count", "copies", "found") if k in result}
            result = {"status": "mistake", "mistake_id": "extra_part", "action": "hint",
                      "rows": extra, "light": "yellow", **keep}
        result["extra_rows"] = extra
        result["extra_say"] = self._extra_words(extra)
        return result

    @staticmethod
    def _extra_words(rows):
        words = [row_label(r) for r in rows]
        if len(words) == 1:
            where, verb = words[0], "is"
        else:
            where = "rows " + ", ".join(w.replace("row ", "") for w in words[:-1]) \
                    + " and " + words[-1].replace("row ", "")
            verb = "are"
        return (f"Something is plugged into {where}, which {verb} not in the plan. "
                "Take out any part that is not in the plan.")

    def _extra_part_spec(self):
        """The "extra_part" mistake (not in the lesson files: it can happen in
        every lesson). The hint names the rows of the latest check."""
        rows = ((self.last_check or ({}, {}))[1] or {}).get("extra_rows") or []
        hint = (self._extra_words(rows) if rows else
                "Something is plugged into a row that is not in the plan. Take out any "
                "part that is not in the plan.")
        return {"id": "extra_part", "description": "Something is plugged into a row that is "
                                                   "not in the plan",
                "rows": rows, "action": "hint", "light": "yellow", "hint": hint,
                "explain": "Every part in the plan has its own rows. A part in any other row "
                           "is not part of this circuit: it may be a part you forgot to take "
                           "out, and it can give the current a path it should not take. Take "
                           "out any part that is not in the plan, then check again.",
                "mini_lesson": {
                    "title": "Every part needs a job",
                    "idea": "A circuit is like a team where every player has a job. A part "
                            "that is not in the plan has no job in this circuit. It can even "
                            "give the current a new path to take.",
                    "question": "A part sits in a row that is not in the plan. What can it do?",
                    "options": ["Make the LED change color",
                                "Give the current a path it should not take",
                                "Make the power last longer"],
                    "answer": 1,
                    "why": "Any part that touches the circuit can give the current a new "
                           "path, even if you did not mean it to."}}

    # --- Notebook --------------------------------------------------------
    NEXT_TIME = {"mistake": "Next time: look at the rows that light red first.",
                 "ok": "Next time: build the next lesson the same careful way.",
                 "unknown": "Next time: compare each part with the lesson plan, one at a time."}

    def notebook_task(self):
        """For the agent: where to write the entry for the latest check."""
        if self.notebook is None or self.pending_entry is None:
            return None
        from agent.notebook import MARKER
        return {"file": self.notebook.agent_path, "entry": self.pending_entry,
                "add_above": MARKER}

    def notebook_after_turn(self):
        """After every turn: if a check happened and the agent did not write
        its notebook entry, write it here, so the notebook never misses one."""
        entry, self.pending_entry = self.pending_entry, None
        if self.notebook is None or entry is None or self.notebook.has_entry(entry):
            return
        status = (self.last_check or ({}, {}))[1].get("status")
        self.notebook.add(f"{entry} {self.NEXT_TIME.get(status, '')}".rstrip())

    # --- Building step by step ------------------------------------------
    def next_step(self):
        """Show where the next part goes: its rows light blue and the
        screen names the part. Button 3."""
        layout = self.lesson["layout"]
        self.step = min(self.step + 1, len(layout))
        self.board.clear_lights()
        if self.step >= len(layout):
            self.board.show("All parts placed!|Press 1 to check.")
            return {"done": True, "step": len(layout), "of": len(layout)}
        part = layout[self.step]
        rows = [leg["row"] for leg in part["legs"] if is_row(leg["row"])]
        self.board.set_lights(rows, "blue")
        legs = part["legs"]
        if any("(" in leg["leg"] for leg in legs):  # a part with + and - legs
            where = [f"{leg['leg'].split(' (')[0]}: {_short(leg['row'])}" for leg in legs]
        else:
            where = [f"{_short(legs[0]['row'])} and {_short(legs[1]['row'])}"]
        screen = self.board.show("|".join([f"Step {self.step + 1} of {len(layout)}",
                                           part["part"], *where]))
        return {"done": False, "step": self.step + 1, "of": len(layout),
                "part": part["part"], "legs": legs, "rows": rows,
                "screen": screen.get("lines", [])}

    # --- Power ----------------------------------------------------------
    def turn_power_on(self):
        """The only way power is turned on (button 4, the AI's tool, a
        "fixed it" message). Measures at once and cuts again if the circuit
        is still unsafe, and says why. Returns a dict with power_on,
        recut, say, and the measurement (summary, lesson_check)."""
        if not self.board.has_power_switch:
            return {"power_on": None, "error": "no power switch",
                    "say": "This board cannot switch the power. Use the switch "
                           "on the power supply."}
        answer = self.board.restore()
        if answer.get("type") == "error":
            return {"power_on": None, "error": answer.get("msg", "error"),
                    "say": f"The board did not switch the power on: {answer.get('msg')}."}
        self.restore_allowed = False  # one "fixed it" turns power on once
        summary, result, safety_cut = self.measure()
        if safety_cut or not summary["power_on"]:
            self.board.show(STILL_UNSAFE_SCREEN)
            return {"power_on": False, "recut": True, "say": self._why_unsafe(summary, result),
                    "why": self._why_unsafe(summary, result),
                    "summary": summary, "lesson_check": result, "safety_cut": True}
        self.board.show("Power is ON.")
        return {"power_on": True, "recut": False, "say": "Power is on.",
                "summary": summary, "lesson_check": result, "safety_cut": False}

    def _why_unsafe(self, summary, result):
        why = "The power went off again right away, because the circuit is still not safe"
        current = summary.get("current_ma")
        if summary.get("current_source") == "measured" and current is not None \
                and current > SAFETY_MAX_MA:
            why += f": {current:.0f} mA flowed, and more than {SAFETY_MAX_MA:.0f} mA is too much"
        elif result.get("mistake_id"):
            spec = self._mistake_spec(self.lesson_id, result["mistake_id"]) or {}
            if spec.get("description"):
                why += f". It still looks like this: {spec['description'][0].lower()}{spec['description'][1:]}"
        return why + ". Fix the circuit first, then turn the power on again."

    def request_power_on(self):
        """For the AI's restore_power tool: only when the student asked."""
        if not self.restore_allowed:
            return {"power_on": None, "error": "not allowed",
                    "say": "Only you can turn the power on: press button 4, or "
                           "say you fixed the circuit."}
        return self.turn_power_on()

    def toggle_power(self):
        """Button 4: power off if on, on if off (the student is asking).
        A board with no power switch says so on its screen instead."""
        if not self.board.has_power_switch:
            self.board.show(NO_SWITCH_SCREEN)
            return {"error": "no power switch",
                    "say": "This board cannot switch the power. Use the switch "
                           "on the power supply."}
        if self.board.read()["power_on"]:
            answer = self.board.cut()
            self.board.show("Power is OFF.")
            return {"power_on": False, "answer": answer, "say": "Power is off."}
        result = self.turn_power_on()
        return {k: v for k, v in result.items() if k not in ("summary",)}

    # --- Hint button ----------------------------------------------------
    def hint(self):
        """Button 2: the next level of help, in plain Python.

        Never measures, never changes power, never adds a mistake. For the
        most recent unfixed mistake: the hint, then the full explanation.
        With no mistake: where the current build step goes.
        """
        om = self.open_mistake
        if om:
            spec = self._mistake_spec(om["lesson"], om["mistake"])
            om["level"] = "explain"  # after the hint comes the full explanation
            self.board.show("More help is on|the laptop.")
            return {"level": "explain", "mistake": om["mistake"], "say": spec["explain"]}
        layout = self.lesson["layout"]
        i = max(self.step, 0)
        if i >= len(layout):
            return {"level": "step", "say": "All parts are in place. Press 1 to check "
                                            "your circuit."}
        part = layout[i]
        start = "" if self.step >= 0 else "Press 3 to start building. "
        return {"level": "step", "step": i + 1, "part": part["part"],
                "say": f"{start}Step {i + 1} of {len(layout)}: {part['part']}. "
                       f"Put {self.where_legs(part['legs'])}."}

    @classmethod
    def where_legs(cls, legs):
        """Where a part's two legs go, in plain words."""
        a, b = legs
        if "(" in a["leg"]:  # a part with + and - legs
            return (f"the {a['leg']} in {cls._where(a['row'])} and the "
                    f"{b['leg']} in {cls._where(b['row'])}")
        kind = a["leg"].split()[-1]  # "leg" or "end"
        return (f"one {kind} in {cls._where(a['row'])} and the other "
                f"{kind} in {cls._where(b['row'])}")

    @staticmethod
    def _where(name):
        return {"PT+": "the top + line (5 V)", "PT-": "the top - line (ground)"}.get(
            name, row_label(name))

    # --- Mistake history -----------------------------------------------
    def times_seen(self, lesson_id, mistake_id):
        return sum(1 for m in self.history["mistakes"]
                   if m["lesson"] == lesson_id and m["mistake"] == mistake_id)

    def _mistake_spec(self, lesson_id, mistake_id):
        lesson = self.lessons.get(lesson_id)
        if lesson is None:
            return None
        if mistake_id == "intro" and "intro_lesson" in lesson:
            # Lesson 0's own mini-lesson (not about a mistake).
            return {"id": "intro", "description": lesson["title"],
                    "mini_lesson": lesson["intro_lesson"]}
        if mistake_id == "extra_part" and not self.is_explore(lesson_id):
            return self._extra_part_spec()
        return next((m for m in lesson["mistakes"] if m["id"] == mistake_id), None)

    # --- Lesson 0: explore, then answer the quiz ------------------------------
    def is_explore(self, lesson_id=None):
        lesson = self.lessons.get(self.lesson_id if lesson_id is None else lesson_id, {})
        return lesson.get("kind") == "explore"

    def intro_answered(self, lesson_id=None):
        lid = self.lesson_id if lesson_id is None else lesson_id
        return any(q.get("lesson") == lid and q.get("mistake") == "intro"
                   for q in self.history["quiz"])

    def start_lesson(self):
        """Where this student starts: lesson 0 when they are new, otherwise
        the first lesson (not an explore one) they have not passed."""
        h = self.history
        if not (h.get("passes") or h["mistakes"] or h["quiz"]):
            return min(self.lessons)
        for lid in sorted(self.lessons):
            if not self.is_explore(lid) and h.get("passes", {}).get(str(lid), 0) == 0:
                return lid
        return min(l for l in self.lessons if not self.is_explore(l))

    def log_mistake(self, mistake_id):
        """Record a mistake. Returns what the agent is allowed to say, and the
        mini-lesson about the idea behind the mistake (shown on the page).

        The mini-lesson explains the idea, never what to change on the board,
        so it is allowed on the first try alongside the hint."""
        mistake = next((m for m in self.lesson["mistakes"] if m["id"] == mistake_id), None)
        if mistake is None and mistake_id == "extra_part" and not self.is_explore():
            mistake = self._extra_part_spec()  # a part plugged in outside the plan
        if mistake is None:
            known = [m["id"] for m in self.lesson["mistakes"]]
            return {"error": f"unknown mistake {mistake_id!r} for lesson "
                             f"{self.lesson_id}. Known: {known}"}
        self.history["mistakes"].append(
            {"lesson": self.lesson_id, "mistake": mistake_id, "time": time.time()})
        self._save()
        occurrence = len(self.history["mistakes"]) - 1
        self.last_logged = {"lesson": self.lesson_id, "mistake": mistake_id,
                            "occurrence": occurrence}
        n = self.times_seen(self.lesson_id, mistake_id)
        # Rule from CLAUDE.md: hint first, never the full answer on the first try.
        level, say = ("hint", mistake["hint"]) if n == 1 else ("explain", mistake["explain"])
        self.open_mistake = {"lesson": self.lesson_id, "mistake": mistake_id, "level": level}
        result = {"times_seen": n, "level": level, "say": say,
                  "mini_lesson": dict(mistake["mini_lesson"])}
        if n >= 2:
            # The same mistake again: the old mini-lesson did not stick, so ask
            # the lesson writer for a new one with a different comparison.
            self.lesson_request = {"lesson": self.lesson_id, "mistake": mistake_id,
                                   "occurrence": occurrence,
                                   "reason": "the same mistake happened again"}
            result["new_lesson_wanted"] = True
        return result

    # --- Mini-lessons written by the lesson writer ---------------------------
    def ai_lesson(self, occurrence):
        return self.history["ai_lessons"].get(str(occurrence))

    def comparisons_used(self, mistake_id=None):
        mistake_id = mistake_id or (self.lesson_request or {}).get("mistake")
        return [l["comparison"] for l in self.history["ai_lessons"].values()
                if l.get("mistake") == mistake_id]

    def lesson_writer_facts(self):
        """What the lesson writer reads (its get_lesson_facts tool)."""
        req = self.lesson_request
        if req is None:
            return {"error": "No new mini-lesson is wanted right now."}
        spec = self._mistake_spec(req["lesson"], req["mistake"]) or {}
        return {"lesson": req["lesson"], "mistake": req["mistake"],
                "what_went_wrong": spec.get("description", ""),
                "why_a_new_lesson": req["reason"],
                "old_mini_lesson": spec.get("mini_lesson", {}),
                "comparisons_already_used": self.comparisons_used(req["mistake"])}

    def accept_ai_lesson(self, lesson):
        """Save a lesson that passed the grader and plain Python's checks, and
        note its comparison in the student's notebook."""
        req = self.lesson_request
        if req is None:
            return None
        keep = {k: lesson[k] for k in ("title", "idea", "question", "options", "answer", "why",
                                       "comparison")}
        self.history["ai_lessons"][str(req["occurrence"])] = dict(
            keep, lesson=req["lesson"], mistake=req["mistake"])
        self._save()
        self.new_ai_lesson = req["occurrence"]
        self.lesson_request = None
        if self.notebook is not None:
            day = time.strftime("%b %d").replace(" 0", " ")
            label = mistake_label(self.lessons[req["lesson"]], req["mistake"])
            self.notebook.add(f"- {day}, lesson {req['lesson']}: Zip wrote a new mini-lesson "
                              f"for {label} using the comparison \"{keep['comparison']}\".")
        return req["occurrence"]

    # --- Mini-lesson quiz ------------------------------------------------
    def quiz_result(self, occurrence, ai=False):
        """The saved answer for one logged mistake, or None if not answered.
        ai=True: the answer to the lesson writer's card for it."""
        return next((q for q in self.history["quiz"] if q.get("occurrence") == occurrence
                     and bool(q.get("ai")) == ai), None)

    def record_quiz(self, lesson_id, mistake_id, choice, occurrence=None, ai=False):
        """Save the student's answer to a mini-lesson question.

        The right answer comes from the lesson file, never from the page.
        Returns {"correct", "choice", "answer", "why"} or {"error": ...}.
        """
        mistake = self._mistake_spec(lesson_id, mistake_id)
        if mistake is None or "mini_lesson" not in mistake:
            return {"error": f"no mini-lesson for lesson {lesson_id!r}, "
                             f"mistake {mistake_id!r}"}
        ml = mistake["mini_lesson"]
        if ai:  # the card the lesson writer wrote for this occurrence
            ml = self.ai_lesson(occurrence)
            if ml is None:
                return {"error": "no new mini-lesson for this mistake"}
        if isinstance(choice, bool) or not isinstance(choice, int) \
                or not 0 <= choice < len(ml["options"]):
            return {"error": f"choice must be 0 to {len(ml['options']) - 1}"}
        correct = choice == ml["answer"]
        entry = {"lesson": lesson_id, "mistake": mistake_id, "occurrence": occurrence,
                 "choice": choice, "correct": correct, "time": time.time()}
        if ai:
            entry["ai"] = True
        self.history["quiz"].append(entry)
        self._save()
        if not ai and not correct and mistake_id != "intro":
            # Wrong answer: a new mini-lesson with another comparison may help.
            self.lesson_request = {"lesson": lesson_id, "mistake": mistake_id,
                                   "occurrence": occurrence,
                                   "reason": "the quiz was answered wrong"}
        if self.notebook is not None and not ai:
            self.notebook.fill_quiz(mistake_label(self.lessons[lesson_id], mistake_id), correct)
        return {"correct": correct, "choice": choice, "answer": ml["answer"],
                "why": ml["why"]}

    def _save(self):
        if self.history_path:
            self.history_path.write_text(json.dumps(self.history, indent=2), encoding="utf-8")

    # --- Picking the next lesson ---------------------------------------
    def next_lesson(self):
        """Choose the next lesson from the mistake history.

        1. Current lesson not passed yet -> stay on it.
        2. The same mistake happened REPEAT_IF_SEEN+ times in this lesson
           and it has only been passed once -> practice it once more.
        3. Otherwise -> the first lesson not passed yet (lesson 0 is not a
           step back, so it comes last).
        4. A lesson that was skipped (for example lesson 0, when the page
           started at lesson 1) -> offered now. Never said to be passed.
        5. All passed -> review the lesson with the most mistakes.
        """
        cur = self.lesson_id
        if not self.passed_current:
            return self._pick(cur, "The current lesson is not finished yet. "
                                   "The circuit has to read correctly first.")

        passes = self.history.setdefault("passes", {})
        passes[str(cur)] = passes.get(str(cur), 0) + 1
        self._save()

        repeated = [m["id"] for m in self.lesson["mistakes"]
                    if self.times_seen(cur, m["id"]) >= REPEAT_IF_SEEN]
        if repeated and passes[str(cur)] == 1:
            # The reason is shown to the student, so it talks to "you" in
            # plain words; the mistake ids stay in "repeated" for the agent.
            result = self._pick(cur, "Let's practice this lesson once more, because "
                                     "you made the same mistake more than once.")
            result["repeated"] = repeated
            return result

        for lid in sorted(self.lessons):
            if self.is_explore(lid):
                continue  # lesson 0 is where new students start, never a step back
            if passes.get(str(lid), 0) == 0:
                return self._pick(lid, "This is the next lesson you have not passed yet.")
        # Every building lesson is passed. A lesson skipped earlier (lesson 0
        # when the student started at lesson 1) comes now: it is not passed.
        for lid in sorted(self.lessons):
            if passes.get(str(lid), 0) == 0:
                return self._pick(lid, f"You skipped lesson {lid}, "
                                       f"{self.lessons[lid]['title']}. Let's do it now.")

        counts = {lid: sum(1 for m in self.history["mistakes"] if m["lesson"] == lid)
                  for lid in self.lessons}
        worst = max(counts, key=lambda lid: (counts[lid], -lid))
        if counts[worst] == 0:
            return {"lesson": None, "reason": "You passed every lesson with no mistakes. "
                                              "Well done!"}
        return self._pick(worst, f"You passed every lesson! Let's review lesson {worst}, "
                                 f"where you made the most mistakes ({counts[worst]}).")

    def _pick(self, lesson_id, reason):
        changed = lesson_id != self.lesson_id or self.passed_current
        if changed:
            self.set_lesson(lesson_id)
        return {"lesson": lesson_id, "title": self.lessons[lesson_id]["title"],
                "circuit": self.lessons[lesson_id]["circuit"], "reason": reason}
