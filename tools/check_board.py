"""Check a board's firmware against the serial protocol (version 2).

This is how the hardware teammate tests the firmware. It connects to the
board, reads its hello line and 20 readings, sends every command, and
prints PASS or FAIL for each rule in plain words. The rules come from
protocol.py and docs/SERIAL_PROTOCOL.md.

    python tools/check_board.py --serial COM3
    python tools/check_board.py --serial auto
    python tools/check_board.py --serial COM3 --buttons      # also press each button
    python tools/check_board.py --serial COM3 --power-cut    # also test the safety cut
    python tools/check_board.py --fake                       # try it on the fake board

Close the Arduino Serial Monitor first: only one program can use the port.
It ends with exit code 0 when nothing failed, 1 when something failed,
and 2 when it could not connect at all.
"""

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocol as P  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Lines:
    """Raw lines from a serial port object (pyserial or sim.fake_port)."""

    def __init__(self, port):
        self.port = port
        self.buf = b""
        self.junk = []          # lines that were not protocol JSON

    def line(self, wait):
        """The next line as (text, message or None), or None after `wait` s."""
        deadline = time.monotonic() + wait
        while True:
            if b"\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    msg = None
                if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                    self.junk.append(text)
                    msg = None
                return text, msg
            waiting = self.port.in_waiting
            if waiting:
                self.buf += self.port.read(waiting)
                continue
            if time.monotonic() > deadline:
                return None
            time.sleep(0.005)

    def write(self, text):
        self.port.write((text + "\n").encode("ascii"))


