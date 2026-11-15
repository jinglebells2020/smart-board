"""Where on the breadboard the student built the lesson circuit.

A lesson file names fixed rows (lesson 1: rows 5, 10 and 12 in the top
half). A student may build the same circuit somewhere else: lower down
(rows 20, 25, 27) or in the bottom half. The parts and their spacing are
the same; only the place moved.

find_placement() looks at which rows have something connected and finds
the shift that puts the lesson's rows on them. shift_lesson() then gives a
copy of the lesson with every row name moved, so the check, the clues,
the lights and the detective all use the rows the student really used.

A placement is (half, offset): half "T" (top) or "B" (bottom), offset the
number of rows the circuit moved (0 = where the lesson file says).
Only the place can change, not the spacing: a resistor still has to span
the same number of rows as in the lesson.
"""

import copy
import re

HOME = ("T", 0)
ROW = re.compile(r"^([TB])(\d+)$")
RAIL = re.compile(r"^P([TB])([+-])$")
LAST_ROW = 63
MIN_DRIVEN = 2  # at least this many lesson rows must have something in them


def shift_name(name, placement):
    """One row name moved: T5 with ("T", 15) -> T20; PT+ with ("B", 0) -> PB+."""
    half, offset = placement
    m = ROW.match(name)
    if m:
        return f"{half}{int(m.group(2)) + offset}"
    m = RAIL.match(name)
    if m:
        return f"P{half}{m.group(2)}"
    return name


def _walk(value, placement):
    if isinstance(value, str):
        return shift_name(value, placement)
    if isinstance(value, list):
        return [_walk(v, placement) for v in value]
    if isinstance(value, dict):
        return {shift_name(k, placement): _walk(v, placement) for k, v in value.items()}
    return value


def shift_lesson(lesson, placement):
    """A copy of the lesson with every row name moved. Only strings that are
    exactly a row name (like "T10" or "PT+") change; sentences do not, and
    the lesson files never write row numbers inside sentences."""
    if tuple(placement) == HOME:
        return lesson
    return _walk(copy.deepcopy(lesson), placement)


def fits(lesson, placement, measured):
    """Can the lesson's rows move there: still rows 1 to 63, and all measured?"""
    for name in lesson.get("rows", []):
        moved = shift_name(name, placement)
        m = ROW.match(moved)
        if not m or not 1 <= int(m.group(2)) <= LAST_ROW or moved not in measured:
            return False
    return True


def driven_rows(readings):
    """Rows with something connected in most readings (power on)."""
    on = [r for r in readings if r.get("power_on")]
    if not on:
        return set()
    measured = set.intersection(*(set(r["rows"]) for r in on))
    return {n for n in measured
            if sum(n in r.get("floating", []) for r in on) <= len(on) / 2}


def find_placement(readings, lesson, current=HOME):
    """The placement that fits the readings best, or `current` when nothing
    fits clearly better.

    Score = how many of the lesson's rows, moved, have something connected.
    We move only if one placement scores more than the current one, scores
    at least MIN_DRIVEN, and no other placement scores the same (so a
    half-built or messy board never makes it jump around)."""
    rows = lesson.get("rows", [])
    if not rows or not readings:
        return current
    driven = driven_rows(readings)
    if not driven:
        return current  # power off or nothing built: keep what we had
    measured = set.intersection(*(set(r["rows"]) for r in readings))

    def score(placement):
        return sum(shift_name(n, placement) in driven for n in rows)

    here = score(current) if fits(lesson, current, measured) else -1
    numbers = [int(ROW.match(n).group(2)) for n in rows if ROW.match(n)]
    if not numbers:
        return current
    scores = {}
    for half in ("T", "B"):
        for offset in range(1 - min(numbers), LAST_ROW - max(numbers) + 1):
            p = (half, offset)
            if p != tuple(current) and fits(lesson, p, measured):
                scores[p] = score(p)
    if not scores:
        return current
    best = max(scores.values())
    winners = [p for p, s in scores.items() if s == best]
    if best > here and best >= MIN_DRIVEN and len(winners) == 1:
        return winners[0]
    return current


def find_all_placements(readings, lesson, max_copies):
    """Every place where a copy of the lesson circuit is built, for a lesson
    with "copies" (lesson 3: 1 to 3 LED circuits). Lessons without "copies"
    use find_placement() instead.

    Same scoring as find_placement: how many of the lesson's rows, moved,
    have something connected. A copy needs all of them, or at least
    MIN_DRIVEN for a copy with a mistake (a row left empty). Two copies
    never share a row. Best score first, then the lowest row first (top
    half before bottom half), at most max_copies.
    Returns a list of placements; [] when nothing is built (or power is off)."""
    rows = lesson.get("rows", [])
    if not rows or not readings:
        return []
    driven = driven_rows(readings)
    if not driven:
        return []
    measured = set.intersection(*(set(r["rows"]) for r in readings))
    numbers = [int(ROW.match(n).group(2)) for n in rows if ROW.match(n)]
    if not numbers:
        return []
    candidates = []
    for half in ("T", "B"):
        for offset in range(1 - min(numbers), LAST_ROW - max(numbers) + 1):
            p = (half, offset)
            if not fits(lesson, p, measured):
                continue
            score = sum(shift_name(n, p) in driven for n in rows)
            if score == len(rows) or score >= MIN_DRIVEN:
                candidates.append((score, p))
    candidates.sort(key=lambda c: (-c[0], c[1][0] != "T", min(numbers) + c[1][1]))
    chosen, used = [], set()
    for _, p in candidates:
        moved = {shift_name(n, p) for n in rows}
        if moved & used:
            continue  # two copies never share a row
        chosen.append(p)
        used |= moved
        if len(chosen) >= max_copies:
            break
    return chosen


def describe(placement, lesson):
    """Plain words for the student, e.g. "rows 20 to 27 (top half)"."""
    rows = [shift_name(n, placement) for n in lesson.get("rows", [])]
    numbers = sorted(int(ROW.match(n).group(2)) for n in rows if ROW.match(n))
    if not numbers:
        return ""
    half = "top" if placement[0] == "T" else "bottom"
    return f"rows {numbers[0]} to {numbers[-1]} ({half} half)"
