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
number of rows the circuit moved (0 = where the lesson file says). That
keeps the spacing: a resistor spans the same number of rows as in the
lesson.

"Any spacing": a placement can also be a row map, a tuple of
(lesson_row, real_row) pairs, e.g. (("T5", "T20"), ("T10", "T27"),
("T12", "T29")): the same circuit with the parts spanning other numbers of
rows. find_flexible() finds row maps from groups of rows that have
something connected. find_placement() and find_all_placements() try the
exact spacing first and use row maps only when that does not fully match.
"""

import copy
import itertools
import re

HOME = ("T", 0)
ROW = re.compile(r"^([TB])(\d+)$")
RAIL = re.compile(r"^P([TB])([+-])$")
LAST_ROW = 63
MIN_DRIVEN = 2  # at least this many lesson rows must have something in them
MAX_GAP = 10    # rows of one circuit are at most this many rows apart (any spacing)
MAX_CLUSTER = 8  # a bigger group of rows is not searched part by part
HIGH = 0.9      # a row at 90 % of the supply or more is the + end of the chain
TIE_V = 0.3     # row maps whose voltage errors differ less than this are a tie


def is_row_map(placement):
    """Is this placement a row map ((lesson_row, real_row), ...)?"""
    return (isinstance(placement, tuple) and len(placement) > 0
            and all(isinstance(pair, tuple) and len(pair) == 2 for pair in placement))


def _map_half(placement):
    """The half (T or B) of a row map's real rows."""
    for _, real in placement:
        m = ROW.match(real)
        if m:
            return m.group(1)
    return "T"


def shift_name(name, placement):
    """One row name moved: T5 with ("T", 15) -> T20; PT+ with ("B", 0) -> PB+.
    With a row map: T5 with (("T5", "T20"), ...) -> T20; the + and - lines
    follow the half of the mapped rows; rows not in the map stay as they are."""
    if is_row_map(placement):
        mapped = dict(placement)
        if name in mapped:
            return mapped[name]
        m = RAIL.match(name)
        if m:
            return f"P{_map_half(placement)}{m.group(2)}"
        return name
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


def _numbers(names):
    return [int(ROW.match(n).group(2)) for n in names if ROW.match(n)]


def _voltages(readings):
    """Average voltage per row, and the average supply, over power-on readings."""
    on = [r for r in readings if r.get("power_on")] or readings
    rows = {}
    for name in set.intersection(*(set(r["rows"]) for r in on)):
        rows[name] = sum(r["rows"][name] for r in on) / len(on)
    supply = sum(r.get("supply_v", 0.0) for r in on) / len(on)
    return rows, supply


def _orient(group, lesson_rows, volts, supply):
    """A row map for exactly k rows in a group: in order, or reversed when the
    last row is at the supply voltage and the first is not (built the other
    way round). lesson_rows are in chain order (first: wired to +)."""
    high = HIGH * supply
    if volts.get(group[-1], 0.0) >= high and volts.get(group[0], 0.0) < high:
        group = list(reversed(group))
    return tuple(zip(lesson_rows, group))


def _best_subset(group, lesson_rows, expected, volts):
    """For a group with more rows than the lesson: the order-preserving pick
    of k rows (either way round) whose voltages are closest to the lesson's
    expected ones. A dangling part copies its neighbour's voltage, so picks
    within TIE_V of the best are a tie; then the spacing closest to the
    lesson's wins."""
    k = len(lesson_rows)
    gaps = [b - a for a, b in zip(_numbers(lesson_rows), _numbers(lesson_rows)[1:])]
    options = []
    for pick in itertools.combinations(group, k):
        for rows in (list(pick), list(reversed(pick))):
            cost = sum(abs(volts.get(real, 0.0) - expected.get(lesson_row, 0.0))
                       for lesson_row, real in zip(lesson_rows, rows))
            real_gaps = [abs(b - a) for a, b in zip(_numbers(rows), _numbers(rows)[1:])]
            shape = sum(abs(g - h) for g, h in zip(real_gaps, gaps))
            options.append((cost, shape, tuple(zip(lesson_rows, rows))))
    best = min(c for c, _, _ in options)
    return min((o for o in options if o[0] <= best + TIE_V), key=lambda o: (o[1], o[0]))[2]


