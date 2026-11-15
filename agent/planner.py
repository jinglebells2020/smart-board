"""Free build: plan the experiment that tells the hypotheses apart (plain
Python, no AI).

When more than one circuit fits the measurements (agent/reader.py keeps
every hypothesis that passes the physics check), the board alone cannot
tell which one the student built. One small action can: turn around an
LED, press or let go of a push button, pull a part out, or move one leg to
another usable row.

plan_experiment() tries every such action on every hypothesis with
sim/solver.py and picks the one whose predicted readings differ most
between the hypotheses (how many rows, and the current, would come out
differently). An action that would make any hypothesis unsafe (more than
the 30 mA limit) is never chosen. which_hypothesis() then compares the
board's new readings with each prediction and keeps the one that came
true.
"""

import itertools

from agent.experiments import validate_instruction
from agent.lessons import FREE_LESSON, SAFETY_MAX_MA
from agent.reader import (CURRENT_TOLERANCE, CURRENT_TOLERANCE_MA, ROW_TOLERANCE_V, place_words,
                          problems_of)
from agent.reader import _solver_parts as _reader_solver_parts
from protocol import is_row
from sim import solver

CHANGED_MA = 0.5  # the current changed (it started or stopped flowing)
# The simplest actions first when two separate the hypotheses equally well.
VERBS = ("press", "let go", "turn around", "pull out", "move")


def _name(part):
    if part["kind"] == "resistor":
        o = part.get("ohms") or 0
        return f"{o / 1000:g}k ohm resistor" if o >= 1000 else f"{o:g} ohm resistor"
    if part["kind"] == "led":
        return "LED"
    if part["kind"] == "button":
        return "push button"
    return part["kind"]


def _where(rows):
    """The two places, in words the experiment check reads: "row 45 and the
    bottom - line", "rows 43 and 45"."""
    if all(is_row(r) for r in rows) and rows[0][0] == rows[1][0]:
        return f"rows {rows[0][1:]} and {rows[1][1:]}"
    rows = sorted(rows, key=lambda r: not is_row(r))  # the row first, then the line
    return f"{place_words(rows[0]).replace(' (lower half)', '')} and " \
           f"{place_words(rows[1]).replace(' (lower half)', '')}"


def _instruction(action):
    verb, part = action["verb"], action["part"]
    where = _where(part["rows"])
    if verb == "turn around":
        return f"Turn around the {_name(part)} in {where}."
    if verb == "press":
        return f"Press the push button in {where} and keep it pressed."
    if verb == "let go":
        return f"Let go of the push button in {where}."
    if verb == "pull out":
        return f"Pull out the {_name(part)} in {where}."
    return (f"Move the {_name(part)} leg in row {action['from'][1:]} "
            f"to row {action['to'][1:]}.")


def _same_part(a, b):
    return a["kind"] == b["kind"] and set(a["rows"]) == set(b["rows"]) and \
        a.get("ohms") == b.get("ohms")


def apply(action, parts):
    """The hypothesis after the action. A part the hypothesis does not have
    cannot be touched: then nothing changes."""
    out = [dict(p, rows=list(p["rows"])) for p in parts]
    target = action["part"]
    for i, p in enumerate(out):
        if action["verb"] == "move":
            if p["kind"] == target["kind"] and action["from"] in p["rows"] and \
                    set(p["rows"]) == set(target["rows"]):
                p["rows"] = [action["to"] if r == action["from"] else r for r in p["rows"]]
                return out
            continue
        if not _same_part(p, target):
            continue
        if action["verb"] == "turn around":
            p["rows"] = p["rows"][::-1]
        elif action["verb"] == "press":
            p["closed"] = True
        elif action["verb"] == "let go":
            p["closed"] = False
        elif action["verb"] == "pull out":
            out.pop(i)
        return out
    return out


def _solver_parts(parts):
    return [{k: v for k, v in p.items() if k in ("kind", "rows", "ohms", "color", "closed")}
            for p in parts if p["kind"] != "unknown"]


