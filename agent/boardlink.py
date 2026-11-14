"""The laptop's handle on a board: the fake board now, the real one later.

A board only needs four methods: hello(), read(), send(command) and
events(). BoardLink adds what the laptop needs on top:

- what this board has (from its hello line): rows, current sensor,
  power switch, screen, buttons;
- the lights and the screen text as the laptop last set them, so the
  web page can show them for any board;
- checks before sending: only real rows, only the 5 colors, and screen
  text always fitted to SCREEN_LINES x SCREEN_CHARS;
- one lock, so the web page and the tutor can share the board safely.
"""

import threading

import protocol as P


class BoardLink:
    def __init__(self, board):
        self.board = board
        self.lock = threading.RLock()
        hello = board.hello()
        if hello.get("protocol") != P.PROTOCOL_VERSION:
            raise ValueError(f"board speaks protocol {hello.get('protocol')}, "
                             f"this laptop needs {P.PROTOCOL_VERSION}")
        self.hello = hello
        self.lights = {}   # row -> color, as last set
        self.screen = []   # lines, as last shown

    # --- What this board has ----------------------------------------
    @property
    def rows(self):
        return list(self.hello["rows"])

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
        return {"rows": len(self.hello["rows"]), "has_current": self.has_current,
                "has_power_switch": self.has_power_switch,
                "has_screen": self.has_screen, "buttons": self.buttons}

    # --- Talking to the board ---------------------------------------
    def read(self):
        with self.lock:
            return self.board.read()

    def send(self, command):
        with self.lock:
            return self.board.send(command)

    def events(self):
        with self.lock:
            return self.board.events()

    # --- Lights -----------------------------------------------------
    def set_light(self, row, color):
        row, color = str(row).upper(), str(color).lower()
        if not P.is_row(row):
            return {"type": "error", "msg": f"{row} is not a row with a light"}
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
        return self.send("RESTORE")