class Checker:
    def __init__(self, port, out=print, count=20, answer_wait=1.0, hello_wait=4.0,
                 event_wait=30.0):
        self.io = Lines(port)
        self.out = out
        self.count = count
        self.answer_wait = answer_wait
        self.hello_wait = hello_wait
        self.event_wait = event_wait  # time for a person to press a button
        self.results = []
        self.hello = None
        self.readings = deque(maxlen=500)  # the latest readings seen, in order
        self.n_readings = 0                # how many readings came in all
        self.events = []        # button and power_cut lines seen
        self.stray = []         # answers that came when no command was sent

    # --- Reporting -------------------------------------------------------
    def rule(self, ok, text, detail=""):
        status = PASS if ok else FAIL
        if ok is None:
            status = SKIP
        self.results.append((status, text, detail))
        show = detail and status != PASS  # details only help when something is wrong
        self.out(f"{status}  {text}" + (f"\n        {detail}" if show else ""))
        return ok

    def section(self, title):
        self.out(f"\n--- {title} ---")

    # --- Lines ------------------------------------------------------------
    def _sort(self, msg):
        kind = msg["type"]
        if kind == "reading":
            self.readings.append(msg)
            self.n_readings += 1
        elif kind in ("button", "power_cut"):
            self.events.append(msg)
        return kind

    def wait_for(self, kinds, wait):
        """The next line of one of these types; others are sorted away."""
        deadline = time.monotonic() + wait
        while True:
            got = self.io.line(max(0.0, deadline - time.monotonic()))
            if got is None:
                return None
            text, msg = got
            if msg is None:
                if time.monotonic() > deadline:
                    return None
                continue
            kind = self._sort(msg)
            if kind in kinds:
                return msg
            if kind in ("ack", "error", "hello"):
                self.stray.append(text)
            if time.monotonic() > deadline:  # readings keep coming: still give up
                return None

    def drain(self, seconds=0.0, most=500):
        """Read what is waiting (and for `seconds` more), at most `most`
        lines, because a board streams readings without end."""
        deadline = time.monotonic() + seconds
        for _ in range(most):
            got = self.io.line(max(0.0, deadline - time.monotonic()))
            if got is None:
                return
            text, msg = got
            if msg is not None and self._sort(msg) in ("ack", "error", "hello"):
                self.stray.append(text)

    def command(self, text):
        """Send one command; its answer line (ack, error or hello), or None."""
        self.drain()
        self.io.write(text)
        return self.wait_for(("ack", "error", "hello"), self.answer_wait)

    def fresh_readings(self, n, wait=5.0):
        start = self.n_readings
        deadline = time.monotonic() + wait
        while self.n_readings - start < n and time.monotonic() < deadline:
            self.wait_for(("reading",), max(0.0, deadline - time.monotonic()))
        new = min(self.n_readings - start, len(self.readings))
        return list(self.readings)[len(self.readings) - new:]

    # --- 1. Hello ---------------------------------------------------------
    def check_hello(self):
        self.section("Hello line")
        hello = self.wait_for(("hello",), self.hello_wait)
        if hello is not None:
            self.rule(True, "The board sent a hello line when the laptop connected.")
        else:
            self.rule(None, "No hello line when the laptop connected.",
                      "That is fine if the board was already running. Asking with HELLO now.")
            self.drain()
            self.io.write("HELLO")
            hello = self.wait_for(("hello",), self.answer_wait * 2)
        if not self.rule(hello is not None, "The board answers the HELLO command with a hello line.",
                         "" if hello is not None else
                         'Send {"type": "hello", "protocol": 2, ...} when you get the line HELLO. '
                         "Is the baud rate right? See docs/FIRMWARE_GUIDE.md."):
            return False
        self.hello = hello
        self.rule(hello.get("protocol") == P.PROTOCOL_VERSION,
                  f'"protocol" is {P.PROTOCOL_VERSION}.', f"it is {hello.get('protocol')!r}")
        rows = hello.get("rows")
        ok = isinstance(rows, list) and len(rows) > 0
        self.rule(ok, '"rows" is a list with at least one name.', f"it is {rows!r}"[:120])
        if not ok:
            return False
        bad = [r for r in rows if not (isinstance(r, str) and P.is_node(r))]
        self.rule(not bad, "Every name in \"rows\" is T1..T63, B1..B63, PT+, PT-, PB+ or PB-.",
                  f"not names: {bad[:8]}" if bad else "")
        dupes = sorted({r for r in rows if rows.count(r) > 1}, key=str)
        self.rule(not dupes, "No name is in \"rows\" twice.", f"twice: {dupes[:8]}" if dupes else "")
        for key in ("has_current", "has_power_switch", "has_screen"):
            self.rule(isinstance(hello.get(key), bool), f'"{key}" is true or false.',
                      f"it is {hello.get(key)!r}")
        b = hello.get("buttons")
        self.rule(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 4,
                  '"buttons" is a whole number from 0 to 4.', f"it is {b!r}")
        self.out(f"        This board: {len(rows)} names measured, current sensor "
                 f"{'yes' if hello.get('has_current') else 'no'}, power switch "
                 f"{'yes' if hello.get('has_power_switch') else 'no'}, screen "
                 f"{'yes' if hello.get('has_screen') else 'no'}, {b} buttons.")
        return True

    # --- 2. Readings --------------------------------------------------------
    def check_readings(self):
        self.section(f"{self.count} readings")
        h = self.hello
        junk_before = len(self.io.junk)
        start = time.monotonic()
        got = self.fresh_readings(self.count, wait=max(10.0, self.count * 0.5))
        seconds = time.monotonic() - start
        if not self.rule(len(got) == self.count,
                         f"The board sends readings on its own ({self.count} arrived).",
                         "" if len(got) == self.count else
                         f"only {len(got)} arrived in {seconds:.0f} s"):
            if not got:
                return
        junk = self.io.junk[junk_before:]
        self.rule(not junk, "Every line is one JSON object with a \"type\" (no junk lines).",
                  f"{len(junk)} junk line(s), first: {junk[0][:80]!r}" if junk else "")

        def all_ok(test):
            for i, r in enumerate(got, 1):
                try:
                    if not test(r):
                        return False, f"reading {i}: {json.dumps(r)[:150]}"
                except Exception:
                    return False, f"reading {i}: {json.dumps(r)[:150]}"
            return True, ""

        def number(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool)

        def ms(x):
            return isinstance(x, int) and not isinstance(x, bool)

        ok, why = all_ok(lambda r: ms(r["t_ms"]) and r["t_ms"] >= 0)
        self.rule(ok, '"t_ms" is a whole number of milliseconds.', why)
        times = [r.get("t_ms") for r in got if ms(r.get("t_ms"))]
        rising = all(b > a for a, b in zip(times, times[1:]))
        self.rule(rising, '"t_ms" goes up with every reading.',
                  "" if rising else f"times: {times[:10]}")
        ok, why = all_ok(lambda r: isinstance(r["power_on"], bool))
        self.rule(ok, '"power_on" is true or false.', why)
        if not h.get("has_power_switch"):
            ok, why = all_ok(lambda r: r["power_on"] is True)
            self.rule(ok, 'With no power switch, "power_on" is always true.', why)
        ok, why = all_ok(lambda r: number(r["supply_v"]))
        self.rule(ok, '"supply_v" is a number (volts).', why)
        if h.get("has_current"):
            ok, why = all_ok(lambda r: number(r["current_ma"]))
            self.rule(ok, '"current_ma" is a number (the board has a current sensor).', why)
        else:
            ok, why = all_ok(lambda r: "current_ma" in r and r["current_ma"] is None)
            self.rule(ok, '"current_ma" is null (the board has no current sensor).', why)
        names = set(h["rows"])
        ok, why = all_ok(lambda r: isinstance(r["rows"], dict) and set(r["rows"]) == names)
        self.rule(ok, '"rows" has exactly the names from the hello line, no more, no less.',
                  why and why + self._row_diff(got, names))
        ok, why = all_ok(lambda r: all(number(v) for v in r["rows"].values()))
        self.rule(ok, 'Every value in "rows" is a number (volts).', why)
        ok, why = all_ok(lambda r: isinstance(r["floating"], list)
                         and set(r["floating"]) <= names)
        self.rule(ok, '"floating" is a list of names from the hello line.', why)

        if len(times) >= 2 and times[-1] > times[0]:
            per_s = (len(times) - 1) * 1000.0 / (times[-1] - times[0])
            longest = max(len(json.dumps(r, separators=(",", ":"))) + 1 for r in got)
            self.out(f"        About {per_s:.1f} readings per second (from t_ms). "
                     f"Longest reading is about {longest} characters.")
            bytes_s = P.DEFAULT_BAUD / 10
            self.out(f"        At {P.DEFAULT_BAUD} baud at most about {bytes_s / longest:.0f} "
                     "readings of that size fit in one second.")

    @staticmethod
    def _row_diff(got, names):
        for r in got:
            have = set(r.get("rows") or {})
            if have != names:
                missing = sorted(names - have)[:6]
                extra = sorted(have - names)[:6]
                return f"\n        missing: {missing}  extra: {extra}"
        return ""

    # --- 3. Commands --------------------------------------------------------
    def expect(self, command, want, text, msg_has=None):
        answer = self.command(command)
        if answer is None:
            return self.rule(False, text, f"no answer to {command!r} within {self.answer_wait:g} s")
        kind = answer.get("type")
        ok = kind == want
        if ok and msg_has is not None:
            ok = msg_has in str(answer.get("msg", ""))
        if ok and want in ("ack", "error"):
            ok = isinstance(answer.get("cmd"), str)
            if not ok:
                return self.rule(False, text, f'the answer has no "cmd": {json.dumps(answer)}')
        return self.rule(ok, text, "" if ok else f"{command!r} answered {json.dumps(answer)[:150]}")

    def check_commands(self):
        self.section("Commands (every command gets exactly one answer line)")
        h = self.hello
        lit = [r for r in h["rows"] if P.is_row(r)]
        row = lit[0] if lit else "T1"
        for color, meaning in P.COLORS.items():
            self.expect(f"LED {row} {color}", "ack", f"LED {row} {color} is answered with ack ({meaning}).")
        self.expect("LED CLEAR", "ack", "LED CLEAR is answered with ack.")
        self.expect("LED T99 red", "error", "LED with a row that does not exist (T99) gives an error.")
        self.expect("LED PT+ red", "error", "LED on a power strip (PT+, no light there) gives an error.")
        self.expect(f"LED {row} purple", "error", "LED with an unknown color (purple) gives an error.")
        cols = P.SCREEN_CHARS
        if h.get("has_screen"):
            self.expect("SHOW Board check|line 2", "ack", "SHOW with text that fits is answered with ack.")
            many = "|".join(f"line {i}" for i in range(1, P.SCREEN_LINES + 2))
            self.expect(f"SHOW {many}", "error",
                        f"SHOW with {P.SCREEN_LINES + 1} lines gives an error (the screen holds "
                        f"{P.SCREEN_LINES}).", msg_has="too long")
            self.expect("SHOW " + "x" * (cols + 1), "error",
                        f"SHOW with a line of {cols + 1} characters gives an error (a line holds "
                        f"{cols}).", msg_has="too long")
        else:
            self.expect("SHOW hello", "error", 'SHOW gives the error "no screen" (no screen).',
                        msg_has="no screen")
        if h.get("has_power_switch"):
            if self.expect("CUT", "ack", "CUT is answered with ack."):
                after = self.fresh_readings(3)
                ok = bool(after) and all(r.get("power_on") is False for r in after)
                self.rule(ok, 'After CUT, readings say "power_on": false.',
                          "" if ok else f"power_on after CUT: {[r.get('power_on') for r in after]}")
            if self.expect("RESTORE", "ack", "RESTORE is answered with ack."):
                after = self.fresh_readings(3)
                ok = bool(after) and after[-1].get("power_on") is True
                self.rule(ok, 'After RESTORE, readings say "power_on": true.',
                          "" if ok else f"power_on after RESTORE: {[r.get('power_on') for r in after]}")
        else:
            self.expect("CUT", "error", 'CUT gives the error "no power switch" (no power switch).',
                        msg_has="no power switch")
            self.expect("RESTORE", "error",
                        'RESTORE gives the error "no power switch" (no power switch).',
                        msg_has="no power switch")
        answer = self.command("HELLO")
        self.rule(answer is not None and answer.get("type") == "hello" and answer == self.hello,
                  "HELLO is answered with the same hello line as before.",
                  "" if answer == self.hello else f"answered {json.dumps(answer)[:150]}")
        self.expect("FLY AWAY", "error", "An unknown command (FLY AWAY) gives an error.")
        after = self.fresh_readings(3)
        self.rule(len(after) == 3, "Readings keep coming after the commands.")
        self.drain(0.3)
        self.rule(not self.stray, "No extra answer lines (each command got exactly one).",
                  f"extra: {self.stray[0][:100]}" if self.stray else "")

    # --- 4. Buttons and power cut (need a person) -----------------------------
    def check_buttons(self, ask):
        self.section("Buttons")
        n = self.hello.get("buttons", 0)
        if ask is None:
            self.rule(None, "Buttons not tested.", "Run again with --buttons to press each one.")
            return
        if n == 0:
            self.rule(None, "This board says it has no buttons.")
            return
        for i in range(1, n + 1):
            ask("button", i)
            got = self._wait_event(lambda e: e.get("type") == "button", self.event_wait)
            self.rule(got is not None and got.get("id") == i,
                      f'Button {i} ({P.BUTTON_LABELS.get(i, "?")}) sends {{"type": "button", "id": {i}}}.',
                      "" if got and got.get("id") == i else
                      (f"nothing came in {self.event_wait:g} s" if got is None
                       else f"got {json.dumps(got)}"))

    def check_power_cut(self, ask):
        self.section("Safety: the board cuts power by itself")
        h = self.hello
        if not (h.get("has_current") and h.get("has_power_switch")):
            self.rule(None, "Not needed: only a board with a current sensor and a power switch "
                      "must cut power by itself.")
            return
        if ask is None:
            self.rule(None, "Power cut not tested.",
                      "Run again with --power-cut (see docs/FIRMWARE_GUIDE.md, part 6).")
            return
        ask("power_cut", None)
        cut = self._wait_event(lambda e: e.get("type") == "power_cut", self.event_wait)
        if not self.rule(cut is not None, 'The board sends a "power_cut" line when the current '
                         "is too high.", "" if cut else f"nothing came in {self.event_wait:g} s"):
            return
        self.rule(cut.get("reason") in P.POWER_CUT_REASONS,
                  f'"reason" is one of {", ".join(P.POWER_CUT_REASONS)}.', f"it is {cut.get('reason')!r}")
        cur = cut.get("current_ma")
        self.rule(isinstance(cur, (int, float)) and not isinstance(cur, bool),
                  '"current_ma" is the current it saw (a number).', f"it is {cur!r}")
        self.rule(isinstance(cut.get("t_ms"), int), '"t_ms" says when it happened.')
        after = self.fresh_readings(5)
        ok = bool(after) and all(r.get("power_on") is False for r in after)
        self.rule(ok, 'After the cut, readings say "power_on": false, and power stays off.',
                  "" if ok else f"power_on after the cut: {[r.get('power_on') for r in after]}")
        self.expect("RESTORE", "ack", "RESTORE turns power back on after a cut (ack).")

    def _wait_event(self, test, wait):
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            for i, e in enumerate(self.events):
                if test(e):
                    return self.events.pop(i)
            self.wait_for(("button", "power_cut"), min(0.2, max(0.0, deadline - time.monotonic())))
        for i, e in enumerate(self.events):
            if test(e):
                return self.events.pop(i)
        return None

    # --- All ------------------------------------------------------------------
    def run(self, buttons_ask=None, power_cut_ask=None):
        if self.check_hello():
            self.check_readings()
            self.check_commands()
            self.check_buttons(buttons_ask)
            self.check_power_cut(power_cut_ask)
        fails = [r for r in self.results if r[0] == FAIL]
        passes = [r for r in self.results if r[0] == PASS]
        self.out("")
        if fails:
            self.out(f"RESULT: FAIL. {len(fails)} rule(s) failed, {len(passes)} passed. "
                     "Fix the first FAIL first: later ones often come from it.")
        else:
            self.out(f"RESULT: PASS. All {len(passes)} rules passed.")
        return not fails


