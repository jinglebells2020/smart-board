"""Random "judge" circuits for free build (fake board only).

random_circuit(seed, parts, usable_rows) builds a random but plausible
circuit from 2 to 5 parts, on usable rows only: a chain from a + line to a
- line (wires, resistors of 100 ohm to 10k, LEDs of any colour, sometimes
a push button), sometimes two LED branches side by side. Sometimes it adds
a typical mistake: an LED in backwards, a missing link (a leg in the wrong
row), a short (a wire across a part) or an LED with no resistor (then the
safety cut must switch the power off).

At the station's 3.3 V (protocol.SUPPLY_V) two LEDs in a row cannot light
(1.9 + 1.9 = 3.8 V), so that chain is only built on a bigger supply, and the
LED with no resistor is red, yellow or green: a blue one at 3.3 V draws
too little to be a danger. Any other LED may still be any colour.

The returned netlist is the truth, for tests and for the judge only. It is
never given to the tutor: the tutor only ever sees what the board measures.
"""

import random

from sim import solver

RESISTORS = [100, 220, 330, 1000, 10000]
LED_RESISTORS = [220, 330, 1000]  # keep a correct circuit's current safe
COLORS = ["red", "yellow", "green", "blue"]
# The colours that light well at 3.3 V (the lessons use only these).
BRIGHT_AT_3V3 = ["red", "yellow", "green"]
MISTAKES = ["led_backwards", "missing_link", "short", "no_resistor"]
MISTAKE_CHANCE = 0.4
SAFE_MA = 25.0  # a circuit with no mistake stays below this (the cut is at 30)


def _groups(usable):
    """{half: sorted row numbers} for the usable rows."""
    out = {}
    for r in usable:
        if r[0] in "TB" and r[1:].isdigit():
            out.setdefault(r[0], []).append(int(r[1:]))
    return {h: sorted(ns) for h, ns in out.items()}


def _two_leds_fit(supply=solver.SUPPLY_V):
    """Can two LEDs in a row both light on this supply?"""
    return supply > 2 * min(solver.LED_VF.values()) + 0.3


def _chain_kinds(rng, n):
    """The parts of one chain, + side first."""
    if n == 2:
        return rng.choice([["resistor", "led"], ["resistor", "resistor"]])
    if n == 3:
        return rng.choice([["wire", "resistor", "led"], ["resistor", "led", "wire"],
                           ["resistor", "resistor", "resistor"], ["button", "resistor", "led"]])
    if n == 4:
        return rng.choice([["wire", "resistor", "led", "wire"],
                           ["wire", "button", "resistor", "led"],
                           ["wire", "resistor", "resistor", "wire"]])
    five = [["wire", "button", "resistor", "led", "wire"],
            ["wire", "resistor", "led", "led", "wire"],
            ["wire", "resistor", "resistor", "led", "wire"]]
    if not _two_leds_fit():  # 3.3 V: two LEDs in a row would both stay dark
        five.pop(1)
    return rng.choice(five)


def _part(rng, kind, a, b, led_chain):
    part = {"kind": kind, "rows": [a, b]}
    if kind == "resistor":
        part["ohms"] = rng.choice(LED_RESISTORS if led_chain else RESISTORS)
    elif kind == "led":
        part["color"] = rng.choice(COLORS)
    # A push button has no "closed": it follows HOLD on / off, like a finger.
    return part


def _chain(rng, kinds, half, rows):
    """One chain of parts along these rows (len(kinds) - 1 of them)."""
    stops = [f"P{half}+"] + [f"{half}{n}" for n in rows] + [f"P{half}-"]
    led_chain = "led" in kinds
    return [_part(rng, k, stops[i], stops[i + 1], led_chain) for i, k in enumerate(kinds)]


