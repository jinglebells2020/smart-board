"""Fake smart breadboard, speaking serial protocol version 2.

Pretends to be the real board so the agent and web page can be built
and tested without hardware. Protocol: docs/SERIAL_PROTOCOL.md.

The real board is not finished, so many things are still open: how many
rows are wired, whether there is a current sensor, whether the laptop
can switch breadboard power, whether there is a screen. The fake board
can be started with any of those answers:

    from sim.fake_board import FakeBoard
    board = FakeBoard(lesson=1, scenario="led_backwards",
                      rows="T1-T16", has_current=False, has_power_switch=False)
    board.hello()          # what this board has
    board.read()           # one reading (dict)
    board.send("LED T10 red")
    board.events()         # button presses waiting to be read

From a terminal (readings stream; type commands and press Enter):

    python sim/fake_board.py --lesson 1 --scenario correct --rows T1-T16 --no-current

Voltages and currents are worked out with Ohm's law from the circuit,
not copied from lessons/*.json, so the tests can check one against the
other.
"""

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for "python sim/fake_board.py"

import protocol as P  # noqa: E402

# --- Board constants -------------------------------------------------
SUPPLY_V = 5.0  # docs/LESSONS.md uses 5 V for every lesson.
CURRENT_LIMIT_MA = 100.0  # PLACEHOLDER: the supply's current limit.
SAMPLE_MS = 100  # Average time between readings. Not fixed: see read().

# --- Part values (from docs/LESSONS.md) ------------------------------
LED_VF = 2.0  # Red LED forward voltage, volts.
# With no resistor the supply runs at its current limit and its voltage
# drops to about the LED voltage at that current.
LED_VF_AT_LIMIT = 2.2

# --- Noise -----------------------------------------------------------
NOISE_V = 0.01  # volts
NOISE_MA_ABS = 0.005  # mA
NOISE_MA_REL = 0.01  # 1 % of the reading

FLOATING = None  # a row nothing drives (empty, or connected to nothing)


def _circuit(lesson, scenario):
    """Noise-free (supply_v, current_ma, {row: volts or FLOATING}).

    Rows in the dict are the lesson's rows (docs/LESSONS.md). Every other
    row is empty, so it floats. PT+ is the supply and PT- is ground.
    """
    v = SUPPLY_V
    if lesson == 1:
        # Red wire PT+ -> T5; 220 ohm T5-T10; LED long leg T10, short
        # leg T12; black wire T12 -> PT-.
        r = 220.0
        if scenario == "correct":
            return v, (v - LED_VF) / r * 1000, {"T5": v, "T10": LED_VF, "T12": 0.0}  # 13.6 mA
        if scenario == "led_backwards":
            # A backwards LED blocks current. No current, so no drop across
            # the resistor: row 10 sits at 5 V. (Same rows if the long leg is
            # not in row 10: then row 10 touches only the resistor.)
            return v, 0.0, {"T5": v, "T10": v, "T12": 0.0}
        if scenario == "no_resistor":
            # LED straight across the supply: current runs into the supply's
            # limit and the supply voltage drops to about the LED voltage.
            return (LED_VF_AT_LIMIT, CURRENT_LIMIT_MA,
                    {"T5": LED_VF_AT_LIMIT, "T10": LED_VF_AT_LIMIT, "T12": 0.0})
        if scenario == "open_circuit":
            # The resistor is in the wrong row, so row 10 holds only the
            # LED's long leg: nothing drives it. No current.
            return v, 0.0, {"T5": v, "T10": FLOATING, "T12": 0.0}
        if scenario == "resistor_too_big":
            # 1k instead of 220: (5 - 2) / 1000 = 3.0 mA. Row 10 still 2 V.
            return v, (v - LED_VF) / 1000.0 * 1000, {"T5": v, "T10": LED_VF, "T12": 0.0}
        if scenario == "led_bypassed":
            # Current skips the LED: row 10 is joined to ground.
            # 5 / 220 = 22.7 mA.
            return v, v / r * 1000, {"T5": v, "T10": 0.0, "T12": 0.0}
        if scenario == "resistor_150":
            # NOT a listed mistake (see UNLISTED): (5 - 2) / 150 = 20 mA.
            return v, (v - LED_VF) / 150.0 * 1000, {"T5": v, "T10": LED_VF, "T12": 0.0}
    if lesson == 2:
        # Red wire PT+ -> T5; top resistor T5-T10; bottom resistor
        # T10-T15; black wire T15 -> PT-.
        def divider(r_top, r_bot):
            return v, v / (r_top + r_bot) * 1000, {
                "T5": v, "T10": v * r_bot / (r_top + r_bot), "T15": 0.0}
        if scenario == "correct":
            return divider(10_000, 10_000)  # 2.5 V, 0.25 mA
        if scenario == "bottom_1k":
            return divider(10_000, 1_000)  # 5 x 1 / 11 = 0.45 V
        if scenario == "top_1k":
            return divider(1_000, 10_000)  # 5 x 10 / 11 = 4.55 V
        if scenario == "bottom_4k7":
            return divider(10_000, 4_700)  # NOT a listed mistake: 1.60 V
        if scenario == "middle_floating":
            # The resistors meet in another row, so row 10 is empty.
            # The divider still works: 0.25 mA.
            return v, v / 20_000 * 1000, {"T5": v, "T10": FLOATING, "T15": 0.0}
        if scenario == "bottom_missing":
            # Loop broken below the middle: no current, middle keeps 5 V.
            return v, 0.0, {"T5": v, "T10": v, "T15": 0.0}
        if scenario == "top_missing":
            # Middle touches only ground (through the bottom resistor).
            return v, 0.0, {"T5": v, "T10": 0.0, "T15": 0.0}
    raise ValueError(f"unknown lesson/scenario: {lesson} {scenario}")


