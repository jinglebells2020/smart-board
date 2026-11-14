"""Fake smart breadboard.

Pretends to be the real board so the agent and web page can be built
and tested without hardware. It speaks the protocol in
docs/SERIAL_PROTOCOL.md: JSON readings out, text commands in.

Two ways to use it:

1. From Python (this is what the agent and tests use):

       from sim.fake_board import FakeBoard
       board = FakeBoard(lesson=1, scenario="led_backwards")
       board.read()          # -> dict, one reading
       board.send("CUT")     # -> dict, the board's answer

2. From a terminal, like a serial port (readings print to the
   screen, type commands and press Enter):

       python sim/fake_board.py --lesson 1 --scenario correct

The voltages and currents are worked out from the circuit using
Ohm's law, not copied from lessons/*.json. That way the tests can
check the simulator against the lesson files and catch mistakes in
either one.
"""

import argparse
import json
import random
import sys
import threading
import time

# --- Board constants -------------------------------------------------
# docs/HARDWARE.md is not filled in yet. These are placeholders.
# Update them when the real values are known.
SUPPLY_V = 5.0  # docs/LESSONS.md uses 5 V for every lesson.
CURRENT_LIMIT_MA = 100.0  # PLACEHOLDER. Real limit: docs/HARDWARE.md.
SAMPLE_MS = 100  # One reading every 100 ms (10 per second).

# --- Part values (from docs/LESSONS.md) ------------------------------
LED_VF = 2.0  # Red LED forward voltage, volts.
# When there is no resistor, the supply is held at its current limit
# and its voltage drops to about the LED voltage at that current.
LED_VF_AT_LIMIT = 2.2

# --- Noise -----------------------------------------------------------
# Real sensors are never perfectly steady. Small random noise makes
# the agent deal with realistic numbers.
NOISE_V = 0.01  # volts
NOISE_MA_ABS = 0.005  # mA
NOISE_MA_REL = 0.01  # 1 % of the reading


def _ideal(lesson, scenario):
    """Return the noise-free (supply_v, current_ma, probe1_v) for a circuit.

    probe1_v may be None, meaning the probe is not touching anything
    (it floats). The caller then picks a random voltage.
    """
    v = SUPPLY_V
    if lesson == 1:
        # 5 V -> 220 ohm -> LED -> ground. probe1 = LED anode, the row
        # between the resistor and the LED.
        r = 220.0
        if scenario == "correct":
            i_ma = (v - LED_VF) / r * 1000  # 13.6 mA
            return v, i_ma, LED_VF
        if scenario == "led_backwards":
            # A backwards LED blocks current. No current means no
            # voltage drop across the resistor, so the anode row is 5 V.
            return v, 0.0, v
        if scenario == "no_resistor":
            # LED straight across the supply. Current shoots up until
            # the supply's limit stops it.
            return LED_VF_AT_LIMIT, CURRENT_LIMIT_MA, LED_VF_AT_LIMIT
        if scenario == "open_circuit":
            # A part is in the wrong row, so nothing connects the LED
            # to 5 V. No current, and the LED node sits at ground.
            return v, 0.0, 0.0
        if scenario == "resistor_too_big":
            # 1k instead of 220 ohm. The LED still drops its 2 V, so the
            # resistor gets the other 3 V: (5 - 2) / 1000 = 3.0 mA. Dim LED.
            # (A 10k would give (5 - 2) / 10000 = 0.3 mA, same row voltage.)
            r_big = 1_000.0
            return v, (v - LED_VF) / r_big * 1000, LED_VF
        if scenario == "led_bypassed":
            # Both LED legs in one row (or a wire around the LED): the
            # resistor goes straight to ground. 5 / 220 = 22.7 mA, and the
            # LED row is tied to ground, so it reads 0 V. The LED is dark.
            return v, v / r * 1000, 0.0
        if scenario == "resistor_150":
            # NOT a listed mistake (see UNLISTED): 150 ohm instead of 220.
            # (5 - 2) / 150 = 20 mA, LED row 2 V. Shows the clue.
            return v, (v - LED_VF) / 150.0 * 1000, LED_VF
    if lesson == 2:
        # 5 V -> R_top -> middle node -> R_bottom -> ground.
        # probe1 = middle node.
        r_top = 10_000.0
        if scenario == "correct":
            r_bot = 10_000.0
            return v, v / (r_top + r_bot) * 1000, v * r_bot / (r_top + r_bot)
        if scenario == "bottom_1k":
            r_bot = 1_000.0
            return v, v / (r_top + r_bot) * 1000, v * r_bot / (r_top + r_bot)
        if scenario == "middle_floating":
            # The divider itself is fine, so current is normal. But the
            # probe row is not connected to the middle node, so probe1
            # reads random junk.
            r_bot = 10_000.0
            return v, v / (r_top + r_bot) * 1000, None
        if scenario == "top_1k":
            # 1k on top, 10k on the bottom: 5 / 11000 = 0.45 mA, and the
            # middle gets 5 x 10 / 11 = 4.55 V.
            r_top_small, r_bot = 1_000.0, 10_000.0
            return (v, v / (r_top_small + r_bot) * 1000,
                    v * r_bot / (r_top_small + r_bot))
        if scenario == "bottom_missing":
            # No bottom resistor: the loop is broken below the middle.
            # No current, so the top resistor drops nothing: middle = 5 V.
            return v, 0.0, v
        if scenario == "top_missing":
            # No top resistor: the middle only touches ground through
            # the bottom resistor. No current, middle = 0 V.
            return v, 0.0, 0.0
        if scenario == "bottom_4k7":
            # NOT a listed mistake (see UNLISTED): 4.7k on the bottom.
            # 5 / 14700 = 0.34 mA, middle = 5 x 4.7 / 14.7 = 1.60 V.
            r_bot = 4_700.0
            return v, v / (r_top + r_bot) * 1000, v * r_bot / (r_top + r_bot)
    raise ValueError(f"unknown lesson/scenario: {lesson} {scenario}")


