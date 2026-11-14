"""Serial protocol version 2: the names and rules shared by the fake
board, the agent and the web page. Full description:
docs/SERIAL_PROTOCOL.md.

Everything that must be the same in several places lives here, once:
row names, light colors and what they mean, what the 4 board buttons
do, and the screen size.
"""

import re

PROTOCOL_VERSION = 2

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

# --- Screen -------------------------------------------------------------
# The real screen size is not known yet. Change it here and only here.
# The laptop never sends more than this.
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
