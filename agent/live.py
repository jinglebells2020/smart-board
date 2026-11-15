"""Instant help: notice the moment the board changes (plain Python, no AI).

ChangeDetector watches the readings the server already gets (the page asks
for the state about 4 times a second). For each reading it looks at which
rows have something connected, their voltages and the current. A change
counts only after the new state has stayed the same for STEADY seconds, so
a hand moving a part does not cause a burst of messages. A row that keeps
switching between connected and empty is reported once, as "flicker".
While the power is off (or right after a safety cut) nothing is reported;
when it comes back the board's state is taken as the new starting point.

classify() then decides, from one normal check and its physics facts:
- "building": some rows of the plan are still empty and nothing is wrong
  yet: stay quiet (the page may show "2 of 4 parts in").
- "mistake": something is wrong right now, even mid-build (a part in a
  row not in the plan, two plan rows joined while current flows, a
  shorted LED, a loose leg, far too much current, or a known mistake once
  every plan row has something in it).
- "complete": every plan row has something in it and nothing above is
  wrong: a normal check decides (praise and pass, or a hint).
"""

import time

from protocol import is_row

STEADY = 1.5          # seconds a new state must last before it counts
SAME_V = 0.3          # volts: closer than this is "the same voltage"
SAME_MA = 1.0         # mA (or 10 %): closer than this is "the same current"
FLICKER_WINDOW = 3.0  # seconds
FLICKER_SWITCHES = 3  # this many connect/empty switches in the window: a loose leg
FAR_ABOVE = 50        # % above the expected current: "far above"
CURRENT_FLOWS_MA = 1.0


def snapshot(reading):
    """What matters about one reading: connected rows, their volts, the current."""
    floating = set(reading.get("floating", []))
    driven = {r: v for r, v in reading.get("rows", {}).items() if r not in floating}
    return {"driven": driven, "current": reading.get("current_ma")}


def same(a, b):
    """Are two snapshots the same state (noise aside)?"""
    if a is None or b is None or set(a["driven"]) != set(b["driven"]):
        return False
    if any(abs(a["driven"][r] - b["driven"][r]) > SAME_V for r in a["driven"]):
        return False
    ca, cb = a["current"], b["current"]
    if (ca is None) != (cb is None):
        return False
    return ca is None or abs(ca - cb) <= max(SAME_MA, 0.1 * max(abs(ca), abs(cb)))


class ChangeDetector:
    """feed(reading) -> an event dict, or None.
    Events: {"kind": "change", "added": [...], "removed": [...]} and
    {"kind": "flicker", "rows": [...]}."""

    def __init__(self, steady=STEADY, clock=time.monotonic):
        self.steady = steady
        self.clock = clock
        self.reset()

    def reset(self):
        self.stable = None     # the last state that counted (or the starting point)
        self.pending = None    # a new state, waiting to stay the same for `steady`
        self.since = None
        self.last = None       # the previous reading's snapshot (for flicker)
        self.switches = {}     # row -> times it switched between connected and empty
        self.flickering = set()

    def feed(self, reading):
        now = self.clock()
        if not reading.get("power_on"):
            self.reset()  # power off (or just cut): nothing to report
            return None
        snap = snapshot(reading)
        flicker = self._flicker(snap, now)
        if flicker:
            self.pending = None
            return {"kind": "flicker", "rows": flicker}
        if any(self.switches.get(r) for r in self.flickering):
            self.pending = None  # still wobbling: wait until it settles
            return None
        if self.stable is None:  # a starting point, reported as nothing
            if same(snap, self.pending) and now - self.since >= self.steady:
                self.stable, self.pending = snap, None
            elif not same(snap, self.pending):
                self.pending, self.since = snap, now
            return None
        if same(snap, self.stable):
            self.pending = None
            return None
        if not same(snap, self.pending):
            self.pending, self.since = snap, now
            return None
        if now - self.since < self.steady:
            return None
        old = set(self.stable["driven"])
        self.stable, self.pending = snap, None
        new = set(snap["driven"])
        return {"kind": "change", "added": sorted(new - old), "removed": sorted(old - new)}

    def _flicker(self, snap, now):
        """Rows that just started flickering (reported once per wobble)."""
        if self.last is not None:
            for row in set(snap["driven"]) ^ set(self.last["driven"]):
                self.switches.setdefault(row, []).append(now)
        self.last = snap
        for row, times in list(self.switches.items()):
            times[:] = [t for t in times if now - t <= FLICKER_WINDOW]
            if not times:
                del self.switches[row]
                self.flickering.discard(row)  # settled: may be reported again later
        new = sorted(r for r, t in self.switches.items()
                     if len(t) >= FLICKER_SWITCHES and r not in self.flickering)
        self.flickering.update(new)
        return new


