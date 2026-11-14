"""One student's session: the board, the current lesson, and the history.

Everything here is plain Python and gives the same answer every time.
The AI decides *how to talk* to the student. The rules that must never
go wrong (power off on danger, hints before answers, picking the next
lesson, what the board screen says when the student must act) live
here, so a confused model cannot break them.
"""

import json
import time
from pathlib import Path

from agent.boardlink import BoardLink
from agent.lessons import SAFETY_MAX_MA, check, load_lessons, summarize
from protocol import is_row, row_label

WINDOW = 10  # readings per measurement (about 1 second on the fake board)
REPEAT_IF_SEEN = 2  # same mistake this many times -> practice the lesson again

# Board screen text when the student must switch power off by hand
# (a board with no power switch). fit_screen keeps it to the screen size.
MANUAL_OFF_SCREEN = "DANGER!|Switch the power OFF|by hand now.|Then fix the circuit."
NO_SWITCH_SCREEN = "No power switch.|Use the switch on|the power supply."


def _short(name):
    """Very short row name for the small screen: T10 -> "row 10"."""
    return {"PT+": "top +", "PT-": "top -", "PB+": "bottom +",
            "PB-": "bottom -"}.get(name, row_label(name).replace(" (lower half)", "b"))


class Session:
    def __init__(self, board, lesson=1, history_path=None, lessons=None):
        self.board = board if isinstance(board, BoardLink) else BoardLink(board)
        self.lessons = lessons or load_lessons()
        self.history_path = Path(history_path) if history_path else None
        self.history = {"mistakes": [], "passes": {}, "quiz": []}
        if self.history_path and self.history_path.exists():
            self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.history.setdefault("quiz", [])  # history files from before quizzes
        self.lesson_id = lesson
        self.passed_current = False
        self.step = -1  # building: -1 = not started; else index into layout
        # The most recent log_mistake call: {"lesson", "mistake", "occurrence"}.
        # The web page uses it to show the matching mini-lesson card.
        self.last_logged = None

    # --- Lessons -------------------------------------------------------
    @property
    def lesson(self):
        return self.lessons[self.lesson_id]

    def lesson_info(self):
        l = self.lesson
        return {"id": l["id"], "title": l["title"], "circuit": l["circuit"],
                "rows": list(l["rows"]), "parts": [p["part"] for p in l["layout"]]}

    def set_lesson(self, lesson_id):
        if lesson_id != self.lesson_id:
            # A new lesson starts with dark lights: old green or red lights
            # would point at rows that mean something else now.
            self.board.clear_lights()
        self.lesson_id = lesson_id
        self.passed_current = False
        self.step = -1

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
        # All readings so far were taken while power was on, so the check
        # still sees what the student built.
        summary = summarize(readings, self.lesson)
        result = check(summary, self.lesson)
        if result["status"] == "ok":
            self.passed_current = True
        if result.get("action") == "cut_power" and summary["power_on"] and not safety_cut:
            if board.has_power_switch:
                board.cut()
                safety_cut = True
            else:
                result["manual_power_off"] = True
                board.show(MANUAL_OFF_SCREEN)
        if safety_cut:
            summary["power_on"] = False  # it is off now
        return summary, result, safety_cut

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

    # --- Power button --------------------------------------------------
    def toggle_power(self):
        """Button 4: power off if on, on if off. A board with no power
        switch says so on its screen instead."""
        if not self.board.has_power_switch:
            self.board.show(NO_SWITCH_SCREEN)
            return {"error": "no power switch",
                    "say": "This board cannot switch the power. Use the switch "
                           "on the power supply."}
        on = self.board.read()["power_on"]
        answer = self.board.cut() if on else self.board.restore()
        self.board.show("Power is OFF." if on else "Power is ON.")
        return {"power_on": not on, "answer": answer}

    # --- Mistake history -----------------------------------------------
    def times_seen(self, lesson_id, mistake_id):
        return sum(1 for m in self.history["mistakes"]
                   if m["lesson"] == lesson_id and m["mistake"] == mistake_id)

    def _mistake_spec(self, lesson_id, mistake_id):
        lesson = self.lessons.get(lesson_id)
        if lesson is None:
            return None
        return next((m for m in lesson["mistakes"] if m["id"] == mistake_id), None)

    def log_mistake(self, mistake_id):
        """Record a mistake. Returns what the agent is allowed to say, and the
        mini-lesson about the idea behind the mistake (shown on the page).

        The mini-lesson explains the idea, never what to change on the board,
        so it is allowed on the first try alongside the hint."""
        mistake = next((m for m in self.lesson["mistakes"] if m["id"] == mistake_id), None)
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
        return {"times_seen": n, "level": level, "say": say,
                "mini_lesson": dict(mistake["mini_lesson"])}

    # --- Mini-lesson quiz ------------------------------------------------
    def quiz_result(self, occurrence):
        """The saved answer for one logged mistake, or None if not answered."""
        return next((q for q in self.history["quiz"] if q.get("occurrence") == occurrence),
                    None)

    def record_quiz(self, lesson_id, mistake_id, choice, occurrence=None):
        """Save the student's answer to a mini-lesson question.

        The right answer comes from the lesson file, never from the page.
        Returns {"correct", "choice", "answer", "why"} or {"error": ...}.
        """
        mistake = self._mistake_spec(lesson_id, mistake_id)
        if mistake is None or "mini_lesson" not in mistake:
            return {"error": f"no mini-lesson for lesson {lesson_id!r}, "
                             f"mistake {mistake_id!r}"}
        ml = mistake["mini_lesson"]
        if isinstance(choice, bool) or not isinstance(choice, int) \
                or not 0 <= choice < len(ml["options"]):
            return {"error": f"choice must be 0 to {len(ml['options']) - 1}"}
        correct = choice == ml["answer"]
        self.history["quiz"].append({
            "lesson": lesson_id, "mistake": mistake_id, "occurrence": occurrence,
            "choice": choice, "correct": correct, "time": time.time()})
        self._save()
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
        3. Otherwise -> the first lesson not passed yet.
        4. All passed -> review the lesson with the most mistakes.
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
            # The reason is shown to the student, so it uses plain words;
            # the mistake ids stay available in "repeated" for the agent.
            result = self._pick(cur, "Let's practice this lesson once more, because "
                                     "the same mistake happened more than once.")
            result["repeated"] = repeated
            return result

        for lid in sorted(self.lessons):
            if passes.get(str(lid), 0) == 0:
                return self._pick(lid, "Next lesson the student has not passed yet.")

        counts = {lid: sum(1 for m in self.history["mistakes"] if m["lesson"] == lid)
                  for lid in self.lessons}
        worst = max(counts, key=lambda lid: (counts[lid], -lid))
        if counts[worst] == 0:
            return {"lesson": None, "reason": "All lessons passed with no mistakes. Done!"}
        return self._pick(worst, f"All lessons passed. Review lesson {worst}: "
                                 f"it had the most mistakes ({counts[worst]}).")

    def _pick(self, lesson_id, reason):
        changed = lesson_id != self.lesson_id or self.passed_current
        if changed:
            self.set_lesson(lesson_id)
        return {"lesson": lesson_id, "title": self.lessons[lesson_id]["title"],
                "circuit": self.lessons[lesson_id]["circuit"], "reason": reason}