def predict(parts, rows):
    """What the board would read on these rows (None: empty), and the current."""
    sol = solver.solve(_solver_parts(parts))
    return {"rows": {r: sol["rows"].get(r) for r in rows}, "current_ma": round(sol["current_ma"], 3)}


def _differs(a, b, sensor):
    """How many readings come out differently in two predictions."""
    n = 0
    for r in a["rows"]:
        x, y = a["rows"][r], b["rows"][r]
        if (x is None) != (y is None) or (x is not None and abs(x - y) > ROW_TOLERANCE_V):
            n += 1
    if sensor:
        x, y = a["current_ma"], b["current_ma"]
        if abs(x - y) > max(CURRENT_TOLERANCE_MA, CURRENT_TOLERANCE * max(abs(x), abs(y))):
            n += 1
    return n


def _in_empty_rows(part, measured):
    """Is every leg of this part (in a usable row) in a row the board reads
    empty? Such a part cannot really be there: never plan around it."""
    rows = (measured or {}).get("rows", {})
    legs = [r for r in part["rows"] if is_row(r)]
    return bool(legs) and all(r in rows and rows[r] is None for r in legs)


def _on_driven(part, measured):
    """Does this part touch a row the board reads with a voltage (or a + / - line)?"""
    rows = (measured or {}).get("rows", {})
    return any((is_row(r) and rows.get(r) is not None) or r in ("PT+", "PT-", "PB+", "PB-")
               for r in part["rows"])


def _problems(parts):
    """The usual mistakes in a hypothesis, worked out with the solver."""
    sparts, index = _reader_solver_parts(parts)
    sol = solver.solve(sparts) if sparts else {"rows": {}, "leds_on": {}, "current_ma": 0.0}
    predicted = {"rows": sol["rows"], "leds_on": {index[i]: on for i, on in sol["leds_on"].items()}}
    return problems_of(parts, predicted), sol["current_ma"]


def candidate_actions(hypotheses, usable, measured=None):
    """Every allowed action on any part of any hypothesis (no duplicates).
    Never on a part whose legs are all in rows the board reads empty."""
    out, seen = [], set()
    used = {r for h in hypotheses for p in h for r in p["rows"]}
    free = [r for r in usable if r not in used]

    def add(action):
        key = (action["verb"], action["part"]["kind"], tuple(sorted(action["part"]["rows"])),
               action.get("from"), action.get("to"))
        if key not in seen:
            seen.add(key)
            out.append(action)

    for parts in hypotheses:
        for p in parts:
            if p["kind"] in ("wire", "unknown") or any(r in solver.UNSEEN for r in p["rows"]) \
                    or _in_empty_rows(p, measured):
                continue
            if p["kind"] == "led":
                add({"verb": "turn around", "part": p})
            if p["kind"] == "button":
                add({"verb": "let go" if p.get("closed") else "press", "part": p})
            add({"verb": "pull out", "part": p})
            for leg in p["rows"]:
                if is_row(leg):
                    near = sorted((r for r in free if r[0] == leg[0]),
                                  key=lambda r: abs(int(r[1:]) - int(leg[1:])))[:1]
                    for to in near:
                        add({"verb": "move", "part": p, "from": leg, "to": to})
    return out


