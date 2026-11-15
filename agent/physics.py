"""Physics facts from the board's measurements (plain Python, no AI).

observations(summary, lesson) turns one check's numbers into a short list
of facts, each one plain sentence a 6th grader can follow, for example:

    F4  part_drop          The 220 ohm resistor has 1.3 volts across it
                           (row 5 to row 10). The plan expects about 1.3 volts.
    F5  resistor_current   1.3 volts across 220 ohm means about 5.9 mA flows
                           through it. The board measured 6.0 mA.

The diagnoser (agent/diagnoser.py) reasons only from these facts and must
name the facts behind every cause, so Python can check the answer is
grounded. Use the lesson AFTER placement (agent/placement.py), so the
facts name the rows the student really used.

Each fact: {"id": "F1", "kind", "rows": [...], "values": {...}, "text"}.
Kinds: power, plan_row_floating, row_voltage, part_drop, resistor_current,
led_state, same_voltage, current_vs_expected, extra_rows, flicker.
"""

import re

from protocol import SUPPLY_V, low_supply_v, row_label

PLUS, MINUS = ("PT+", "PB+"), ("PT-", "PB-")
# The + line's voltage when nothing pulls it down: the board's own supply
# (summary["nominal_v"], from its hello line), else this (3.3 V, the station).
EXPECTED_SUPPLY_V = SUPPLY_V
LED_ON = (1.5, 2.5)      # volts across a lit red LED
LED_BLOCKED_V = 2.5      # more than this across it, and no current: blocked
SHORTED_V = 0.3          # less than this across a part: current goes around it
CURRENT_FLOWS_MA = 1.0
SAME_V = 0.15            # two rows this close read "the same"
SHOULD_DIFFER_V = 1.0    # ...when the plan says they differ by more than this
# The + line pulled below this share of the supply (2.8 V at 3.3 V): a big
# current flows. low_supply_v() in protocol.py.
LOW_SUPPLY_V = low_supply_v(SUPPLY_V)


def _supply(summary):
    """The supply this summary's board has (its hello line), else 3.3 V."""
    v = (summary or {}).get("nominal_v")
    return float(v) if isinstance(v, (int, float)) and v > 0 else EXPECTED_SUPPLY_V


def part_kind(part):
    n = part.lower()
    for kind in ("resistor", "led", "button", "wire"):
        if kind in n:
            return kind
    return "part"


def ohms_of(part, lesson):
    """A resistor's value: from current_from (by part name), else its name
    ("220 ohm resistor" -> 220, "Top 10k resistor" -> 10000)."""
    for r in lesson.get("current_from", []):
        if r.get("part") == part:
            return float(r["ohms"])
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k)?\s*(?:ohm)?", part, re.I)
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2) else 1)


def _words(rows):
    """["T5", "T10"] -> "rows 5 and 10"; a rail -> "the top + line"."""
    names = [row_label(r) for r in rows]
    if len(names) == 1:
        return names[0]
    if all(n.startswith("row ") for n in names):
        bare = [n[4:] for n in names]
        return "rows " + ", ".join(bare[:-1]) + " and " + bare[-1]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _tidy(x):
    return 0.0 if abs(x) < 0.05 else x  # no "-0.0"


def _v(x):
    return f"{_tidy(x):.1f} volts"


def _ma(x):
    x = 0.0 if abs(x) < 0.005 else x
    return f"{x:.2f} mA" if abs(x) < 1 else f"{x:.1f} mA"


def _cap(text):
    return text[:1].upper() + text[1:]


def _measured(summary, row):
    """The voltage on a row (the + and - lines are the supply and ground),
    or None when nothing is connected or the board does not measure it."""
    if row in PLUS:
        return summary.get("supply_v")
    if row in MINUS:
        return 0.0
    if row in summary.get("floating", []) or row not in summary.get("rows", {}):
        return None
    return summary["rows"][row]


def _expected(lesson, row, supply=EXPECTED_SUPPLY_V):
    if row in PLUS:
        return supply
    if row in MINUS:
        return 0.0
    return lesson.get("expected", {}).get("rows", {}).get(row)


