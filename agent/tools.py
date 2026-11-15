"""The tools the agent can call. Each one acts on the board or the session.

Tool names come from CLAUDE.md (protocol version 2): read_rows,
read_current, cut_power, light_row, clear_lights, show_on_board,
log_mistake, next_lesson, plus restore_power to turn power back on
after the student fixes a circuit ("Turn the power back on?" first).
Zip acts: run_experiment (ask the student to do one thing, then measure
again; it pauses for the student's "done" or "can't") and
guide_next_part (which parts look placed). cut_power takes a reason.
There is no tool that turns power on without the student's approval.
Every tool returns a JSON string, which is what the model reads.
"""

import functools
import json

from langgraph.types import interrupt

from agent.experiments import (MAX_EXPERIMENTS, diff_facts, facts_of, label, placed_parts,
                               validate_instruction)
from agent.lessons import summarize

from agent.serial_board import BoardError
from protocol import COLORS

MANUAL_OFF = ("This board has no power switch. Tell the student to switch the "
              "power off by hand right now. The board screen already says so.")
MANUAL_ON = ("This board has no power switch. Tell the student to switch the "
             "power back on by hand after fixing the circuit.")


def _board_errors_in_words(tool):
    """A board that stops answering gives the AI a plain message, not a crash."""
    @functools.wraps(tool)
    def wrapper(*args, **kwargs):
        try:
            return tool(*args, **kwargs)
        except BoardError as e:
            return json.dumps({"type": "error", "board_error": str(e),
                               "say": "The board is not answering. Check that its USB "
                                      "cable is plugged in."})
    return wrapper