def plan_experiment(hypotheses, measured, usable, about=None):
    """The action that best tells the hypotheses apart, or None (fewer than
    2 hypotheses, or no safe action changes anything differently).
    hypotheses: lists of parts; about: for each, {"circuit", "problem"}
    words to carry along. Returns {"verb", "action", "part" (words),
    "rows", "instruction", "predictions": [{"hypothesis", "rows",
    "current_ma"}...], "watch": rows, "separates": pairs told apart}."""
    if len(hypotheses) < 2:
        return None
    sensor = measured.get("current_source") == "measured"
    best, best_key = None, None
    top_problems, top_current = _problems(hypotheses[0])
    for action in candidate_actions(hypotheses, usable, measured):
        text = _instruction(action)
        ok, _error, _parsed = validate_instruction(text, FREE_LESSON)
        if not ok:
            continue
        after = [apply(action, h) for h in hypotheses]
        watch = sorted({r for h in hypotheses + after for p in h for r in p["rows"] if is_row(r)}
                       | {r for r, v in measured.get("rows", {}).items() if v is not None},
                       key=lambda r: (r[0], int(r[1:])))
        try:
            preds = [predict(h, watch) for h in after]
        except Exception:
            continue
        if any(p["current_ma"] > SAFETY_MAX_MA for p in preds):
            continue  # never ask for something that would be unsafe
        pairs = list(itertools.combinations(range(len(preds)), 2))
        diffs = [_differs(preds[i], preds[j], sensor) for i, j in pairs]
        told = sum(d > 0 for d in diffs)
        if told == 0:
            continue
        # Equally good at telling them apart? Then the action that would FIX
        # the problem in the most likely hypothesis (it has fewer problems
        # after, or current starts to flow), then one on a part in a row with
        # a voltage, then the one with the most differences, then the simplest.
        after_problems, after_current = _problems(after[0])
        fixes = len(after_problems) < len(top_problems) or \
            (top_current <= CHANGED_MA < after_current)
        key = (told, fixes, _on_driven(action["part"], measured), sum(diffs),
               -VERBS.index(action["verb"]))
        if best_key is None or key > best_key:
            best_key = key
            before = {"rows": {r: measured.get("rows", {}).get(r) for r in watch},
                      "current_ma": measured.get("current_ma")}
            best = {"verb": action["verb"], "action": action["verb"], "before": before,
                    "part": _name(action["part"]), "rows": list(action["part"]["rows"]),
                    "instruction": text, "watch": watch, "separates": told, "fixes": fixes,
                    "move": ({"from": action["from"], "to": action["to"]}
                             if action["verb"] == "move" else None),
                    "predictions": [dict(p, hypothesis=i, **((about or [{}] * len(preds))[i]))
                                    for i, p in enumerate(preds)]}
    return best


def _row_changed(a, b):
    return (a is None) != (b is None) or (a is not None and abs(a - b) > ROW_TOLERANCE_V)


def _current_changed(a, b):
    return abs((a or 0.0) - (b or 0.0)) > max(CHANGED_MA, CURRENT_TOLERANCE * max(abs(a or 0),
                                                                              abs(b or 0)))


def which_hypothesis(plan, measured):
    """After the student did the action: the hypothesis whose prediction
    came true. The guessed values (a resistor's ohms, an LED's colour) may
    be rough, so what counts is WHAT CHANGED from before: for every row
    watched (and the current), did it change or not, as predicted? Ties go
    to the closest numbers. Returns {"index", "matched", "total",
    "all_matched"} for the best one."""
    before = plan.get("before") or {"rows": {}, "current_ma": None}
    sensor = measured.get("current_source") == "measured" and \
        measured.get("current_ma") is not None and before.get("current_ma") is not None
    scores = []
    for pred in plan["predictions"]:
        matched = total = 0
        error = 0.0
        for r, p in pred["rows"].items():
            if r not in measured.get("rows", {}) or r not in before["rows"]:
                continue
            m, b = measured["rows"][r], before["rows"][r]
            total += 1
            matched += int(_row_changed(b, p) == _row_changed(b, m))
            if m is not None and p is not None:
                error += abs(m - p)
            elif (m is None) != (p is None):
                error += 5
        if sensor:
            total += 1
            cm, cp, cb = measured["current_ma"], pred["current_ma"], before["current_ma"]
            matched += int(_current_changed(cb, cp) == _current_changed(cb, cm))
            error += abs(cm - cp) / max(1.0, abs(cm))
        scores.append((matched / max(total, 1), -error, matched, total, pred["hypothesis"]))
    _score, _err, matched, total, index = max(scores, key=lambda s: (s[0], s[1], -s[4]))
    return {"index": index, "matched": matched, "total": total, "all_matched": matched == total}
