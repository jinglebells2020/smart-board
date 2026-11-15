"""The laptop's handle on a board: the fake board now, the real one later.

A board only needs four methods: hello(), read(), send(command) and
events(). BoardLink adds what the laptop needs on top:

- what this board has (from its hello line): rows, current sensor,
  power switch, screen, buttons;
- the lights and the screen text as the laptop last set them, so the
  web page can show them for any board;
- checks before sending: only real rows, only the 5 colors, and screen
  text always fitted to SCREEN_LINES x SCREEN_CHARS;
- one lock, so the web page and the tutor can share the board safely;
- usable rows (--usable-rows): on a board where only some rows work, the
  others are left out of every reading (never measured, never "empty")
  and are never lit.
"""

import threading
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
        self.lights = {}   # row -> color, as last set
        self.screen = []   # lines, as last shown
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
        """Every usable row (T and B rows), in board order."""
        return [r for r in P.ALL_ROWS if self.usable is None or r in self.usable]

    def _usable_only(self, reading):
        if self.usable is None or not isinstance(reading.get("rows"), dict):
            return reading
        return dict(reading, rows={r: v for r, v in reading["rows"].items() if self.is_usable(r)},
                    floating=[r for r in reading.get("floating", []) if self.is_usable(r)])

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

    def capabilities(self):
        return {"rows": len(self.rows), "has_current": self.has_current,
                "has_power_switch": self.has_power_switch,
                "has_screen": self.has_screen, "buttons": self.buttons}

    # --- Talking to the board ---------------------------------------
    def read(self):
        with self.lock:
            reading = self._usable_only(self.board.read())
            if reading.get("power_on"):
                self._recent_on.append(reading)
            self.poll()  # a power_cut line can come right after this reading
            return reading

    def send(self, command):
        with self.lock:
            return self.board.send(command)

    def poll(self):
        """Collect lines the board sent on its own: button presses wait for
        events(); power_cut lines are remembered here."""
        with self.lock:
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
        answer = self.send(f"LED {row} {color}")
        if answer.get("type") == "ack":
            if color == "off":
                self.lights.pop(row, None)
            else:
                self.lights[row] = color
        return answer

    def set_lights(self, rows, color):
        return [self.set_light(r, color) for r in rows]

    def clear_lights(self):
        answer = self.send("LED CLEAR")
        if answer.get("type") == "ack":
            self.lights.clear()
        return answer

    # --- Screen -----------------------------------------------------
    def show(self, text):
        """Show text on the screen, fitted to its size ("|" = new line)."""
        if not self.has_screen:
            return {"type": "error", "msg": "no screen"}
        lines = P.fit_screen(text)
        answer = self.send("SHOW " + "|".join(lines))
        if answer.get("type") == "ack":
            self.screen = lines
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