def power_fact(summary, safety_cut=False):
    on = bool(summary.get("power_on"))
    supply = summary.get("supply_v") or 0.0
    if safety_cut:
        text = (f"The power was on ({_v(supply)}) but has been switched off to keep the "
                "parts safe.")
    elif on:
        text = f"The power is on: the + line has {_v(supply)}."
        if supply < low_supply_v(_supply(summary)):
            text = (f"The power is on, but the + line has only {_v(supply)} instead of "
                    f"{_supply(summary):g}: something is pulling too much current.")
    else:
        text = "The power is off, so nothing can be measured."
    return {"kind": "power", "rows": [], "text": text,
            "values": {"on": on, "supply_v": round(supply, 2), "safety_cut": bool(safety_cut)}}


def extra_rows_fact(rows):
    verb = "is" if len(rows) == 1 else "are"
    return {"kind": "extra_rows", "rows": list(rows), "values": {},
            "text": f"Something is connected in {_words(rows)}, which {verb} not in the plan."}


def circuit_facts(summary, lesson, label=""):
    """The facts about one circuit (one copy of it in lesson 3)."""
    facts = []
    pre = f"{label}: " if label else ""
    plan = list(lesson.get("rows", []))
    floating = set(summary.get("floating", []))
    current = summary.get("current_ma")
    source = summary.get("current_source")
    supply = _supply(summary)

    def add(kind, rows, values, text):
        facts.append({"kind": kind, "rows": list(rows), "values": values, "text": pre + text})

    for row in plan:
        if row in floating:
            add("plan_row_floating", [row], {},
                f"Nothing is connected in {row_label(row)}, but the plan uses it.")
    for row in plan:
        m, e = _measured(summary, row), _expected(lesson, row, supply)
        if m is None or e is None:
            continue
        add("row_voltage", [row], {"measured": round(m, 2), "expected": e},
            f"{_cap(row_label(row))} reads {_v(m)}. The plan expects about {_v(e)}.")

    for part in lesson.get("layout", []):
        if len(part["legs"]) != 2:
            continue
        a, b = part["legs"][0]["row"], part["legs"][1]["row"]
        kind, name = part_kind(part["part"]), part["part"]
        the = ("the " + name[0].lower() + name[1:] if name[:1].isupper() and name[1:2].islower()
               else "the " + name)
        ma, mb = _measured(summary, a), _measured(summary, b)
        ea, eb = _expected(lesson, a, supply), _expected(lesson, b, supply)
        rows = [r for r in (a, b) if not r.startswith("P")]
        if ma is None or mb is None:
            empty = [r for r in (a, b) if _measured(summary, r) is None]
            add("part_drop", rows, {"drop": None},
                f"{_cap(the)} cannot be measured: {_words(empty)} "
                f"{'has' if len(empty) == 1 else 'have'} nothing connected.")
            continue
        drop = ma - mb
        expect = None if ea is None or eb is None else round(ea - eb, 2)
        tail = f" The plan expects about {_v(abs(expect))}." if expect is not None else ""
        add("part_drop", rows, {"drop": round(drop, 2), "expected": expect},
            f"{_cap(the)} has {_v(abs(drop))} across it ({_words([a, b])}).{tail}")
        if kind == "resistor":
            ohms = ohms_of(name, lesson)
            if ohms:
                through = abs(drop) / ohms * 1000
                value = "" if re.search(r"\d", name) else f" ({ohms:g} ohm)"
                text = (f"{_v(abs(drop))} across {the}{value} means about "
                        f"{_ma(through)} flows through it.")
                if source == "measured" and current is not None:
                    text += f" The board measured {_ma(current)}."
                add("resistor_current", rows, {"ma": round(through, 3), "ohms": ohms,
                                                "measured_ma": current if source == "measured"
                                                else None}, text)
        if kind == "led":
            flows = current is not None and current > CURRENT_FLOWS_MA
            if LED_ON[0] <= drop <= LED_ON[1] and flows:
                state, text = "on", (f"The LED has {_v(drop)} across it and current flows, "
                                     "so it should be lit.")
            elif drop > LED_BLOCKED_V and (current is None or current < CURRENT_FLOWS_MA):
                state, text = "blocked", (f"The LED has {_v(drop)} across it but almost no "
                                          "current flows: it is blocked (backwards, missing "
                                          "or open).")
            elif abs(drop) < SHORTED_V and flows:
                state, text = "shorted", (f"The LED has almost no voltage across it "
                                          f"({_v(abs(drop))}) while current flows: the current "
                                          "goes around it (shorted or bypassed).")
            elif abs(drop) < SHORTED_V:
                # With no current every part has 0 V across it: that says nothing.
                state, text = "no_current", (f"No current flows yet, so the LED's "
                                             f"{_v(abs(drop))} across it tells nothing about it.")
            else:
                state, text = "unclear", (f"The LED has {_v(drop)} across it; that is not "
                                          "what a lit LED shows.")
            add("led_state", rows, {"state": state, "drop": round(drop, 2)}, text)

    # Two rows that should differ but read the same are only "joined" when
    # current flows (or the supply is pulled down, which needs a big current
    # even when no sensor sees it). With no current, every part has 0 V
    # across it, so the same voltage tells nothing.
    pulled_down = (summary.get("supply_v") or supply) < low_supply_v(supply)
    joined = (current is not None and current > CURRENT_FLOWS_MA) or pulled_down
    known = [r for r in plan if _measured(summary, r) is not None and
             _expected(lesson, r, supply) is not None]
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            ea, eb = _expected(lesson, a, supply), _expected(lesson, b, supply)
            if abs(ea - eb) > SHOULD_DIFFER_V and \
                    abs(_measured(summary, a) - _measured(summary, b)) <= SAME_V:
                should = _v(abs(ea - eb))
                same = f"{_cap(_words([a, b]))} read the same ({_v(_measured(summary, a))})"
                text = (f"{same} but should differ by about {should}: they may be joined "
                        "together." if joined else
                        f"{same}, but no current flows yet, so that tells nothing.")
                add("same_voltage", [a, b], {"volts": round(_measured(summary, a), 2),
                                            "joined": joined}, text)

    expected_ma = lesson.get("expected", {}).get("current_ma")
    if current is not None and expected_ma is not None:
        how = (f"The board measured {_ma(current)} of current." if source == "measured" else
               f"Worked out from the rows, the current is about {_ma(current)}.")
        if expected_ma > 0:
            pct = round((current - expected_ma) / expected_ma * 100)
            change = (f"{abs(pct)}% {'more' if pct > 0 else 'less'}" if abs(pct) >= 5
                      else "about right")
            text = f"{how} The plan expects about {_ma(expected_ma)} ({change})."
        else:
            pct = None
            text = f"{how} The plan expects no current."
        add("current_vs_expected", [], {"ma": round(current, 2), "expected_ma": expected_ma,
                                        "percent": pct, "source": source}, text)

    for row in summary.get("flickering", []):
        add("flicker", [row], {},
            f"{_cap(row_label(row))} keeps switching between connected and empty: "
            "a leg may be loose.")
    return facts