WIRE_DROP_V = 0.5  # a wire never has more than this across it


def _volts(row, summary):
    if row in ("PT+", "PB+"):
        return summary.get("supply_v") or 5.0
    if row in ("PT-", "PB-"):
        return 0.0
    if row in summary.get("floating", []) or row not in summary.get("rows", {}):
        return None
    return summary["rows"][row]


def parts_in(lesson, summary):
    """(parts that are in, all parts). A part is in when every row it uses
    has something connected, and nothing shows it cannot be there: a wire
    never has more than WIRE_DROP_V across it (with the LED joined across by
    mistake, row 12 reads 5 V, but that does not mean the black wire to the
    - line is in)."""
    layout = lesson.get("layout", [])
    done = 0
    for part in layout:
        volts = [_volts(leg["row"], summary) for leg in part["legs"]]
        if any(v is None for v in volts):
            continue
        if "wire" in part["part"].lower() and len(volts) == 2 and \
                abs(volts[0] - volts[1]) > WIRE_DROP_V:
            continue  # impossible for a wire: it is not in yet
        done += 1
    return done, len(layout)


def wrong_side(lesson, summary):
    """Plan rows that read the opposite end of the supply from the plan (a
    row that should be near 0 V at 5 V, or the other way round)."""
    supply = summary.get("supply_v") or 5.0
    out = []
    for row, e in lesson.get("expected", {}).get("rows", {}).items():
        m = _volts(row, summary)
        if m is None or row not in lesson.get("rows", []):
            continue
        if (e <= WIRE_DROP_V and m >= supply - WIRE_DROP_V) or \
                (e >= supply - WIRE_DROP_V and m <= WIRE_DROP_V):
            out.append(row)
    return out


MISTAKE_NOW = ("extra_rows", "flicker")


def classify(result, summary, facts, lesson):
    """What instant help should do with one check (see the module docstring).
    Returns {"kind": "building" | "mistake" | "complete", "rows", "text",
    "fact" (the kind of fact that showed the mistake), "parts_in", "parts",
    "empty" (plan rows with nothing connected), "any_driven" (is anything
    plugged in at all)}."""
    plan = list(lesson.get("rows", []))
    floating = set(summary.get("floating", []))
    empty = [r for r in plan if r in floating or r not in summary.get("rows", {})]
    current = summary.get("current_ma")
    flows = current is not None and current > CURRENT_FLOWS_MA
    done, total = parts_in(lesson, summary)
    extra = any(f["kind"] == "extra_rows" for f in facts)
    out = {"parts_in": done, "parts": total, "rows": [], "text": "", "fact": None,
           "empty": empty, "any_driven": len(empty) < len(plan) or extra}

    now = []
    for f in facts:
        k, v = f["kind"], f.get("values", {})
        if k in MISTAKE_NOW:
            now.append(f)
        elif k == "same_voltage" and flows:
            now.append(f)  # joined rows; with no current, the same voltage is normal mid-build
        elif k == "led_state" and v.get("state") == "shorted":
            now.append(f)
        elif k == "current_vs_expected" and (v.get("percent") or 0) >= FAR_ABOVE:
            now.append(f)
    # Every plan row is in, but nothing flows and a row reads the wrong end of
    # the supply: a part is still missing (the build is not complete).
    unfinished = bool(empty) or (not flows and bool(wrong_side(lesson, summary)))
    if not now and not unfinished and result.get("status") == "mistake":
        rule_rows = set(result.get("rows") or [])
        now = [f for f in facts if rule_rows & set(f["rows"]) and f["kind"] in
               ("led_state", "same_voltage", "plan_row_floating", "part_drop", "row_voltage")]
        now = sorted(now, key=lambda f: ["led_state", "same_voltage", "plan_row_floating",
                                         "part_drop", "row_voltage"].index(f["kind"]))[:1] or \
            [{"kind": "rule", "rows": sorted(rule_rows),
              "text": "Something is not right with the circuit."}]
    if now:
        rows = sorted({r for f in now for r in f["rows"] if is_row(r)}, key=_row_order)
        return dict(out, kind="mistake", rows=rows, text=now[0]["text"], fact=now[0].get("kind"))
    if unfinished:
        return dict(out, kind="building")
    return dict(out, kind="complete")


def _row_order(row):
    return (row[0] != "T", int(row[1:]))
