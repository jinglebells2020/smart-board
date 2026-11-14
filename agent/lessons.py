"""Load lessons/*.json and compare readings against them.

Plain Python, no AI here. The agent's tools use this so that the
decision "is this circuit right, and if not, which mistake is it?" is
the same every time, whichever model is running.
"""

import json
import statistics
from pathlib import Path

LESSONS_DIR = Path(__file__).resolve().parent.parent / "lessons"

# Any current above this is unsafe for every lesson, so power is cut
# at once, without waiting for the AI. Lesson 1 normally draws 13.6 mA.
SAFETY_MAX_MA = 30.0

# For the clue on unknown mistakes: how far a number may be from the
# lesson's expected value before it counts as "off", when the lesson has
# no accept range for that number.
CURRENT_TOLERANCE = 0.15  # 15 % of the expected current
VOLTAGE_TOLERANCE = 0.3   # volts
JUMPY_SPREAD_V = 1.0      # a reading that moves this much is not steady
LOW_SUPPLY_V = 4.5        # every lesson runs from 5 V


def load_lessons(folder=LESSONS_DIR):
    """Return {lesson_id: lesson_dict} for every lessons/lesson*.json."""
    lessons = {}
    for path in sorted(Path(folder).glob("lesson*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        lessons[data["id"]] = data
    return lessons


def summarize(readings):
    """Average a list of readings into one set of numbers."""
    probe = [r["voltages"]["probe1"] for r in readings]
    return {
        "current_ma": round(statistics.mean(r["current_ma"] for r in readings), 3),
        "supply_v": round(statistics.mean(r["supply_v"] for r in readings), 3),
        "probe1_v": round(statistics.mean(probe), 3),
        "probe1_v_spread": round(max(probe) - min(probe), 3),
        "power_on": all(r["power_on"] for r in readings),
    }


def in_range(value, rule):
    """rule is {"min": a, "max": b} (either may be left out) or [a, b]."""
    if isinstance(rule, list):
        rule = {"min": rule[0], "max": rule[1]}
    return rule.get("min", float("-inf")) <= value <= rule.get("max", float("inf"))


def matches(summary, detect):
    return all(in_range(summary[field], rule) for field, rule in detect.items())


def check(summary, lesson):
    """Decide what a summary means for a lesson.

    Returns a dict with "status" set to one of:
      "power_off" : power is cut, nothing to measure
      "mistake"   : matches a known mistake (see "mistake_id")
      "ok"        : the circuit is correct
      "unknown"   : wrong, but not a mistake we know about
    """
    if not summary["power_on"]:
        return {"status": "power_off"}
    for m in lesson["mistakes"]:
        if matches(summary, m["detect"]):
            result = {"status": "mistake", "mistake_id": m["id"], "action": m["action"]}
            if m.get("show_row") and lesson.get("probe1_row") is not None:
                result["row"] = lesson["probe1_row"]
            return result
    if matches(summary, lesson["accept"]):
        return {"status": "ok"}
    return {"status": "unknown", "clue": clue(summary, lesson)}


def clue(summary, lesson):
    """Explain an unknown mistake from the numbers alone.

    Compares each measured number with the lesson's expected value and
    says which one is off, in which direction, and what that usually
    means, in words a 6th grader knows. It never names a specific fix:
    for an unknown mistake we do not know which part is wrong.

    Returns {"text": "...", "details": [{"number", "direction",
    "measured", "expected", "meaning"}, ...]}, most important first.
    """
    expected = lesson.get("expected", {})
    accept = lesson.get("accept", {})
    resistor = lesson.get("resistor_words", "the resistor")
    found = []  # (importance, detail)

    def add(importance, number, direction, measured, exp, meaning):
        found.append((importance, {"number": number, "direction": direction,
                                   "measured": measured, "expected": exp,
                                   "meaning": meaning}))

    spread = summary.get("probe1_v_spread", 0.0)
    jumpy = spread >= JUMPY_SPREAD_V
    if jumpy:
        add(3, "voltage at the measuring row", "jumping", round(spread, 2), None,
            "The voltage at the measuring row keeps jumping. That usually means "
            "the row is not touching anything.")

    if "current_ma" in expected:
        exp = expected["current_ma"]
        got = max(0.0, summary["current_ma"])
        low, high = accept.get("current_ma") or (exp * (1 - CURRENT_TOLERANCE),
                                                 exp * (1 + CURRENT_TOLERANCE))
        if got < low:
            if got < 0.1 * exp:
                add(3, "current", "almost none", got, exp,
                    "Almost no current is flowing. That usually means the loop is "
                    "broken somewhere, so current cannot get all the way around.")
            elif got < 0.5 * exp:
                add(2, "current", "much lower", got, exp,
                    "The current is much lower than it should be. Something is "
                    f"holding it back more than {resistor} would.")
            else:
                add(1, "current", "a little lower", got, exp,
                    "The current is a little lower than it should be. Something is "
                    f"holding it back a bit more than {resistor} would.")
        elif got > high:
            if got > 2 * exp:
                add(2, "current", "much higher", got, exp,
                    "The current is much higher than it should be. It may have found "
                    f"an easier path than through {resistor}.")
            else:
                add(1, "current", "a little higher", got, exp,
                    "The current is a little higher than it should be. Something is "
                    f"letting it through more easily than {resistor} would.")

    if "probe1_v" in expected and not jumpy:
        exp = expected["probe1_v"]
        got = summary["probe1_v"]
        supply = summary.get("supply_v", 5.0)
        low, high = accept.get("probe1_v") or (exp - VOLTAGE_TOLERANCE,
                                               exp + VOLTAGE_TOLERANCE)
        if got < low:
            if got < 0.2:
                add(2, "voltage at the measuring row", "almost none", got, exp,
                    "The measuring row has almost 0 volts. That usually means it has "
                    "no path to the power supply, or it is joined straight to ground.")
            else:
                add(1, "voltage at the measuring row", "lower", got, exp,
                    "The voltage at the measuring row is lower than it should be. "
                    "Less of the push from the power supply is reaching that row.")
        elif got > high:
            if got > supply - 0.2:
                add(2, "voltage at the measuring row", "almost all", got, exp,
                    "The measuring row has almost the full push of the power supply. "
                    "That usually means no current is flowing past that row, so "
                    "nothing lowers the voltage.")
            else:
                add(1, "voltage at the measuring row", "higher", got, exp,
                    "The voltage at the measuring row is higher than it should be. "
                    "More of the push from the power supply is reaching that row.")

    if summary.get("supply_v", 5.0) < LOW_SUPPLY_V:
        add(1, "power voltage", "lower", summary["supply_v"], 5.0,
            "The power voltage is lower than 5 volts. The power supply may be "
            "having trouble keeping up.")

    found.sort(key=lambda f: -f[0])  # most important first (stable for ties)
    details = [d for _, d in found]
    if details:
        text = " ".join(d["meaning"] for d in details[:2])
    else:
        text = ("The numbers are close to right, but not quite there yet. "
                "Look over the circuit once more, one part at a time.")
    return {"text": text, "details": details}
