"""The laptop's handle on a board: the fake board now, the real one later.

A board only needs four methods: hello(), read(), send(command) and
events(). BoardLink adds what the laptop needs on top:

- what this board has (from its hello line): rows, current sensor,
  power switch, screen, buttons, and its supply voltage ("supply_v", the
  station sends 3.3; protocol.SUPPLY_V when a board does not say). Every
  reading it hands on carries that as "nominal_v" (a laptop-only field), so
  every check judges "low supply" against this board's own supply;
- the lights and the screen text as the board last confirmed them (its
  ack), so the web page can show them for any board. A refused or
  unanswered command never leaves a stale picture: what the board may be
  showing instead is dropped (a station that is reconnected starts dark,
  because SerialBoard sends LED CLEAR after its hello);
- lights that stay on for a while and then go off by themselves
  (clear_lights_later: the green "correct" lights after a lesson passes);
- checks before sending: only real rows, only the 5 colors, and screen
  text always fitted to SCREEN_LINES x SCREEN_CHARS;
- one lock, so the web page and the tutor can share the board safely;
- usable rows (--usable-rows): on a board where only some rows work, the
  others are left out of every reading (never measured, never "empty")
  and are never lit.
"""

import threading
import time
from collections import deque

import protocol as P


class BoardLink:
    def __init__(self, board, usable_rows=None):
        self.board = board
        # The rows that work: a list of T/B rows, or None for every row.
        usable = P.parse_usable(usable_rows) if isinstance(usable_rows, str) else usable_rows
        self.usable = None if usable is None or set(usable) >= set(P.ALL_ROWS) \
            else set(usable)
        self.lock = threading.RLock()
        hello = board.hello()
        if hello.get("protocol") != P.PROTOCOL_VERSION:
            raise ValueError(f"board speaks protocol {hello.get('protocol')}, "
                             f"this laptop needs {P.PROTOCOL_VERSION}")
        self.hello = hello
        self.lights = {}   # row -> color, as the board last confirmed
        self.screen = []   # lines, as the board last confirmed ([]: blank or not known)
        # A real board counts its connections: a new one means the board may
        # have restarted, and it got LED CLEAR (agent/serial_board.py).
        self._connects = getattr(board, "connects", None)
        # clear_lights_later(): when the lights go off by themselves (or None).
        self._clear_at = None
        # Power cuts the board made by itself (power_cut lines), oldest first.
        self.power_cuts = []
        # The latest one since power was last turned on, or None.
        self.last_cut = None
        # The last readings taken while power was on, and a copy of them made
        # when the board cut power: they show what the student had built.
        self._recent_on = deque(maxlen=10)
        self.before_cut = []
        self._buttons = []

    # --- What this board has ----------------------------------------
    @property
    def rows(self):
        return [r for r in self.hello["rows"] if self.is_usable(r)]

    def is_usable(self, row):
        """Can this row be used? The + and - lines always can."""
        return self.usable is None or not P.is_row(row) or row in self.usable

    @property
    def usable_rows(self):
        """Every usable row (T and B rows) this board measures, in board
        order: the rows in its hello line, and in --usable-rows if given."""
        measured = set(self.hello["rows"])
        return [r for r in P.ALL_ROWS if r in measured and (self.usable is None or r in self.usable)]

    def _usable_only(self, reading):
        """The reading with only usable rows, and this board's supply voltage
        added as "nominal_v" (laptop only: the board never sends it)."""
        if not isinstance(reading, dict):
            return reading
        reading = dict(reading, nominal_v=self.supply_v)
        if self.usable is None or not isinstance(reading.get("rows"), dict):
            return reading
        return dict(reading, rows={r: v for r, v in reading["rows"].items() if self.is_usable(r)},
                    floating=[r for r in reading.get("floating", []) if self.is_usable(r)])

    @property
    def unmapped(self):
        """The station before its row map is flashed: its hello line lists no
        rows at all, so nothing can be measured yet."""
        return not self.hello.get("rows")

    @property
    def has_current(self):
        return bool(self.hello["has_current"])

    @property
    def has_power_switch(self):
        return bool(self.hello["has_power_switch"])

    @property
    def has_screen(self):
        return bool(self.hello["has_screen"])

    @property
    def buttons(self):
        return int(self.hello["buttons"])

    @property
    def supply_v(self):
        """The board's supply voltage: its hello line's "supply_v", else
        protocol.SUPPLY_V (3.3 V, the station)."""
        return P.supply_of(self.hello)

    def capabilities(self):
        return {"rows": len(self.rows), "has_current": self.has_current,
                "has_power_switch": self.has_power_switch,
                "has_screen": self.has_screen, "buttons": self.buttons,
                "supply_v": self.supply_v}

    # --- Talking to the board ---------------------------------------
    def read(self):
        with self.lock:
            reading = self._usable_only(self.board.read())
            self._reconnected()
            if reading.get("power_on"):
                self._recent_on.append(reading)
            self.poll()  # a power_cut line can come right after this reading
            return reading

    def send(self, command):
        with self.lock:
            answer = self.board.send(command)
            self._reconnected()
            return answer

    def _reconnected(self):
        """The real board was connected again (it may have restarted, and it
        got LED CLEAR): its lights are off and its screen is not known."""
        n = getattr(self.board, "connects", None)
        if n != self._connects:
            self._connects = n
            self.lights.clear()
            self.screen = []

    def poll(self):
        """Collect lines the board sent on its own: button presses wait for
        events(); power_cut lines are remembered here. Lights waiting to go
        off by themselves (clear_lights_later) go off here when it is time."""
        with self.lock:
            self._clear_if_due()
            for ev in self.board.events():
                if ev.get("type") == "button":
                    self._buttons.append(ev)
                elif ev.get("type") == "power_cut":
                    self.power_cuts.append(ev)
                    self.last_cut = ev
                    # A real board's line brings the readings that came just
                    # before it (the laptop may have skipped them).
                    self.before_cut = [self._usable_only(r) for r in
                                       (ev.get("readings_before") or self._recent_on)]

    def events(self):
        """Button presses since the last call."""
        with self.lock:
            self.poll()
            out, self._buttons = self._buttons, []
            return out

    # --- Lights -----------------------------------------------------
    def set_light(self, row, color):
        row, color = str(row).upper(), str(color).lower()
        if not P.is_row(row):
            return {"type": "error", "msg": f"{row} is not a row with a light"}
        if not self.is_usable(row):
            return {"type": "error", "msg": f"{row} cannot be used on this board"}
        if color not in P.COLORS:
            return {"type": "error", "msg": f"unknown color {color!r}; use one of {list(P.COLORS)}"}
        with self.lock:
            self._clear_if_due(now=True)  # old lights waiting to go off go first
            answer = self.send(f"LED {row} {color}")
            if answer.get("type") == "ack":
                if color == "off":
                    self.lights.pop(row, None)
                else:
                    self.lights[row] = color
            elif self._rail_down(answer):
                # A station firmware that switches every row light off while a
                # rail is down (older versions did): none of them is lit now.
                self.lights.clear()
            elif "did not answer" in str(answer.get("msg", "")):
                self.lights.pop(row, None)  # not known: never claim it is lit
            return answer

    def set_lights(self, rows, color):
        return [self.set_light(r, color) for r in rows]

    def clear_lights(self):
        with self.lock:
            self._clear_at = None
            answer = self.send("LED CLEAR")
            if answer.get("type") == "ack":
                self.lights.clear()
            return answer

    def clear_lights_later(self, seconds):
        """Leave the lights as they are for `seconds`, then switch them all
        off (checked on every read and poll). Lighting any row, or
        clear_lights, before then switches the old ones off at once."""
        with self.lock:
            self._clear_at = time.monotonic() + seconds

    def _clear_if_due(self, now=False):
        if self._clear_at is not None and (now or time.monotonic() >= self._clear_at):
            self.clear_lights()

    @staticmethod
    def _rail_down(answer):
        return answer.get("type") == "error" and "rail is down" in str(answer.get("msg", ""))

    # --- Screen -----------------------------------------------------
    def show(self, text):
        """Show text on the screen, fitted to its size ("|" = new line)."""
        if not self.has_screen:
            return {"type": "error", "msg": "no screen"}
        lines = P.fit_screen(text)
        with self.lock:
            answer = self.send("SHOW " + "|".join(lines))
            if answer.get("type") == "ack":
                self.screen = lines
            elif str(answer.get("msg", "")) not in ("too long", "no screen"):
                # Refused for another reason (a firmware that keeps its own
                # warning on the screen while a rail is down), or no answer:
                # what the screen shows now is not known. Never keep old text.
                self.screen = []
        return dict(answer, lines=lines)

    # --- Power ------------------------------------------------------
    def cut(self):
        return self.send("CUT")

    def restore(self):
        answer = self.send("RESTORE")
        if answer.get("type") == "ack":
            self.last_cut = None       # a new start: forget the old cut
            self._recent_on.clear()
        return answer
