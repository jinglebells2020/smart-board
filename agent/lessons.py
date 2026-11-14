"""Load lessons/*.json and compare readings against them.

Plain Python, no AI here. The agent's tools use this so that the
decision "is this circuit right, and if not, which mistake is it?" is
the same every time, whichever model is running.
"""

import json
import statistics
from pathlib import Path

from protocol import ALL_NODES, row_label

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


# =====================================================================
# Protocol version 2: checking from many row voltages.
# A board may measure any set of rows and may or may not have a current
# sensor, so everything below works with whatever the board reports.
# =====================================================================

FLOAT_SHARE = 0.5            # floating in more than half the readings
CURRENT_AGREE_REL = 0.25     # two calculated currents must agree this well...
CURRENT_AGREE_ABS_MA = 0.02  # ...or be this close


def calculate_current(rows, floating, current_from):
    """Current through the lesson's known resistors, from the voltage on
    both sides: I = (V1 - V2) / R. Example: (5 - 2) / 220 = 13.6 mA.

    A resistor with an end in a floating row carries no current. With two
    or more known resistors in a line, their answers must agree; if they
    do not, one of them is not what the lesson expects, so the answer is
    None ("not sure") instead of a guess.
    Returns (current_ma or None, reason).
    """
    estimates = []
    for r in current_from:
        a, b = r["rows"]
        if a not in rows or b not in rows:
            return None, f"the board does not measure {', '.join(x for x in (a, b) if x not in rows)}"
        if a in floating or b in floating:
            estimates.append(0.0)
        else:
            estimates.append((rows[a] - rows[b]) / r["ohms"] * 1000)
    if not estimates:
        return None, "the lesson has no known resistor"
    lo, hi = min(estimates), max(estimates)
    if hi - lo > max(CURRENT_AGREE_ABS_MA, CURRENT_AGREE_REL * max(abs(hi), abs(lo))):
        return None, "the resistors do not agree, so a part is not what the lesson expects"
    return round(max(0.0, sum(estimates) / len(estimates)), 3), "calculated"


def summarize_rows(readings, lesson):
    """Average a list of version 2 readings for one lesson.

    Returns {"rows", "spread", "floating", "supply_v", "power_on",
    "current_ma", "current_source", "current_note", "missing_rows"}.
    current_source is "measured" (a current sensor), "calculated" (from
    the rows, see calculate_current) or "unknown".
    """
    wanted = lesson.get("rows", [])
    measured = set.intersection(*(set(r["rows"]) for r in readings)) if readings else set()
    rows, spread, floating = {}, {}, []
    for name in wanted:
        if name not in measured:
            continue
        values = [r["rows"][name] for r in readings]
        rows[name] = round(statistics.mean(values), 3)
        spread[name] = round(max(values) - min(values), 3)
        if sum(name in r.get("floating", []) for r in readings) > FLOAT_SHARE * len(readings):
            floating.append(name)
    summary = {
        "rows": rows, "spread": spread, "floating": floating,
        "supply_v": round(statistics.mean(r["supply_v"] for r in readings), 3),
        "power_on": all(r["power_on"] for r in readings),
        "missing_rows": [n for n in wanted if n not in measured],
    }
    currents = [r.get("current_ma") for r in readings]
    if currents and all(c is not None for c in currents):
        summary.update(current_ma=round(statistics.mean(currents), 3),
                       current_source="measured", current_note="measured by the board")
    else:
        value, note = calculate_current(rows, floating, lesson.get("current_from", []))
        summary.update(current_ma=value, current_note=note,
                       current_source="calculated" if value is not None else "unknown")
    return summary


def matches_rows(summary, rule):
    """Does a summary fit a rule? A rule can have "rows" (name -> range),
    "floating" and "driven" (lists of names), "current_ma" and "supply_v"
    (ranges). Anything the board did not measure never matches."""
    for name, rng in rule.get("rows", {}).items():
        if name not in summary["rows"] or name in summary["floating"]:
            return False
        if not in_range(summary["rows"][name], rng):
            return False
    for name in rule.get("floating", []):
        if name not in summary["rows"] or name not in summary["floating"]:
            return False
    for name in rule.get("driven", []):
        if name not in summary["rows"] or name in summary["floating"]:
            return False
    if "current_ma" in rule:
        if summary.get("current_ma") is None or not in_range(summary["current_ma"], rule["current_ma"]):
            return False
    if "supply_v" in rule and not in_range(summary["supply_v"], rule["supply_v"]):
        return False
    return True