def make_tools(session, can_pause=False):
    """Build the tool functions for one session. can_pause: the agent has a
    checkpointer, so run_experiment can wait for the student."""
    board = session.board

    def _measure():
        return _report(*session.measure())

    def _report(summary, result, safety_cut):
        return {
            "rows": summary["rows"],
            "floating": summary["floating"],
            "supply_v": summary["supply_v"],
            "power_on": summary["power_on"],
            "current_ma": summary["current_ma"],
            "current_source": summary["current_source"],
            "current_note": summary["current_note"],
            "safety_cut": safety_cut,
            "board": board.capabilities(),
            "lesson": session.lesson_info(),
            "lesson_check": result,
            # Where to write this check in the student's notebook (or None).
            "notebook": session.notebook_task(),
        }

    def read_rows() -> str:
        """Measure the voltage on every row this lesson uses, and which
        of them are empty ("floating": nothing connected).

        Also returns lesson_check: is the circuit correct, which known
        mistake is it (with the rows it is in), or a clue for an unknown
        mistake. If the circuit is dangerous, power is already off
        (safety_cut true), or, on a board with no power switch,
        lesson_check.manual_power_off is true: the student must switch
        the power off by hand.
        """
        return json.dumps(_measure())

    def read_current() -> str:
        """Measure the supply current in mA.

        current_source says where the number comes from: "measured" (the
        board has a current sensor), "calculated" (from the voltage on
        both sides of a known resistor, for example (5 - 2) / 220 =
        13.6 mA) or "unknown" (it could not be worked out). Also returns
        lesson_check, like read_rows.
        """
        m = _measure()
        return json.dumps({k: m[k] for k in ("current_ma", "current_source", "current_note",
                                             "power_on", "safety_cut", "lesson_check")})

    def cut_power(reason: str = "") -> str:
        """Switch breadboard power OFF at once. Use for any dangerous
        circuit, and whenever the facts suggest danger (current rising
        toward the limit, the + line pulled down), even before the safety
        limit. Give the reason in a few words, and tell the student why in
        your reply. Only the student can turn power back on (restore_power
        asks them first). If the board has no power switch, this tells you
        to have the student switch it off by hand."""
        if reason:
            session.power_log.append({"cut": str(reason)[:200]})
        answer = board.cut()
        if answer.get("type") == "error":
            board.show("DANGER!|Switch the power OFF|by hand now.")
            return json.dumps(dict(answer, do_instead=MANUAL_OFF))
        return json.dumps(answer)

    def restore_power() -> str:
        """Switch breadboard power back ON. Only works when the student
        asked for it in this message (said they fixed it, or asked for the
        power). The circuit is measured at once: if it is still unsafe,
        power goes off again ("recut": true) and "why" says why. Also
        returns lesson_check, like read_rows."""
        result = session.request_power_on()
        if result.get("error") == "no power switch":
            return json.dumps({"type": "error", "msg": "no power switch",
                               "do_instead": MANUAL_ON})
        if result.get("error"):
            return json.dumps({"type": "error", "error": result["error"], "msg": result["say"]})
        out = _report(result["summary"], result["lesson_check"], result["safety_cut"])
        out.update(power_on=result["power_on"], recut=result["recut"])
        if result["recut"]:
            out["why"] = result["why"]
        return json.dumps(out)

    def get_hint() -> str:
        """When the student asks for a hint: the next level of help for
        their most recent mistake (hint, then full explanation), or where
        the current build step goes. Does not measure or change power."""
        return json.dumps(session.hint())

    def light_row(rows: list[str], color: str) -> str:
        """Light the lights beside one or more rows, like ["T10", "T12"].

        Colors always mean the same: blue = put a part here, green =
        correct, red = problem here, yellow = careful, off = light off.
        """
        if isinstance(rows, str):
            rows = [r for r in rows.replace(",", " ").split() if r]
        if str(color).lower() not in COLORS:
            return json.dumps({"type": "error",
                               "msg": f"color must be one of {list(COLORS)}"})
        allowed = session.lights_allowed(rows, color)
        if not allowed:  # mid-build: an empty row is not a mistake (instant help)
            return json.dumps({"type": "ack", "skipped": list(rows),
                               "why": "still building: those rows are just empty"})
        return json.dumps(board.set_lights(allowed, color))

    def clear_lights() -> str:
        """Turn every row light off."""
        return json.dumps(board.clear_lights())

    def show_on_board(text: str) -> str:
        """Show a short message on the board's small screen. Use "|" to
        start a new line. It is cut to fit the screen (4 lines of 20
        characters), so keep it very short."""
        return json.dumps(board.show(text))

    def log_mistake(mistake_id: str) -> str:
        """Record a mistake from lesson_check.mistake_id in the history.

        Returns "say": the only text you may give the student about this
        mistake. On the first time it is a hint, not the answer.
        Also returns "mini_lesson": a short lesson about the idea behind the
        mistake, with a quiz question. The page shows it to the student as a
        card, so do not read it out or give away the quiz answer.
        """
        return json.dumps(session.log_mistake(mistake_id))

    def next_lesson() -> str:
        """Pick the next lesson from the mistake history and switch to it.
        Call only after lesson_check status is "ok"."""
        return json.dumps(session.next_lesson())

    def guide_next_part() -> str:
        """While the student builds: every part of the lesson (in the plan's
        order), its rows, and whether it looks placed (every row it uses has
        something connected). YOU choose the next part (the student may
        have started elsewhere): light its rows "blue" with light_row and
        show a short step on the board screen with show_on_board (at most
        4 lines of 20 characters). Does not change power."""
        readings = [board.read() for _ in range(3)]
        lesson = session._step_lesson(session.lesson) if session.button_step else session.lesson
        parts = placed_parts(lesson, summarize(readings, lesson))
        missing = [p for p in parts if not p["placed"]]
        return json.dumps({"parts": parts, "placed": len(parts) - len(missing),
                           "total": len(parts),
                           "first_missing": missing[0]["part"] if missing else None,
                           "screen": "4 lines of 20 characters"})

    def run_experiment(instruction: str, done_label: str = "Done",
                       cant_label: str = "I can't") -> str:
        """Ask the student to do ONE thing with their hands, then measure
        again, to tell two causes apart. instruction: one action from
        "pull out", "push in", "turn around", "move", "press", "let go",
        "wait", a part of the lesson (or "the extra part") and a row, e.g.
        "Turn around the LED in rows 10 and 12." The page shows it with two
        buttons (done_label, cant_label) and waits. Returns before_facts,
        after_facts and "changed" (what is different), or done: false if
        the student could not. At most 2 per reply. Never about power."""
        lesson = session._step_lesson(session.lesson) if session.button_step else session.lesson
        ok, error, parsed = validate_instruction(instruction, lesson)
        if not ok:
            return json.dumps({"type": "error", "msg": error + " Rephrase and try again."})
        pending = session.experiment
        if pending is None or pending.get("instruction") != instruction:
            if session.experiments_done >= MAX_EXPERIMENTS:
                return json.dumps({"type": "error",
                                   "msg": f"At most {MAX_EXPERIMENTS} experiments per reply. "
                                          "Conclude from what you have, or ask a question."})
            if session.last_check is None:
                session.measure()
            summary, result = session.last_check
            pending = {"instruction": instruction, "parsed": parsed,
                       "before": facts_of(session, summary, result)}
            session.experiment = pending  # kept while the run waits for the student
        answer = interrupt({"type": "experiment", "instruction": instruction.strip(),
                            "done_label": label(done_label, "Done"),
                            "cant_label": label(cant_label, "I can't"),
                            "action": parsed["action"], "part": parsed["part"],
                            "rows": parsed["rows"]})
        session.experiment = None
        session.experiments_done += 1
        if not (isinstance(answer, dict) and answer.get("done")):
            session.trace.append({"who": "tutor", "tool": "experiment_result",
                                  "args": {"done": False}})
            return json.dumps({"done": False, "say": "The student could not do it."})
        summary, result, cut = session.measure()
        after = facts_of(session, summary, result, cut)
        changed, words = diff_facts(pending["before"], after)
        session.trace.append({"who": "tutor", "tool": "experiment_result",
                              "args": {"done": True, "changed": words}})
        return json.dumps({"done": True, "before_facts": pending["before"],
                           "after_facts": after, "changed": changed, "changed_words": words,
                           "lesson_check": result})

    tools = [read_rows, read_current, cut_power, restore_power, get_hint, light_row,
             clear_lights, show_on_board, log_mistake, next_lesson, guide_next_part]
    if can_pause:
        tools.append(run_experiment)
    return [_board_errors_in_words(t) for t in tools]