# Every circuit the simulator can pretend the student built.
# "correct" is the right circuit. The names in UNLISTED are mistakes the
# lessons do NOT list, so the tutor must explain them with a clue from
# the numbers. Every other name is a mistake from docs/LESSONS.md and
# matches a mistake "id" in lessons/*.json.
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
    """A pretend board. Call read() for readings and send() for commands."""

    def __init__(self, lesson=1, scenario="correct", seed=None, noise=True):
        self._rng = random.Random(seed)
        self._noise = noise
        self._samples = 0
        self.power_on = True
        self.led_row = None
        self.set_scenario(lesson, scenario)

    def set_scenario(self, lesson, scenario):
        """Change which circuit the pretend student has built."""
        if lesson not in SCENARIOS or scenario not in SCENARIOS[lesson]:
            raise ValueError(f"unknown lesson/scenario: {lesson} {scenario}")
        self.lesson = lesson
        self.scenario = scenario

    def read(self):
        """Return one reading as a dict (see docs/SERIAL_PROTOCOL.md)."""
        t_ms = self._samples * SAMPLE_MS
        self._samples += 1

        if not self.power_on:
            supply_v, current_ma, probe1_v = 0.0, 0.0, 0.0
        else:
            supply_v, current_ma, probe1_v = _ideal(self.lesson, self.scenario)
            if probe1_v is None:
                probe1_v = self._rng.uniform(0.0, SUPPLY_V)  # floating
            if self._noise:
                supply_v += self._rng.gauss(0, NOISE_V)
                probe1_v += self._rng.gauss(0, NOISE_V)
                current_ma += self._rng.gauss(
                    0, NOISE_MA_ABS + NOISE_MA_REL * current_ma
                )

        return {
            "type": "reading",
            "t_ms": t_ms,
            "power_on": self.power_on,
            "supply_v": round(supply_v, 3),
            "current_ma": round(current_ma, 3),
            "voltages": {"probe1": round(probe1_v, 3)},
            "led_row": self.led_row,
        }

    def send(self, line):
        """Handle one text command. Returns the board's answer as a dict."""
        parts = line.strip().split()
        if not parts:
            return {"type": "error", "msg": "empty command"}
        cmd = parts[0].upper()

        if cmd == "CUT" and len(parts) == 1:
            self.power_on = False
            return {"type": "ack", "cmd": "CUT"}
        if cmd == "RESTORE" and len(parts) == 1:
            self.power_on = True
            return {"type": "ack", "cmd": "RESTORE"}
        if cmd == "LED" and len(parts) == 2:
            try:
                row = int(parts[1])
            except ValueError:
                return {"type": "error", "msg": f"bad row: {parts[1]}"}
            if row < 1:
                return {"type": "error", "msg": f"bad row: {parts[1]}"}
            self.led_row = row
            return {"type": "ack", "cmd": f"LED {row}"}
        if cmd == "SCENARIO" and len(parts) == 3:  # simulator only
            try:
                self.set_scenario(int(parts[1]), parts[2])
            except ValueError as e:
                return {"type": "error", "msg": str(e)}
            return {"type": "ack", "cmd": f"SCENARIO {parts[1]} {parts[2]}"}
        return {"type": "error", "msg": f"unknown command: {line.strip()}"}


def main(argv=None):
    p = argparse.ArgumentParser(description="Fake smart breadboard.")
    p.add_argument("--lesson", type=int, default=1)
    p.add_argument("--scenario", default="correct")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--count", type=int, default=0,
                   help="stop after this many readings (0 = run forever)")
    p.add_argument("--interval", type=float, default=SAMPLE_MS / 1000,
                   help="seconds between readings")
    p.add_argument("--list", action="store_true",
                   help="list lessons and scenarios, then exit")
    args = p.parse_args(argv)

    if args.list:
        for lesson, names in SCENARIOS.items():
            print(f"lesson {lesson}: {', '.join(names)}")
        return 0

    try:
        board = FakeBoard(args.lesson, args.scenario, seed=args.seed)
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
