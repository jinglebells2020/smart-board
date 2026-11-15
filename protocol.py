"""Serial protocol version 2: the names and rules shared by the fake
board, the agent and the web page. Full description:
docs/SERIAL_PROTOCOL.md.

Everything that must be the same in several places lives here, once:
row names, light colors and what they mean, what the 4 board buttons
do (and their colours on the station), the supply voltage, and the
screen size.
"""

import re

PROTOCOL_VERSION = 2

# --- Supply voltage -------------------------------------------------------
# The station (docs/HARDWARE.md) powers the breadboard from the 3.3 V
# regulator of its ESP32-S3 board: its mux chips take nothing outside
# 0-3.3 V. Every lesson is written for this voltage. A board whose hello
# line has "supply_v" is taken at its word (supply_of); the fake board
# defaults to SUPPLY_V and takes --supply.
SUPPLY_V = 3.3
# The + line below this share of the supply: something pulls a big current
# (a short, or an LED with no resistor). Not 0.9: the station's meter tops
# out at about 2.9 to 3.1 V and is good to about 0.1 V, so a healthy + line
# can read as low as about 2.8 V (0.85 x 3.3).
LOW_SUPPLY_SHARE = 0.85
# The station's meter (the ESP32-S3's ADC) reads 0 to about 2.9-3.1 V.
# Anything at or above its top is sent as the supply voltage ("at the
# supply"), and below it a number is good to about ADC_ACCURACY_V.
ADC_TOP_V = 2.9
ADC_ACCURACY_V = 0.1


def supply_of(hello):
    """The supply voltage a board says it has ("supply_v" in its hello
    line, a number above 0), else SUPPLY_V."""
    v = (hello or {}).get("supply_v")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 30:
        return float(v)
    return SUPPLY_V


def low_supply_v(supply=None):
    """Below this the + line counts as pulled down (LOW_SUPPLY_SHARE of the
    supply)."""
    return round(LOW_SUPPLY_SHARE * (supply or SUPPLY_V), 3)


def volts_words(v):
    """3.3 -> "3.3 volts", 5.0 -> "5 volts"."""
    return f"{v:g} volts"

# --- Rows ---------------------------------------------------------------
# A full-size breadboard has 63 numbered rows. Each row has two halves:
# holes a-e (top half, "T") and holes f-j (bottom half, "B").
ROWS_PER_HALF = 63
TOP_ROWS = [f"T{i}" for i in range(1, ROWS_PER_HALF + 1)]
BOTTOM_ROWS = [f"B{i}" for i in range(1, ROWS_PER_HALF + 1)]
ALL_ROWS = TOP_ROWS + BOTTOM_ROWS
# Power strips along the long edges: top + and -, bottom + and -.
RAILS = ["PT+", "PT-", "PB+", "PB-"]
ALL_NODES = ALL_ROWS + RAILS

# --- Lights -------------------------------------------------------------
# One light beside every row (T and B rows; the power strips have none).
# Each color has one meaning, everywhere: board, agent and web page.
COLORS = {
    "blue": "put a part here",
    "green": "correct",
    "red": "problem here",
    "yellow": "careful",
    "off": "light off",
}

# --- Buttons ------------------------------------------------------------
# The board has 4 buttons. This is the only place their meaning is set.
BUTTONS = {1: "check", 2: "hint", 3: "next_step", 4: "power"}
BUTTON_LABELS = {1: "Check my circuit", 2: "I need a hint",
                 3: "Next step", 4: "Power off / on"}
# The station's buttons have colours, not numbers (docs/HARDWARE.md). Text
# a student reads names a button by its colour (button_words).
BUTTON_COLORS = {1: "yellow", 2: "white", 3: "blue", 4: "red"}


def button_words(n):
    """How a student is told about a button: 1 -> "the yellow button"."""
    return f"the {BUTTON_COLORS[n]} button"


# --- Switching the power off by hand --------------------------------------
# The station has no power switch: the laptop's USB cable powers it all.
# The breadboard's + lines get their 3.3 V from rail feeds wired inside the
# station (soldered under the core's + lines and plugged into the logic
# board): they are not the student's to pull. The circuit takes its power
# from the + line through its own wires and legs (the red wire, one per LED
# circuit in lesson 3, or any wire or leg in free build).
# - The student's switch-off by hand: pull every wire, or leg, of the
#   circuit out of the + line. The + line stays at 3.3 V; the circuit's
#   rows, starting with the plan's + rows, go empty (floating). The board
#   keeps measuring (unplugging USB would stop it too).
# - The feed unplugged (a teacher unplugged the feed header, or a feed wire
#   came loose): the + line has no power feed. The station sends
#   "supply_v": 0 and lists its + taps (PT+, PB+) as floating. That is not
#   the student's switch-off, and never a short.
MANUAL_OFF_WORDS = "pull every wire out of the + line"
MANUAL_ON_WORDS = "push the wires back into the + line"
# The board screen's text for it (fit_screen keeps it to the screen size).
MANUAL_OFF_SCREEN = "DANGER!|Pull every wire|out of the + line|right now!"
NO_SWITCH_SCREEN = "No power switch.|Off: pull every wire|out of the + line.|On: push them back."
# Said when the student did switch the power off by hand after being asked.
HAND_OFF_SAY = (f"Good, the power is off. Now fix the circuit, then "
                f"{MANUAL_ON_WORDS}.")
HAND_OFF_SCREEN = "Power is off. Good!|Fix the circuit,|then push the wires|back into the + line"
# Said when the + line has no power feed (supply_v 0, its + taps floating).
NO_FEED_SAY = "The + line has no power. Ask a grown-up to check the station's power plug inside."
NO_FEED_SCREEN = "+ line: no power.|Ask a grown-up to|check the station's|power plug inside."
# The + line reads at most this (volts) and its + taps float: it has no feed.
NO_FEED_V = 0.1