# Every circuit the fake board can pretend the student built. "correct"
# is the right one; UNLISTED are mistakes no lesson lists (the tutor must
# explain them with a clue); every other name is a mistake id from
# lessons/*.json.
UNLISTED = {
    1: ["resistor_150"],
    2: ["bottom_4k7"],
}
SCENARIOS = {
    1: ["correct", "led_backwards", "no_resistor", "open_circuit",
        "resistor_too_big", "led_bypassed"] + UNLISTED[1],
    2: ["correct", "bottom_1k", "middle_floating", "top_1k",
        "bottom_missing", "top_missing"] + UNLISTED[2],
}


class FakeBoard:
    """A pretend board. read() for readings, send() for commands,
    events() for button presses, hello() for what it has."""

    def __init__(self, lesson=1, scenario="correct", seed=None, noise=True,
                 rows=None, has_current=True, has_power_switch=True,
                 has_screen=True, buttons=4):
        self._rng = random.Random(seed)
        self._noise = noise
        self._t_ms = 0
        if rows is None:
            rows = list(P.ALL_NODES)
        elif isinstance(rows, str):
            rows = P.parse_rows(rows)
        else:
            bad = [r for r in rows if not P.is_node(r)]
            if bad:
                raise ValueError(f"not row names: {bad}")
            rows = P.parse_rows(",".join(rows))
        self.rows = rows
        self.has_current = bool(has_current)
        self.has_power_switch = bool(has_power_switch)
        self.has_screen = bool(has_screen)
        if not 0 <= buttons <= 4:
            raise ValueError("buttons must be 0 to 4")
        self.buttons = buttons
        self.power_on = True
        self.lights = {}   # row -> color
        self.screen = []   # lines on the screen
        self._events = []
        self.set_scenario(lesson, scenario)

    def set_scenario(self, lesson, scenario):
        """Change which circuit the pretend student has built."""
        if lesson not in SCENARIOS or scenario not in SCENARIOS[lesson]:
            raise ValueError(f"unknown lesson/scenario: {lesson} {scenario}")
        self.lesson = lesson
        self.scenario = scenario

    def hello(self):
        return {"type": "hello", "protocol": P.PROTOCOL_VERSION, "rows": list(self.rows),
                "has_current": self.has_current, "has_power_switch": self.has_power_switch,
                "has_screen": self.has_screen, "buttons": self.buttons}

    def read(self):
        """One reading (docs/SERIAL_PROTOCOL.md). The time between
        readings varies, like a real board: use t_ms."""
        step = SAMPLE_MS + (self._rng.randint(-30, 30) if self._noise else 0)
        self._t_ms += step
        supply, current, circuit = _circuit(self.lesson, self.scenario)
        nodes = dict(circuit, **{"PT+": supply, "PT-": 0.0})

        def jitter(value, size=NOISE_V):
            return value + (self._rng.gauss(0, size) if self._noise else 0.0)

        rows, floating = {}, []
        for name in self.rows:
            if name not in nodes or nodes[name] is FLOATING:
                # Nothing drives this row: the number means nothing.
                rows[name] = round(self._rng.uniform(0.0, SUPPLY_V) if self._noise else 0.0, 3)
                floating.append(name)
            elif self.power_on:
                rows[name] = round(jitter(nodes[name]), 3)
            else:
                rows[name] = round(jitter(0.0), 3)
        if not self.power_on:
            supply, current = 0.0, 0.0
        else:
            supply = jitter(supply)
            current = jitter(current, NOISE_MA_ABS + NOISE_MA_REL * current)
        return {
            "type": "reading", "t_ms": self._t_ms, "power_on": self.power_on,
            "supply_v": round(supply, 3),
            "current_ma": round(current, 3) if self.has_current else None,
            "rows": rows, "floating": floating,
        }

    def events(self):
        """Button presses waiting to be read (and forget them)."""
        out, self._events = self._events, []
        return out

    def send(self, line):
        """Handle one text command. Returns the board's answer (a dict)."""
        text = line.strip()
        parts = text.split()
        if not parts:
            return {"type": "error", "msg": "empty command"}
        cmd = parts[0].upper()

        def ack(c=None):
            return {"type": "ack", "cmd": c or text}

        def error(msg):
            return {"type": "error", "cmd": cmd, "msg": msg}

        if cmd in ("CUT", "RESTORE") and len(parts) == 1:
            if not self.has_power_switch:
                return error("no power switch")
            self.power_on = cmd == "RESTORE"
            return ack(cmd)
        if cmd == "LED" and len(parts) == 2 and parts[1].upper() == "CLEAR":
            self.lights.clear()
            return ack("LED CLEAR")
        if cmd == "LED" and len(parts) == 3:
            row, color = parts[1].upper(), parts[2].lower()
            if not P.is_row(row):
                return error(f"not a row with a light: {parts[1]}")
            if color not in P.COLORS:
                return error(f"unknown color: {parts[2]}")
            if color == "off":
                self.lights.pop(row, None)
            else:
                self.lights[row] = color
            return ack(f"LED {row} {color}")
        if cmd == "SHOW":
            if not self.has_screen:
                return error("no screen")
            lines = text[4:].lstrip().split("|") if len(text) > 4 else [""]
            if len(lines) > P.SCREEN_LINES or any(len(l) > P.SCREEN_CHARS for l in lines):
                return error("too long")
            self.screen = lines
            return ack("SHOW")
        if cmd == "HELLO" and len(parts) == 1:
            return self.hello()
        if cmd == "PRESS" and len(parts) == 2:  # simulator only
            if not parts[1].isdigit() or not 1 <= int(parts[1]) <= self.buttons:
                return error(f"no button {parts[1]}")
            self._events.append({"type": "button", "id": int(parts[1])})
            return ack(f"PRESS {int(parts[1])}")
        if cmd == "SCENARIO" and len(parts) == 3:  # simulator only
            try:
                self.set_scenario(int(parts[1]), parts[2])
            except ValueError as e:
                return error(str(e))
            return ack(f"SCENARIO {parts[1]} {parts[2]}")
        return error(f"unknown command: {text}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Fake smart breadboard (protocol version 2).")
    p.add_argument("--lesson", type=int, default=1)
    p.add_argument("--scenario", default="correct")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--rows", default="all",
                   help='rows it measures, e.g. "T1-T16" or "16" or "all"')
    p.add_argument("--no-current", action="store_true", help="no current sensor")
    p.add_argument("--no-power-switch", action="store_true", help="cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="no screen")
    p.add_argument("--buttons", type=int, default=4)
    p.add_argument("--count", type=int, default=0,
                   help="stop after this many readings (0 = run forever)")
    p.add_argument("--interval", type=float, default=SAMPLE_MS / 1000,
                   help="seconds between readings")
    p.add_argument("--list", action="store_true", help="list lessons and circuits, then exit")
    args = p.parse_args(argv)

    if args.list:
        for lesson, names in SCENARIOS.items():
            print(f"lesson {lesson}: {', '.join(names)}")
        return 0
    try:
        board = FakeBoard(args.lesson, args.scenario, seed=args.seed, rows=args.rows,
                          has_current=not args.no_current,
                          has_power_switch=not args.no_power_switch,
                          has_screen=not args.no_screen, buttons=args.buttons)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    lock = threading.Lock()

    def emit(msg):
        with lock:
            print(json.dumps(msg), flush=True)

    def read_commands():
        for line in sys.stdin:
            if line.strip():
                emit(board.send(line))
                for ev in board.events():
                    emit(ev)

    emit(board.hello())
    threading.Thread(target=read_commands, daemon=True).start()
    n = 0
    try:
        while args.count == 0 or n < args.count:
            emit(board.read())
            n += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