def find_flexible(readings, lesson, exclude=frozenset()):
    """Row maps for the lesson circuit built with any spacing ("any spacing").

    Per half (top, then bottom): the rows with something connected (minus
    `exclude`) are split into groups, a new group starting after a gap of
    more than MAX_GAP rows. With k lesson rows (in chain order, the first
    wired to +):
    - a group of exactly k rows is mapped in order (or reversed, see _orient);
    - a bigger group (up to MAX_CLUSTER rows) gives the k rows whose voltages
      fit the lesson best (_best_subset); the others are left for the
      extra-part check;
    - a smaller group is skipped (the exact spacing handles missing parts);
    - in a lesson with "copies", a group of 2k or 3k rows is split in order
      into groups of k, one row map each.
    Returns a list of row maps (tuples, so they can be compared and copied)."""
    lesson_rows = sorted(lesson.get("rows", []), key=lambda n: _numbers([n]) or [0])
    expected = lesson.get("expected", {}).get("rows", {})
    k = len(lesson_rows)
    if not k or not readings or len(_numbers(lesson_rows)) != k:
        return []
    driven = driven_rows(readings) - set(exclude)
    if not driven:
        return []
    volts, supply = _voltages(readings)
    maps = []
    for half in ("T", "B"):
        rows = sorted((n for n in driven if ROW.match(n) and n[0] == half),
                      key=lambda n: int(n[1:]))
        groups, group = [], []
        for name in rows:
            if group and int(name[1:]) - int(group[-1][1:]) > MAX_GAP:
                groups.append(group)
                group = []
            group.append(name)
        if group:
            groups.append(group)
        for group in groups:
            if len(group) < k:
                continue
            if len(group) == k:
                maps.append(_orient(group, lesson_rows, volts, supply))
            elif lesson.get("copies") and len(group) % k == 0 and len(group) // k > 1:
                maps.extend(_orient(group[i:i + k], lesson_rows, volts, supply)
                            for i in range(0, len(group), k))
            elif len(group) <= MAX_CLUSTER:
                maps.append(_best_subset(group, lesson_rows, expected, volts))
    return maps


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

    Exact spacing first. Score = how many of the lesson's rows, moved, have
    something connected. We move only if one placement scores more than the
    current one, scores at least MIN_DRIVEN, and no other placement scores
    the same (so a half-built or messy board never makes it jump around).

    Only when no exact-spacing placement matches every lesson row: any
    spacing (find_flexible). If that finds exactly one row map, it is used,
    except when the circuit is already being checked at a fixed-spacing
    place that still matches at least MIN_DRIVEN rows and shares rows with
    the row map: then the student built it there with a part in a wrong
    row, and the exact spacing says which part is missing."""
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
    numbers = _numbers(rows)
    if not numbers:
        return current
    scores = {}
    for half in ("T", "B"):
        for offset in range(1 - min(numbers), LAST_ROW - max(numbers) + 1):
            p = (half, offset)
            if p != tuple(current) and fits(lesson, p, measured):
                scores[p] = score(p)
    best = max(scores.values()) if scores else -1
    if max(best, here) < len(rows):  # no exact-spacing match: try any spacing
        flexible = find_flexible(readings, lesson)
        if len(flexible) == 1:
            found = flexible[0]
            current_rows = {shift_name(n, current) for n in rows}
            if (not is_row_map(current) and here >= MIN_DRIVEN and here >= best
                    and current_rows & {real for _, real in found}):
                return current  # built here, a part in a wrong row
            return found
    if not scores:
        return current
    winners = [p for p, s in scores.items() if s == best]
    if best > here and best >= MIN_DRIVEN and len(winners) == 1:
        return winners[0]
    return current


def find_all_placements(readings, lesson, max_copies):
    """Every place where a copy of the lesson circuit is built, for a lesson
    with "copies" (lesson 3: 1 to 3 LED circuits). Lessons without "copies"
    use find_placement() instead.

    1. Exact-spacing copies with every lesson row connected.
    2. Any spacing (find_flexible) on the rows not used yet.
    3. Exact-spacing copies with a mistake: at least MIN_DRIVEN rows
       connected (a row left empty).
    Two copies never share a row; at most max_copies. Within 1 and 3: best
    score first, then the lowest row first (top half before bottom half).
    Returns a list of placements; [] when nothing is built (or power is off)."""
    rows = lesson.get("rows", [])
    if not rows or not readings:
        return []
    driven = driven_rows(readings)
    if not driven:
        return []
    measured = set.intersection(*(set(r["rows"]) for r in readings))
    numbers = _numbers(rows)
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

    def take(p):
        moved = {shift_name(n, p) for n in rows}
        if moved & used or len(chosen) >= max_copies:
            return  # two copies never share a row
        chosen.append(p)
        used.update(moved)

    for score, p in candidates:
        if score == len(rows):
            take(p)
    for p in find_flexible(readings, lesson, exclude=used):
        take(p)
    for score, p in candidates:
        if score < len(rows):
            take(p)
    return chosen


def describe(placement, lesson):
    """Plain words for the student, e.g. "rows 20 to 27 (top half)"."""
    rows = [shift_name(n, placement) for n in lesson.get("rows", [])]
    numbers = sorted(_numbers(rows))
    if not numbers:
        return ""
    half = _map_half(placement) if is_row_map(placement) else placement[0]
    return f"rows {numbers[0]} to {numbers[-1]} ({'top' if half == 'T' else 'bottom'} half)"
