"""Free build, phase 2: what Zip says about a circuit it did not plan.

feedback_items() turns the physics-checked guess (agent/reader.py) and the
measurements into a short list, most important first. Each item:

    {"kind", "rows", "light", "text", "level"}

kind: led_on, led_backwards, led_too_little, led_off, short, short_supply,
too_much_current, current_high, current_ok, does_nothing, working,
open_path, goal_met, goal_not_met, goal_press, goal_unknown, not_sure.
light: "red" (likely problem), "yellow" (possible problem), "green" (a
working part) or None. Only usable rows are ever in "rows".
level: "problem", "maybe", "good" or "info".
text: plain words for a 6th grader, hint first: it points at the rows and
what the board sees, never the whole fix.

Plain Python: the AI tutor words its reply from these, and the offline
tutor says them as they are.
"""

import re

from agent.lessons import SAFETY_MAX_MA
from protocol import is_row, low_supply_v, row_label
from sim import solver

LOW_SCORE = 0.6       # a guess matching less than this: "I am not sure"
NEAR_LIMIT_MA = 20.0  # above this: close to the limit
FLOWS_MA = 0.5
ZERO_V = 0.05
LEVELS = ("problem", "maybe", "good", "info")


def _cap(text):
    return text[:1].upper() + text[1:]


def _rows_words(rows):
    names = [row_label(r) for r in rows if is_row(r)]
    if not names:
        return "the + and - lines"
    if len(names) == 1:
        return names[0]
    return f"{names[0]} and {names[1]}"


def _volts(row, measured, supply):
    if row in ("PT+", "PB+"):
        return supply
    if row in ("PT-", "PB-"):
        return 0.0
    return measured["rows"].get(row)


def _name(part):
    if part["kind"] == "led":
        return f"the {part.get('color', 'red')} LED"
    if part["kind"] == "resistor":
        o = part.get("ohms") or 0
        return f"the {o / 1000:g}k ohm resistor" if o >= 1000 else f"the {o:g} ohm resistor"
    if part["kind"] == "button":
        return "the push button"
    if part["kind"] == "wire":
        return "a wire"
    return "a part"


def _place(place):
    if place in ("PT+", "PB+"):
        return "the + line"
    if place in ("PT-", "PB-"):
        return "the - line"
    if place in solver.UNSEEN:
        return "a row I cannot measure"
    return row_label(place).replace(" (lower half)", "")


def problem_text(problem, parts):
    """A usual mistake in a hypothesis (agent/reader.problems_of), hint
    first: where to look and a question, never the whole fix."""
    p = parts[problem["part"]]
    a, b = sorted(p["rows"], key=lambda r: not is_row(r))  # the row first, then the line
    if problem["kind"] == "led_backwards":
        return (f"{_cap(_name(p))} between {_place(a)} and {_place(b)} is blocking the "
                "current: which way does its long leg go?")
    if problem["kind"] == "missing_link":
        return (f"{_cap(_name(p))} in {_place(problem.get('row') or a)} may have a leg in a row "
                "I cannot measure, so no current flows: is its other leg in the row you think?")
    if problem["kind"] == "button_open":
        return (f"The push button between {_place(a)} and {_place(b)} is not pressed, so no "
                "current flows: press it and keep it pressed, then press Check.")
    return ""