def tell_person(what, n):
    """Ask the person at the board to do something."""
    if what == "button":
        print(f"\n>>> Press button {n} ({P.BUTTON_LABELS.get(n, '?')}) on the board now "
              "(you have 30 seconds).", flush=True)
    else:
        print("\n>>> Make the board cut power now (you have 30 seconds). Safe way: in the "
              "firmware set the cut limit low (for example 10 mA), upload it, then build "
              "lesson 1 (LED with the 220 ohm resistor, about 14 mA). Do NOT short the "
              "supply.", flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check a board's firmware against the serial protocol")
    p.add_argument("--serial", metavar="PORT", help='the board\'s port (COM3, /dev/ttyUSB0) or "auto"')
    p.add_argument("--baud", type=int, default=P.DEFAULT_BAUD,
                   help=f"serial speed (default {P.DEFAULT_BAUD})")
    p.add_argument("--count", type=int, default=20, help="how many readings to check (default 20)")
    p.add_argument("--buttons", action="store_true", help="also test the buttons (you press them)")
    p.add_argument("--power-cut", action="store_true",
                   help="also test the safety cut (see docs/FIRMWARE_GUIDE.md)")
    p.add_argument("--fake", action="store_true", help="check the fake board instead (no hardware)")
    args = p.parse_args(argv)

    if args.fake:
        from sim.fake_board import FakeBoard
        from sim.fake_port import FakeSerialPort
        board = FakeBoard(rows="T1-T16,PT+,PT-")
        port = FakeSerialPort(board)

        def fake_ask(what, n):
            board.send(f"PRESS {n}" if what == "button" else "SCENARIO 1 no_resistor")
        ask = fake_ask
        name = "the fake board"
    elif args.serial:
        try:
            import serial
            from agent.serial_board import BoardError, choose_port
        except ImportError:
            print("pyserial is not installed. Run: python -m pip install -r requirements.txt")
            return 2
        try:
            name = choose_port(args.serial)
            port = serial.Serial(name, args.baud, timeout=0, write_timeout=1)
        except BoardError as e:
            print(e)
            return 2
        except (OSError, ValueError) as e:
            print(f"Cannot open {args.serial}: {e}\nIs the board plugged in? Is the Arduino "
                  "Serial Monitor (or the tutor page) still using the port? Close it and try again.")
            return 2
        ask = tell_person
    else:
        p.print_help()
        print("\nGive --serial PORT (or --serial auto), or --fake to try it without a board.")
        return 2

    print(f"Checking {name} at {args.baud} baud, protocol {P.PROTOCOL_VERSION}.")
    checker = Checker(port, count=args.count)
    ok = checker.run(buttons_ask=ask if args.buttons else None,
                     power_cut_ask=ask if args.power_cut else None)
    try:
        port.close()
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
