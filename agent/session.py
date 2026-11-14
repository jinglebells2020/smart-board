"""One student's session: the board, the current lesson, and the history.

Everything here is plain Python and gives the same answer every time.
The AI decides *how to talk* to the student. The rules that must never
go wrong (cutting power, hints before answers, picking the next lesson)
live here, so a confused model cannot break them.
"""

import json
import time
from pathlib import Path

from agent.lessons import SAFETY_MAX_MA, check, load_lessons, summarize

WINDOW = 10  # readings per measurement (1 second on the real board)
REPEAT_IF_SEEN = 2  # same mistake this many times -> practice the lesson again


class Session:
    def __init__(self, board, lesson=1, history_path=None, lessons=None):
        self.board = board
        self.lessons = lessons or load_lessons()
        self.history_path = Path(history_path) if history_path else None
        self.history = {"mistakes": [], "passes": {}, "quiz": []}
        if self.history_path and self.history_path.exists():
            self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.history.setdefault("quiz", [])  # history files from before quizzes
        self.lesson_id = lesson
        self.passed_current = False
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
                "probe1": l["probe1"]}

    def set_lesson(self, lesson_id):
        self.lesson_id = lesson_id
        self.passed_current = False

    # --- Measuring -----------------------------------------------------
    def measure(self):
        """Take a window of readings. Cuts power at once if unsafe."""
        readings = []
        safety_cut = False
        for _ in range(WINDOW):
            r = self.board.read()
            readings.append(r)
            if r["power_on"] and r["current_ma"] > SAFETY_MAX_MA:
                # Safety net: does not wait for the AI.
                self.board.send("CUT")
                safety_cut = True
                break
        # All readings so far were taken while power was on, so the
        # check still sees what the student built.
        summary = summarize(readings)
        result = check(summary, self.lesson)
        if result["status"] == "ok":
            self.passed_current = True
        if safety_cut:
            summary["power_on"] = False  # it is off now
        return summary, result, safety_cut

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