def random_circuit(seed=None, parts=3, usable_rows=None, mistake="random"):
    """{"seed", "parts" (the true netlist), "mistake" (or None)}.
    mistake: "random" (sometimes one), None (never), or one of MISTAKES."""
    rng = random.Random(seed)
    n = max(2, min(5, int(parts)))
    usable = usable_rows or [f"{h}{i}" for h in "TB" for i in range(1, 64)]
    groups = _groups(usable)
    if mistake == "random":
        mistake = rng.choice(MISTAKES) if rng.random() < MISTAKE_CHANCE else None
    parallel = n >= 4 and rng.random() < 0.3 and mistake != "no_resistor"
    need = 2 if parallel else n - 1  # rows the chain(s) need
    # The missing link needs one more row; pick a half with enough rows.
    halves = [h for h, ns in groups.items() if len(ns) >= need + 1 + (mistake == "missing_link")]
    if not halves:
        halves = [h for h, ns in groups.items() if len(ns) >= need]
        if mistake == "missing_link":
            mistake = None
    if not halves:
        raise ValueError(f"not enough usable rows for {n} parts")
    half = rng.choice(halves)
    free = list(groups[half])
    if parallel:
        # Two LED branches side by side, each with its own resistor.
        a, b = sorted(rng.sample(free, 2))
        netlist = _chain(rng, ["resistor", "led"], half, [a]) + \
            _chain(rng, ["resistor", "led"], half, [b])
        if n == 5:
            c = rng.choice([r for r in free if r not in (a, b)] or [a])
            netlist.append({"kind": "resistor", "rows": [f"P{half}+", f"{half}{c}"],
                            "ohms": 10000})
    else:
        kinds = _chain_kinds(rng, n)
        if mistake == "no_resistor":
            # Exactly one LED, straight between the + and - lines (wires and
            # maybe a button around it): far too much current flows.
            # (no button: the danger must not wait for a press)
            kinds = ["wire" if k in ("resistor", "led", "button") else k for k in kinds]
            kinds[-1 if kinds[-1] != "wire" or len(kinds) < 3 else -2] = "led"
        rows = sorted(rng.sample(free, n - 1))
        netlist = _chain(rng, kinds, half, rows)
    if mistake == "no_resistor":
        for p in netlist:
            if p["kind"] == "led" and p["color"] not in BRIGHT_AT_3V3:
                p["color"] = rng.choice(BRIGHT_AT_3V3)  # far too much current, at any supply
    used = {r for p in netlist for r in p["rows"]}
    leds = [p for p in netlist if p["kind"] == "led"]
    if mistake == "led_backwards" and leds:
        p = rng.choice(leds)
        p["rows"] = p["rows"][::-1]
    elif mistake == "missing_link":
        inner = [p for p in netlist if not p["rows"][1].startswith("P")]
        spare = [f"{half}{r}" for r in free if f"{half}{r}" not in used]
        if inner and spare:
            p = rng.choice(inner)
            p["rows"] = [p["rows"][0], rng.choice(spare)]  # the leg went in the wrong row
        else:
            mistake = None
    elif mistake == "short":
        target = [p for p in netlist if p["kind"] in ("led", "resistor")]
        if target:
            netlist.append({"kind": "wire", "rows": list(rng.choice(target)["rows"])})
        else:
            mistake = None
    elif mistake == "led_backwards":
        mistake = None
    if mistake is None:
        _lights_work(netlist)
        _make_safe(netlist)
    return {"seed": seed, "parts": solver.clean(netlist), "mistake": mistake}


def pressed(netlist):
    """The circuit with every push button held down (the worst case for
    safety, and how a correct circuit is meant to work)."""
    return [dict(p, closed=True) if p["kind"] == "button" else p for p in netlist]


def _lights_work(netlist):
    """A circuit with no mistake lights every LED: two LEDs in a row whose
    voltages add up to the whole supply (blue + yellow) would both stay
    dark, so recolour them red until every LED is on."""
    for _ in range(5):
        on = solver.solve(pressed(netlist))["leds_on"]
        dark = [i for i, lit in on.items() if not lit]
        if not dark:
            return
        for i in dark:
            netlist[i]["color"] = "red"


def _make_safe(netlist):
    """A circuit with no mistake must not trip the safety cut: raise the
    smallest resistor until the current is below SAFE_MA."""
    for _ in range(10):
        if solver.solve(pressed(netlist))["current_ma"] < SAFE_MA:
            return
        rs = [p for p in netlist if p["kind"] == "resistor"]
        if not rs:
            return
        low = min(rs, key=lambda p: p["ohms"])
        bigger = [o for o in RESISTORS if o > low["ohms"]]
        low["ohms"] = bigger[0] if bigger else low["ohms"] * 10
