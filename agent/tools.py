"""The tools the agent can call. Each one acts on the board or the session.

Tool names come from CLAUDE.md (protocol version 2): read_rows,
read_current, cut_power, light_row, clear_lights, show_on_board,
log_mistake, next_lesson, plus restore_power to turn power back on
after the student fixes a circuit.
Every tool returns a JSON string, which is what the model reads.
"""

import json

from protocol import COLORS

MANUAL_OFF = ("This board has no power switch. Tell the student to switch the "
              "power off by hand right now. The board screen already says so.")
MANUAL_ON = ("This board has no power switch. Tell the student to switch the "
             "power back on by hand after fixing the circuit.")


def make_tools(session):
    """Build the tool functions for one session."""
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

    def cut_power() -> str:
        """Switch breadboard power OFF. Use for any dangerous circuit.
        If the board has no power switch, this tells you to have the
        student switch it off by hand."""
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
        return json.dumps(board.set_lights(rows, color))

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

    return [read_rows, read_current, cut_power, restore_power, get_hint, light_row,
            clear_lights, show_on_board, log_mistake, next_lesson]