def free_facts(summary, safety_cut=False):
    """Free build (no plan): the facts that need no plan. The power, the
    voltage of every row with something in it, and the current."""
    facts = [power_fact(summary, safety_cut)]
    if summary.get("power_on") or safety_cut:
        floating = set(summary.get("floating", []))
        for row, v in summary.get("rows", {}).items():
            if row in floating:
                continue
            facts.append({"kind": "row_voltage", "rows": [row],
                          "values": {"measured": round(v, 2), "expected": None},
                          "text": f"{_cap(row_label(row))} reads {_v(v)}."})
        current, source = summary.get("current_ma"), summary.get("current_source")
        if current is None:
            text = "This board has no current sensor, so the current is not known."
        elif source == "measured":
            text = f"The board measured {_ma(current)} of current."
        else:
            text = f"Worked out from the rows, the current is about {_ma(current)}."
        facts.append({"kind": "current", "rows": [],
                      "values": {"ma": None if current is None else round(current, 2),
                                 "source": source}, "text": text})
    return number(facts)


def number(facts):
    """Give the facts their ids, F1, F2, ... (in place). Returns them."""
    for i, f in enumerate(facts, 1):
        f["id"] = f"F{i}"
        facts[i - 1] = {"id": f["id"], **{k: v for k, v in f.items() if k != "id"}}
    return facts


def observations(summary, lesson, extra_rows=None, safety_cut=False):
    """Every fact for one check: the power, then the circuit, then extra rows."""
    facts = [power_fact(summary, safety_cut)]
    if summary.get("power_on") or safety_cut:
        facts += circuit_facts(summary, lesson)
        if extra_rows:
            facts.append(extra_rows_fact(extra_rows))
    return number(facts)