# --- Power cut by the board itself ----------------------------------------
# A board with a current sensor and a power switch must switch power off by
# itself when the current is too high, and say so with one line:
#   {"type": "power_cut", "reason": "overcurrent", "current_ma": 104.2, "t_ms": 51230}
# These are the reasons, with plain words the laptop shows the student.
POWER_CUT_REASONS = {
    "overcurrent": "too much current was flowing",
    "short": "there was a short circuit",
    "overheat": "something got too hot",
    "other": "the board found a problem",
}
# Suggested firmware limit. The laptop cuts at 30 mA (SAFETY_MAX_MA in
# agent/lessons.py) when it sees it; the firmware cut is the backstop
# that works even when the laptop is slow or not connected.
FIRMWARE_CUT_MA = 50.0

# The serial speed both sides use unless told otherwise.
DEFAULT_BAUD = 115200


def power_cut_words(event):
    """Plain words for a power_cut line, for the student."""
    reason = POWER_CUT_REASONS.get(event.get("reason"), POWER_CUT_REASONS["other"])
    current = event.get("current_ma")
    seen = f" It measured {current:.0f} mA." if isinstance(current, (int, float)) else ""
    return (f"The board switched the power off by itself, because {reason}.{seen} "
            f"Fix the circuit, then press {button_words(4)} to turn the power back on.")


# --- Screen -------------------------------------------------------------
# The station's StickS3 shows a big row name plus text and holds 4 x 20
# easily. Change it here and only here. The laptop never sends more than this.
SCREEN_LINES = 4
SCREEN_CHARS = 20


def is_row(name):
    """True for T1..T63 and B1..B63 (rows that have a light)."""
    return name in _ROW_SET


def is_node(name):
    """True for any measurable name: a row or a power strip."""
    return name in _NODE_SET


_ROW_SET = set(ALL_ROWS)
_NODE_SET = set(ALL_NODES)


def parse_rows(spec):
    """Turn a short text into a list of names, in board order.

    Examples: "T1-T16" -> T1..T16; "T1-T16,B1-B16,PT+,PT-";
    "all" -> every row and power strip; "16" -> T1..T16.
    Raises ValueError for anything that is not a real name.
    """
    spec = str(spec).strip()
    if spec.lower() == "all":
        return list(ALL_NODES)
    if spec.isdigit():
        n = int(spec)
        if not 1 <= n <= ROWS_PER_HALF:
            raise ValueError(f"row count must be 1 to {ROWS_PER_HALF}")
        return TOP_ROWS[:n]
    names = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        m = re.fullmatch(r"([TB])(\d+)-(?:[TB])?(\d+)", part)
        if m:
            half, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            if not (1 <= a <= b <= ROWS_PER_HALF):
                raise ValueError(f"bad row range: {part}")
            names += [f"{half}{i}" for i in range(a, b + 1)]
        elif is_node(part):
            names.append(part)
        else:
            raise ValueError(f"not a row name: {part}")
    order = {name: i for i, name in enumerate(ALL_NODES)}
    return sorted(set(names), key=order.get)


USABLE_ROWS_ENV = "SMART_BOARD_USABLE_ROWS"


def parse_usable(spec):
    """The rows of this board that work (are wired and measured): every
    row (T1..T63, B1..B63) by default, or a list like "T3-T8,T20-T24,B40-B45".
    The + and - lines always work, so they are not listed. Rows outside the
    list are never measured, never treated as empty and never lit."""
    if spec is None or not str(spec).strip() or str(spec).strip().lower() == "all":
        return list(ALL_ROWS)
    rows = [r for r in parse_rows(spec) if r in _ROW_SET]
    if not rows:
        raise ValueError(f"no usable rows in {spec!r} (use for example T3-T8,B40-B45)")
    return rows


def row_label(name):
    """A row name in words a middle schooler knows: T10 -> "row 10"."""
    if name.startswith("T") and name[1:].isdigit():
        return f"row {name[1:]}"
    if name.startswith("B") and name[1:].isdigit():
        return f"row {name[1:]} (lower half)"
    return {"PT+": "the top + line", "PT-": "the top - line",
            "PB+": "the bottom + line", "PB-": "the bottom - line"}.get(name, name)


def _ascii(text):
    swaps = {"→": "->", "—": "-", "–": "-", "‘": "'", "’": "'",
             "“": '"', "”": '"', "…": "...", "Ω": "ohm"}
    text = "".join(swaps.get(ch, ch) for ch in str(text))
    return text.encode("ascii", "ignore").decode()


def fit_screen(text, lines=None, chars=None):
    """Fit text on the board screen: at most SCREEN_LINES lines of at
    most SCREEN_CHARS characters each. Words are wrapped, "|" starts a
    new line, and anything that does not fit ends with "..".
    Returns a list of lines (plain ASCII, no "|")."""
    lines = lines or SCREEN_LINES
    chars = chars or SCREEN_CHARS
    parts = text if isinstance(text, list) else str(text).split("|")
    out = []
    for part in parts:
        words = _ascii(part).replace("|", " ").split()
        line = ""
        for word in words:
            while len(word) > chars:  # a word longer than a line: cut it
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:chars])
                word = word[chars:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= chars:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    if len(out) > lines:
        out = out[:lines]
        last = out[-1]
        out[-1] = (last[: chars - 2] + "..") if len(last) > chars - 2 else last + ".."
    return out


def screen_command(text):
    """The SHOW command for some text, already fitted to the screen."""
    return "SHOW " + "|".join(fit_screen(text))