def check_rows(summary, lesson):
    """Decide what a row summary means for a lesson.

    "status" is one of:
      "power_off"    : power is off, nothing to measure
      "missing_rows" : the board does not measure a row this lesson needs
      "mistake"      : a known mistake ("mistake_id", "action", "rows")
      "ok"           : the circuit is correct
      "unknown"      : wrong in a way the lesson does not list ("clue")
    "not_checked" lists mistakes that need a current sensor this board
    does not have, so they could not be looked for.
    """
    if not summary["power_on"]:
        return {"status": "power_off"}
    if summary["missing_rows"]:
        names = ", ".join(summary["missing_rows"])
        return {"status": "missing_rows", "missing_rows": summary["missing_rows"],
                "message": f"This board does not measure {names}, and lesson "
                           f"{lesson['id']} needs {'it' if len(summary['missing_rows']) == 1 else 'them'}. "
                           "I cannot check this lesson on this board."}
    not_checked = []
    for m in lesson["mistakes"]:
        if m.get("needs_current_sensor") and summary.get("current_source") != "measured":
            not_checked.append(m["id"])
            continue
        if matches_rows(summary, m["detect_rows"]):
            return {"status": "mistake", "mistake_id": m["id"], "action": m["action"],
                    "rows": list(m.get("rows", []))}
    extra = {"not_checked": not_checked} if not_checked else {}
    if matches_rows(summary, lesson["accept_rows"]):
        return {"status": "ok", **extra}
    return {"status": "unknown", "clue": clue_rows(summary, lesson), **extra}


def clue_rows(summary, lesson):
    """Explain an unknown mistake from the row voltages alone.

    For every lesson row: is it empty (floating), or is its voltage off,
    and which way? Plus the current, but only when a sensor measured it
    (a calculated current comes from these same rows, so it adds
    nothing). Plain words, never a specific fix.
    Returns {"text", "details": [{"number", "row", "direction",
    "measured", "expected", "meaning"}]}, most important first.
    """
    expected = lesson.get("expected", {})
    exp_rows = expected.get("rows", {})
    accept = lesson.get("accept_rows", {}).get("rows", {})
    resistor = lesson.get("resistor_words", "the resistor")
    supply = summary.get("supply_v", 5.0)
    found = []

    def add(importance, number, row, direction, measured, exp, meaning):
        found.append((importance, {"number": number, "row": row, "direction": direction,
                                   "measured": measured, "expected": exp,
                                   "meaning": meaning}))

    for name, exp in exp_rows.items():
        if name not in summary["rows"]:
            continue
        label = row_label(name)
        Label = label[0].upper() + label[1:]
        got = summary["rows"][name]
        if name in summary["floating"]:
            add(3, f"voltage in {label}", name, "empty", None, exp,
                f"{Label} has nothing connected to it. A part that should "
                f"reach {label} is not touching it.")
            continue
        rng = accept.get(name) or {"min": exp - VOLTAGE_TOLERANCE, "max": exp + VOLTAGE_TOLERANCE}
        low, high = rng.get("min", float("-inf")), rng.get("max", float("inf"))
        if got < low:
            if got < 0.2:
                add(2, f"voltage in {label}", name, "almost none", got, exp,
                    f"{Label} has almost 0 volts. That usually means it has no path "
                    "to the power supply, or it is joined straight to ground.")
            else:
                add(1, f"voltage in {label}", name, "lower", got, exp,
                    f"{Label} has less voltage than it should. Less of the push "
                    "from the power supply is reaching that row.")
        elif got > high:
            if got > supply - 0.2:
                add(2, f"voltage in {label}", name, "almost all", got, exp,
                    f"{Label} has almost the full push of the power supply. That "
                    "usually means no current is flowing past that row, so nothing "
                    "lowers the voltage.")
            else:
                add(1, f"voltage in {label}", name, "higher", got, exp,
                    f"{Label} has more voltage than it should. More of the push "
                    "from the power supply is reaching that row.")

    if summary.get("current_source") == "measured" and "current_ma" in expected:
        exp = expected["current_ma"]
        got = max(0.0, summary["current_ma"])
        crange = lesson.get("accept_rows", {}).get("current_ma")
        low, high = ((crange["min"], crange["max"]) if crange else
                     (exp * (1 - CURRENT_TOLERANCE), exp * (1 + CURRENT_TOLERANCE)))
        if got < low:
            if got < 0.1 * exp:
                add(3, "current", None, "almost none", got, exp,
                    "Almost no current is flowing. That usually means the loop is "
                    "broken somewhere, so current cannot get all the way around.")
            elif got < 0.5 * exp:
                add(2, "current", None, "much lower", got, exp,
                    "The current is much lower than it should be. Something is "
                    f"holding it back more than {resistor} would.")
            else:
                add(1, "current", None, "a little lower", got, exp,
                    "The current is a little lower than it should be. Something is "
                    f"holding it back a bit more than {resistor} would.")
        elif got > high:
            if got > 2 * exp:
                add(2, "current", None, "much higher", got, exp,
                    "The current is much higher than it should be. It may have found "
                    f"an easier path than through {resistor}.")
            else:
                add(1, "current", None, "a little higher", got, exp,
                    "The current is a little higher than it should be. Something is "
                    f"letting it through more easily than {resistor} would.")

    if supply < LOW_SUPPLY_V:
        add(1, "power voltage", None, "lower", supply, 5.0,
            "The power voltage is lower than 5 volts. The power supply may be "
            "having trouble keeping up.")

    found.sort(key=lambda f: -f[0])
    details = [d for _, d in found]
    if details:
        text = " ".join(d["meaning"] for d in details[:2])
    else:
        text = ("The numbers are close to right, but not quite there yet. "
                "Look over the circuit once more, one part at a time.")
    return {"text": text, "details": details}