def feedback_items(reading, measured, usable, tray=None, plan=None):
    """reading: read_and_check's answer (or None: no reader). measured: what
    the board measured (agent/reader.measured_of). usable: usable rows.
    plan: the experiment that tells the matching hypotheses apart (or None)."""
    usable = set(usable)
    items = []

    def add(kind, level, text, rows=(), light=None):
        rows = [r for r in rows if is_row(r) and r in usable]
        items.append({"kind": kind, "level": level, "text": text, "rows": rows,
                      "light": light if rows else None})

    supply = measured.get("supply_v") or measured.get("nominal_v") or solver.SUPPLY_V
    nominal = measured.get("nominal_v") or solver.SUPPLY_V  # the board's own supply
    low = low_supply_v(nominal)  # 2.8 V at 3.3 V
    current = measured.get("current_ma")
    sensor = measured.get("current_source") == "measured" and current is not None
    driven = [r for r, v in measured["rows"].items() if v is not None]

    # Measurement alone (no guess needed): the current and the supply.
    if sensor and current > SAFETY_MAX_MA:
        add("too_much_current", "problem",
            f"{current:.0f} mA flowed: more than {SAFETY_MAX_MA:.0f} mA is too much, so the "
            "power was switched off. Look for an LED with no resistor, or a wire from + "
            "straight to -.")
    elif measured.get("supply_v") is not None and supply < low and driven:
        add("short_supply", "problem",
            f"The + line dropped to {supply:.1f} volts: something joins + and - almost "
            "directly (a short), or an LED has no resistor.")
    elif sensor and current > NEAR_LIMIT_MA:
        add("current_high", "maybe",
            f"{current:.0f} mA flows. That is close to the {SAFETY_MAX_MA:.0f} mA limit: a bigger "
            "resistor would be safer.")
    elif sensor and current > FLOWS_MA:
        add("current_ok", "info", f"{current:.1f} mA flows, safely under {SAFETY_MAX_MA:.0f} mA.")

    guess = (reading or {}).get("guess")
    score = (reading or {}).get("score", 0.0)
    # A guess that leaves a row with a voltage unexplained is not used.
    explains = (reading or {}).get("explains_all", True)
    if not guess or not guess.get("parts") or score < LOW_SCORE or not explains:
        tray_button = any(p.get("name") == "push button" for p in (tray or {}).get("parts", []))
        if not driven and tray_button:
            add("not_sure", "maybe", "Nothing I can measure has power yet. If the push button "
                "is in the path, press it and keep it pressed, then press Check.")
        elif driven:
            ask = ("Tap the parts you used in \"What parts did you use?\" and press Check again."
                   if not (tray and tray.get("parts")) else
                   "Is every part in the rows you think? Press Check again after you look.")
            add("not_sure", "maybe", f"I am not sure what you built. {ask}")
        return _sorted(items)

    parts = guess["parts"]
    pred = reading.get("predicted") or {"rows": {}, "leds_on": {}}
    leds_on = {int(k): v for k, v in (pred.get("leds_on") or {}).items()}
    total_flows = (current > FLOWS_MA) if sensor else (pred.get("current_ma", 0) > FLOWS_MA)
    floating = {r for r, v in measured["rows"].items() if v is None}
    said_open = set()

    # The usual mistakes in the most likely hypothesis come first (hint first).
    told = set()
    for problem in (reading or {}).get("problems") or []:
        p = parts[problem["part"]]
        level, light = ("problem", "red") if problem["kind"] == "led_backwards" else ("maybe", "yellow")
        add(problem["kind"], level, problem_text(problem, parts), p["rows"], light)
        told.add(problem["part"])
    # Other hypotheses that fit just as well, and the experiment that tells them apart.
    for other in ((reading or {}).get("hypotheses") or [])[1:]:
        other_parts = other["guess"]["parts"]
        for problem in other.get("problems") or []:
            add("could_be", "info", "It could also be this: " +
                problem_text(problem, other_parts)[0].lower() + problem_text(problem, other_parts)[1:])
            break
    if plan:
        add("experiment", "info", f"To find out which, try this: {plan['instruction']}")

    # Shorts: a wire across the two legs of another part.
    shorted = set()
    for i, w in enumerate(parts):
        if w["kind"] != "wire":
            continue
        for j, p in enumerate(parts):
            if j != i and p["kind"] in ("led", "resistor", "button") and \
                    set(p["rows"]) == set(w["rows"]):
                shorted.add(j)
                add("short", "problem",
                    f"Look at {_rows_words(p['rows'])}: a wire joins the two legs of "
                    f"{_name(p)}, so the current goes around it (a short).", p["rows"], "red")

    for i, p in enumerate(parts):
        if i in shorted or i in told or p["kind"] in ("wire", "unknown"):
            continue
        a, b = p["rows"]
        va, vb = _volts(a, measured, supply), _volts(b, measured, supply)
        if p["kind"] == "led":
            vf = solver.LED_VF.get(p.get("color", "red"), 1.9)
            if leds_on.get(i) and va is not None and vb is not None:
                dim = ""
                if p.get("color") in ("blue", "white") and vf > 0.85 * nominal:
                    # At 3.3 V a blue or white LED (about 3 V) is left with
                    # almost nothing for its resistor: it only glows a little.
                    dim = (f" It needs almost all of the {nominal:g} volts, so it only "
                           "glows a little. Red, yellow and yellow-green LEDs are brighter "
                           "here.")
                add("led_on", "good", f"{_cap(_name(p))} in {_rows_words(p['rows'])} is on: "
                    f"it has {va - vb:.1f} volts across it and current flows through it.{dim}",
                    p["rows"], "green")
            elif va is None or vb is None:
                empty = a if va is None else b
                said_open.add(empty)
                add("open_path", "maybe",
                    f"{_cap(_name(p))} in {_rows_words(p['rows'])} stays off: "
                    f"{row_label(empty)} has no path to + or -. Is a leg in the wrong row?",
                    p["rows"], "yellow")
            elif vb - va > ZERO_V * 10:
                # The board cannot tell an LED in backwards from a leg in the
                # wrong row: both stop the current. Say both, hint first.
                add("led_backwards", "problem",
                    f"{_cap(_name(p))} in {_rows_words(p['rows'])} stays off and no current "
                    f"flows. Is its long leg (+) on the side with more voltage, {row_label(b)}? "
                    "Or is one of its legs not in the row you think?", p["rows"], "red")
            elif va - vb < vf - 0.2:
                add("led_too_little", "maybe",
                    f"{_cap(_name(p))} in {_rows_words(p['rows'])} has only {va - vb:.1f} "
                    f"volts across it, but it needs about {vf:.1f} volts to light.",
                    p["rows"], "yellow")
            else:
                add("led_off", "maybe", f"{_cap(_name(p))} in {_rows_words(p['rows'])} is "
                    "off: no current flows through it.", p["rows"], "yellow")
            continue
        if p["kind"] == "resistor":
            through = None
            pa, pb = pred["rows"].get(a), pred["rows"].get(b)
            pa = supply if a in ("PT+", "PB+") else 0.0 if a in ("PT-", "PB-") else pa
            pb = supply if b in ("PT+", "PB+") else 0.0 if b in ("PT-", "PB-") else pb
            if pa is not None and pb is not None:
                through = abs(pa - pb) / p["ohms"] * 1000
            if through is not None and through > FLOWS_MA / 10:
                add("working", "good", f"{_cap(_name(p))} in {_rows_words(p['rows'])} "
                    f"carries about {through:.1f} mA.", p["rows"], "green")
            elif total_flows:
                add("does_nothing", "maybe",
                    f"{_cap(_name(p))} in {_rows_words(p['rows'])} has no current through "
                    "it, so it does nothing: is it part of the loop?", p["rows"], "yellow")

    # Open paths: a row a guessed part uses, with nothing driving it.
    for r in sorted({r for p in parts for r in p["rows"] if r in floating} - said_open):
        add("open_path", "maybe", f"{_cap(row_label(r))} has nothing driving it: the "
            "path is broken there. Is a leg in the wrong row?", [r], "yellow")

    goal = (tray or {}).get("goal") or ""
    if goal:
        _goal(goal, parts, leds_on, add)
    return _sorted(items)


