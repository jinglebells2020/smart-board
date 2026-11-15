"""Zip acts: the plain-Python side of the tutor's experiment and guide tools.

run_experiment: the tutor asks the student to do ONE thing with their
hands ("Turn around the LED in rows 10 and 12"), then the board measures
again and the tutor sees what changed. validate_instruction() makes sure
the instruction is one small, safe action on a part of the lesson, in a
row the board has; anything else goes back to the tutor to rephrase.
diff_facts() lists what changed between the facts before and after.

guide_next_part: which parts of the lesson look placed (every row they use
has something connected, and nothing shows they cannot be there). The
tutor picks the next part and lights its rows blue.
"""

import re

from agent.live import WIRE_DROP_V, _volts
from agent.physics import observations, part_kind
from protocol import row_label

ACTIONS = ["pull out", "push in", "turn around", "move", "press", "let go", "wait"]
# Each action, also split by a few words ("push it in", "pull the LED out").
_SPLIT = r"(?:\s+\S+){0,4}?\s+"
ACTION_PATTERNS = {
    "pull out": rf"\bpull{_SPLIT}out\b|\bpull out\b",
    "push in": rf"\bpush{_SPLIT}in\b|\bpush in\b",
    "turn around": rf"\bturn{_SPLIT}around\b|\bturn around\b",
    "move": r"\bmove\b",
    "press": r"\bpress\b",
    "let go": rf"\blet{_SPLIT}go\b|\blet go\b",
    "wait": r"\bwait\b",
}
NOT_ALLOWED = ["power", "battery", "supply", "plug in", "unplug", "cut", "switch"]
MAX_EXPERIMENTS = 2   # per reply
MAX_LABEL = 24
CHANGED_V = 0.3       # a row voltage that moved more than this "changed"
CHANGED_MA = 1.0      # a current that moved more than this "changed"


def _part_words(lesson):
    """{word: part name}: every layout part's full name and its kind word
    (led, resistor, wire, button)."""
    if lesson.get("kind") == "free":  # free build: no plan, any part of these kinds
        return {"led": "LED", "resistor": "resistor", "wire": "wire", "button": "push button"}
    words = {}
    for part in lesson.get("layout", []):
        name = part["part"]
        words[name.lower()] = name
        words.setdefault(part_kind(name), name)
    return words


def validate_instruction(instruction, lesson):
    """(ok, error, parsed). parsed: {"action", "part", "rows"}.
    One action from ACTIONS, a part of the lesson layout (or "the extra
    part"), and a row (row 1 to 63). Nothing about the power."""
    text = str(instruction or "").strip()
    low = text.lower()
    if not text or len(text) > 160:
        return False, "Give one short instruction (at most 160 characters).", None
    bad = [w for w in NOT_ALLOWED if re.search(rf"\b{re.escape(w)}\b", low)]
    if bad:
        return False, (f"Do not ask about '{bad[0]}': the student never touches the power in "
                       "an experiment."), None
    found = [(m.start(), a) for a, pattern in ACTION_PATTERNS.items()
             for m in re.finditer(pattern, low)]
    if len(found) != 1:
        return False, (f"Name exactly ONE action from: {', '.join(ACTIONS)} "
                       f"(found {len(found)})."), None
    action = found[0][1]
    words = _part_words(lesson)
    parts = [name for word, name in words.items() if re.search(rf"\b{re.escape(word)}\b", low)]
    if re.search(r"\bextra part\b", low):
        parts.append("the extra part")
    if not parts:
        return False, (f"Name a part of this lesson ({', '.join(p['part'] for p in lesson.get('layout', []))}) "
                       "or 'the extra part'."), None
    rows = _rows_in(low)
    if not rows:
        return False, "Name the row, for example 'in row 10'.", None
    if any(not 1 <= r <= 63 for r in rows):
        return False, "Rows go from 1 to 63.", None
    return True, None, {"action": action, "part": parts[0], "rows": rows}


def _rows_in(low):
    """Row numbers in an instruction: "row 10", "rows 10 and 12", "T10"."""
    rows = []
    for m in re.finditer(r"\brows?\s+(\d+)((?:\s*(?:,|and|to)\s*\d+)*)", low):
        rows.append(int(m.group(1)))
        rows += [int(n) for n in re.findall(r"\d+", m.group(2))]
    rows += [int(n) for n in re.findall(r"\b[tb](\d+)\b", low)]
    return rows


def label(text, default):
    text = str(text or "").strip()
    return text[:MAX_LABEL] if text else default


def facts_of(session, summary, result, safety_cut=False):
    lesson = session._step_lesson(session.lesson) if session.button_step else session.lesson
    return observations(summary, lesson, extra_rows=result.get("extra_rows"),
                        safety_cut=safety_cut)


def diff_facts(before, after):
    """What changed: [{"kind", "rows", "before", "after"}] (text, or None when
    the fact is new or gone), and plain sentences for the row voltages and
    empty rows ("row 12 changed from 5.0 V to 0.0 V")."""
    def key(f):
        return (f["kind"], tuple(f["rows"]))

    b = {key(f): f for f in before if f["kind"] != "power"}
    a = {key(f): f for f in after if f["kind"] != "power"}
    changed, words = [], []
    for k in list(b) + [k for k in a if k not in b]:
        fb, fa = b.get(k), a.get(k)
        if fb and fa and fb["text"] == fa["text"]:
            continue
        if fb and fa and _noise(k[0], fb.get("values", {}), fa.get("values", {})):
            continue  # the same fact, only the last digit moved
        if fb and fa and k[0] == "row_voltage":
            vb, va = fb["values"]["measured"], fa["values"]["measured"]
            words.append(f"{row_label(k[1][0])} changed from {vb:.1f} V to {va:.1f} V")
        elif k[0] == "plan_row_floating":
            words.append(f"{row_label(k[1][0])} is now empty" if fa
                         else f"{row_label(k[1][0])} now has something in it")
        changed.append({"kind": k[0], "rows": list(k[1]),
                        "before": fb["text"] if fb else None, "after": fa["text"] if fa else None})
    return changed, words


def _noise(kind, vb, va):
    """Did a fact only change by measuring noise?"""
    def near(key, limit):
        a, b = vb.get(key), va.get(key)
        return a is not None and b is not None and abs(a - b) <= limit

    if kind == "row_voltage":
        return near("measured", CHANGED_V)
    if kind == "part_drop":
        return near("drop", CHANGED_V) or (vb.get("drop") is None and va.get("drop") is None)
    if kind == "resistor_current":
        return near("ma", CHANGED_MA)
    if kind == "current_vs_expected":
        return near("ma", CHANGED_MA)
    if kind == "led_state":
        return vb.get("state") == va.get("state") and near("drop", CHANGED_V)
    if kind == "same_voltage":
        return vb.get("joined") == va.get("joined") and near("volts", CHANGED_V)
    return False


def placed_parts(lesson, summary):
    """[{"part", "rows", "placed"}] for every layout part, in layout order."""
    out = []
    for part in lesson.get("layout", []):
        rows = [leg["row"] for leg in part["legs"] if not leg["row"].startswith("P")]
        volts = [_volts(leg["row"], summary) for leg in part["legs"]]
        placed = all(v is not None for v in volts)
        if placed and "wire" in part["part"].lower() and len(volts) == 2 and \
                abs(volts[0] - volts[1]) > WIRE_DROP_V:
            placed = False  # impossible for a wire: not in yet
        out.append({"part": part["part"], "rows": rows,
                    "legs": [{"leg": leg["leg"], "row": leg["row"]} for leg in part["legs"]],
                    "placed": placed})
    return out
