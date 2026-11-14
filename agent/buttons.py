"""What the 4 board buttons do. The meaning of each button is set in
protocol.BUTTONS; this file is the one place that carries it out.

    1 = check my circuit    -> the tutor checks the circuit
    2 = I need a hint       -> the next level of help (no AI, no measuring,
                               never changes power, never logs a mistake)
    3 = next step           -> light the rows for the next part (no AI)
    4 = power off / on      -> switch power; turning it on measures at once
                               and cuts again if the circuit is unsafe
"""

from protocol import BUTTONS

TUTOR_TEXT = {"check": "Check my circuit"}


def handle_button(button_id, session, ask_tutor):
    """Do what a button press means.

    ask_tutor(text) sends text to the tutor and returns its reply.
    Returns a dict saying what happened.
    """
    action = BUTTONS.get(button_id)
    if action in TUTOR_TEXT:
        return {"button": button_id, "action": action,
                "reply": ask_tutor(TUTOR_TEXT[action])}
    if action == "hint":
        result = session.hint()
        return {"button": button_id, "action": action, "reply": result["say"], **result}
    if action == "next_step":
        return {"button": button_id, "action": action, **session.next_step()}
    if action == "power":
        return {"button": button_id, "action": action, **session.toggle_power()}
    return {"button": button_id, "error": f"no button {button_id}"}
