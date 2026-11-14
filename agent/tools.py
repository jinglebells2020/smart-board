"""The tools the agent can call. Each one acts on the board or the session.

Tool names come from CLAUDE.md, plus restore_power, which is needed
to turn power back on after the student fixes a cut circuit.
Every tool returns a JSON string, which is what the model reads.
"""

import json


def make_tools(session):
    """Build the tool functions for one session."""

    def _measure(fields):
        summary, result, safety_cut = session.measure()
        out = {k: summary[k] for k in fields}
        out["power_on"] = summary["power_on"]
        out["safety_cut"] = safety_cut
        out["lesson"] = session.lesson_info()
        out["lesson_check"] = result
        return json.dumps(out)

    def read_current() -> str:
        """Measure the current (mA) flowing from the breadboard supply.

        Also returns lesson_check: whether the circuit matches the current
        lesson, or which known mistake it matches. If the current is
        unsafe, power has already been cut (safety_cut is true).
        """
        return _measure(["current_ma"])

    def read_voltages() -> str:
        """Measure the supply voltage and the probe1 voltage (volts).

        probe1_v_spread is how much probe1 jumped during the
        measurement; a big spread means the probe is not connected.
        Also returns lesson_check, like read_current.
        """
        return _measure(["supply_v", "probe1_v", "probe1_v_spread"])

    def cut_power() -> str:
        """Turn breadboard power OFF. Use for any unsafe circuit."""
        return json.dumps(session.board.send("CUT"))

    def restore_power() -> str:
        """Turn breadboard power back ON, after the student says they fixed
        the circuit. Measure right after; unsafe current is cut again."""
        return json.dumps(session.board.send("RESTORE"))

    def light_row_led(row: int) -> str:
        """Light the indicator LED next to a breadboard row, to show the
        student where to look."""
        return json.dumps(session.board.send(f"LED {row}"))

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

    return [read_voltages, read_current, cut_power, restore_power,
            light_row_led, log_mistake, next_lesson]
