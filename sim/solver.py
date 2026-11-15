"""A small DC circuit solver for the fake board (plain Python, no numpy).

Free build: the student may build any circuit from any parts, so the fake
board can no longer look its numbers up per lesson. It works them out
instead, from a netlist: a list of parts, each with the rows its legs are in.

    {"kind": "wire",     "rows": ["PT+", "T5"]}
    {"kind": "resistor", "rows": ["T5", "T10"], "ohms": 220}
    {"kind": "led",      "rows": ["T10", "T12"], "color": "red"}   # long leg first
    {"kind": "button",   "rows": ["T5", "T8"], "closed": true}

Every + line (PT+, PB+) is the 5 V supply, every - line (PT-, PB-) is 0 V.
The current sensor sits in the supply's + line, so it measures the current
the circuit takes from the supply.

How it works (nodal analysis):
- Each row with a part in it is a node. Every part between two nodes adds a
  conductance; a lit LED is LED_ON_OHMS in series with its forward voltage.
  Kirchhoff's current law at every row gives a set of linear equations,
  solved by Gaussian elimination.
- LEDs are not linear: each is either off (no current) or on. Start with
  every LED on, solve, then switch off any LED whose current would flow
  backwards, and switch on any off LED with more than its forward voltage
  across it. Repeat until nothing changes (at most MAX_ROUNDS times).
- A row joined to the supply or ground by nothing that conducts (only an
  off LED, an open button, or nothing at all) is "floating": the board
  cannot read anything meaningful there. Those rows are None.
- The supply has a current limit (like a bench supply): if the circuit
  would take more, the supply's voltage drops until it gives exactly the
  limit (found by halving the interval).
"""

SUPPLY_V = 5.0
CURRENT_LIMIT_MA = 100.0
WIRE_OHMS = 0.05
LED_ON_OHMS = 15.0
LED_VF = {"red": 1.9, "yellow": 2.0, "green": 2.1, "blue": 3.0, "white": 3.0}
MAX_ROUNDS = 20
PLUS, MINUS = ("PT+", "PB+"), ("PT-", "PB-")
KINDS = ("wire", "resistor", "led", "button")


class NetlistError(ValueError):
    pass


UNSEEN = tuple(f"U{i}" for i in range(1, 10))  # rows the board does not measure


def _is_place(name):
    """A + or - line, a row T1..T63 / B1..B63, or U1..U9: a row the board
    does not measure (a leg in it touches only what else is in that row)."""
    if name in PLUS or name in MINUS or name in UNSEEN:
        return True
    return (len(name) > 1 and name[0] in "TB" and name[1:].isdigit()
            and 1 <= int(name[1:]) <= 63)


def clean(parts):
    """Check a netlist and return it in one tidy form (or raise NetlistError)."""
    if not isinstance(parts, list):
        raise NetlistError("parts must be a list")
    out = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise NetlistError(f"part {i + 1} is not an object")
        kind = str(p.get("kind", "")).lower()
        if kind not in KINDS:
            raise NetlistError(f"part {i + 1}: kind must be one of {', '.join(KINDS)}")
        rows = p.get("rows")
        if not isinstance(rows, (list, tuple)) or len(rows) != 2:
            raise NetlistError(f"part {i + 1}: give exactly 2 rows")
        rows = [str(r).upper() for r in rows]
        bad = [r for r in rows if not _is_place(r)]
        if bad:
            raise NetlistError(f"part {i + 1}: not a row: {', '.join(bad)}")
        part = {"kind": kind, "rows": rows}
        if kind == "resistor":
            try:
                ohms = float(p.get("ohms"))
            except (TypeError, ValueError):
                raise NetlistError(f"part {i + 1}: a resistor needs ohms") from None
            if not ohms > 0:
                raise NetlistError(f"part {i + 1}: ohms must be more than 0")
            part["ohms"] = ohms
        if kind == "led":
            color = str(p.get("color", "red")).lower()
            if color not in LED_VF:
                raise NetlistError(f"part {i + 1}: LED color must be one of {', '.join(LED_VF)}")
            part["color"] = color
        if kind == "button":  # None: not said (the fake board's HOLD decides)
            part["closed"] = None if p.get("closed") is None else bool(p["closed"])
        out.append(part)
    return out


def _node(row):
    if row in PLUS:
        return "+"
    if row in MINUS:
        return "-"
    return row