def _goal(goal, parts, leds_on, add):
    low = goal.lower()
    colors = [c for c in ("red", "yellow", "green", "blue", "white") if c in low]
    wants_light = re.search(r"\b(light|lights|lit|glow|glows|shine|shines|led|leds|on|bright)\b",
                            low) or colors
    on = [p for i, p in enumerate(parts) if p["kind"] == "led" and leds_on.get(i)]
    buttons = [p for p in parts if p["kind"] == "button"]
    if wants_light:
        if colors:
            on = [p for p in on if p.get("color") in colors]
        if on:
            add("goal_met", "good", f"Your goal: \"{goal}\". Yes: {_name(on[0])} is on.")
        elif re.search(r"\b(press|button|push)\b", low) and buttons:
            add("goal_press", "info", f"Your goal: \"{goal}\". Press the button and keep it "
                "pressed, then press Check.")
        else:
            add("goal_not_met", "maybe", f"Your goal: \"{goal}\". Not yet: "
                + (f"no {colors[0]} LED is on." if colors else "no LED is on."))
    else:
        add("goal_unknown", "info", f"Your goal: \"{goal}\". I cannot check that by measuring. "
            "Does it work?")


def _sorted(items):
    return sorted(items, key=lambda i: LEVELS.index(i["level"]))


def main_problem(items):
    """The most important problem (or possible problem), or None."""
    return next((i for i in items if i["level"] in ("problem", "maybe")), None)