def _gauss(a, b):
    """Solve a x = b (a: list of rows). Partial pivoting. Returns x."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-15:
            raise NetlistError("the circuit cannot be solved")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def _edges(parts, led_on):
    """[(a, b, conductance, source_v)] for every part that conducts now.
    source_v: a lit LED's forward voltage (current a -> b is
    g * (Va - Vb - source_v))."""
    out = []
    for i, p in enumerate(parts):
        a, b = (_node(r) for r in p["rows"])
        if a == b:
            continue  # both legs in the same row: nothing flows through it
        k = p["kind"]
        if k == "wire":
            out.append((a, b, 1 / WIRE_OHMS, 0.0, i))
        elif k == "resistor":
            out.append((a, b, 1 / p["ohms"], 0.0, i))
        elif k == "button" and p.get("closed"):
            out.append((a, b, 1 / WIRE_OHMS, 0.0, i))
        elif k == "led" and led_on[i]:
            out.append((a, b, 1 / LED_ON_OHMS, LED_VF[p["color"]], i))
    return out


def _driven(nodes, edges):
    """The nodes joined (through parts that conduct) to the supply or ground."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, *_ in edges:
        parent[find(a)] = find(b)
    roots = {find(n) for n in ("+", "-") if n in parent}
    return {n for n in nodes if find(n) in roots}


def _solve_at(parts, supply_v):
    """One solve with an ideal supply: (current_ma, {row: volts or None}, led_on)."""
    nodes = {"+", "-"} | {_node(r) for p in parts for r in p["rows"]}
    led_on = {i: p["kind"] == "led" for i, p in enumerate(parts)}
    volts = {}
    for _ in range(MAX_ROUNDS):
        edges = _edges(parts, led_on)
        driven = _driven(nodes, edges)
        unknown = sorted(n for n in driven if n not in ("+", "-"))
        index = {n: i for i, n in enumerate(unknown)}
        fixed = {"+": supply_v, "-": 0.0}
        size = len(unknown)
        a = [[0.0] * size for _ in range(size)]
        rhs = [0.0] * size
        for na, nb, g, vs, _i in edges:
            if na not in driven:
                continue
            # Current leaving na towards nb: g * (Va - Vb - vs).
            for here, there, sign in ((na, nb, 1), (nb, na, -1)):
                if here not in index:
                    continue
                r = index[here]
                a[r][r] += g
                if there in index:
                    a[r][index[there]] -= g
                else:
                    rhs[r] += g * fixed[there]
                rhs[r] += sign * g * vs
        x = _gauss(a, rhs) if size else []
        volts = dict(fixed)
        volts.update({n: x[index[n]] for n in unknown})
        changed = False
        for i, p in enumerate(parts):
            if p["kind"] != "led":
                continue
            a_, b_ = (_node(r) for r in p["rows"])
            vf = LED_VF[p["color"]]
            if a_ == b_:
                on = False
            elif led_on[i]:
                # A lit LED stays lit while current flows forwards through it.
                on = a_ in volts and b_ in volts and volts[a_] - volts[b_] - vf > 1e-9
            else:
                # An off LED lights when both sides are driven and it has more
                # than its forward voltage across it.
                on = a_ in volts and b_ in volts and volts[a_] - volts[b_] > vf + 1e-9
            if on != led_on[i]:
                led_on[i], changed = on, True
        if not changed:
            break
    edges = _edges(parts, led_on)
    current = 0.0
    for na, nb, g, vs, _i in edges:
        if na in volts and nb in volts:
            flow = g * (volts[na] - volts[nb] - vs)  # amps, na -> nb
            if na == "+":
                current += flow
            if nb == "+":
                current -= flow
    rows = {}
    for p in parts:
        for r in p["rows"]:
            n = _node(r)
            if n in ("+", "-"):
                continue
            rows[r] = round(volts[n], 4) if n in volts else None
    return current * 1000, rows, led_on


def solve(parts, supply_v=SUPPLY_V, current_limit_ma=CURRENT_LIMIT_MA):
    """Solve a netlist. Returns {"supply_v", "current_ma", "rows" (row ->
    volts, or None when floating), "leds_on" (part index -> bool)}.
    With a current limit, the supply's voltage drops so the current is
    never above it (like a real bench supply)."""
    parts = clean(parts)
    current, rows, leds = _solve_at(parts, supply_v)
    if current_limit_ma is not None and current > current_limit_ma:
        lo, hi = 0.0, supply_v
        for _ in range(40):
            mid = (lo + hi) / 2
            c, _r, _l = _solve_at(parts, mid)
            lo, hi = (mid, hi) if c < current_limit_ma else (lo, mid)
        supply_v = lo
        current, rows, leds = _solve_at(parts, supply_v)
    return {"supply_v": supply_v, "current_ma": max(0.0, current), "rows": rows,
            "leds_on": {i: on for i, on in leds.items() if parts[i]["kind"] == "led"}}
