"""Free build, phase 2: the circuit reader. Zip works out ANY circuit from
the measurements, and plain Python checks the answer against physics.

1. The "circuit_reader" subagent (Claude) gets what the board measured:
   the usable rows, every row's voltage (floating rows are left out: no
   path), the current, the supply, the parts tray and goal, and the plain
   facts. It answers with a CircuitGuess: the parts it thinks are there,
   on which rows: up to 3 hypotheses, as a plain pydantic schema
   (response_format=CircuitReading), not ToolStrategy, like the diagnoser.
2. Plain Python checks the guess (physics_check): sim/solver.py works out
   what the guessed circuit WOULD read, and every row (and the current) is
   compared with what the board DID read: within 0.3 V, and the current
   within 20 % (or 2 mA). Score = how many match.
3. If it does not match, the reader is told what did not match
   ("T21: you predict 2.0 V, measured 4.1 V") and tries once more. The
   best-scoring guess is kept. Nothing here ever raises (except a reply
   that was given up on, which must stop).

Offline (no ANTHROPIC_API_KEY), HeuristicReaderModel stands in: plain
Python that reads a single chain of parts from the voltages. It is the
baseline for tools/eval_free_build.py.

The true netlist (the fake board's) is never part of the reader's input:
only what the board measured.
"""

import itertools
import json
import math
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from agent.deadline import RunAbandoned
from protocol import is_row, row_label
from sim import solver

KINDS = ("wire", "resistor", "led", "button", "unknown")
RAILS = ("PT+", "PT-", "PB+", "PB-")
LED_VF = dict(solver.LED_VF)
MAX_TRIES = 2
MAX_HYPOTHESES = 3
MAX_PARTS = 12
ROW_TOLERANCE_V = 0.3
CURRENT_TOLERANCE = 0.20  # 20 %...
CURRENT_TOLERANCE_MA = 2.0  # ...or 2 mA
RECURSION_LIMIT = 8
E_SERIES = [100, 150, 220, 330, 470, 680, 1000, 1500, 2200, 3300, 4700, 6800, 10000]


class GuessedPart(BaseModel):
    """One part the reader thinks is on the breadboard."""
    kind: str = Field(description='"wire", "resistor", "led", "button" or "unknown".')
    rows: list[str] = Field(description='The 2 places its legs are in: usable rows like "T5", '
                                        '"PT+", "PT-", "PB+", "PB-" (the + and - lines), or "U1".."U9" '
                                        'for a row the board does not measure. An LED: long leg (+) '
                                        'first.')
    ohms: float | None = Field(default=None, description="A resistor's value (use the parts "
                                                         "tray's values when given).")
    color: str | None = Field(default=None, description='An LED: "red", "yellow", "green", '
                                                        '"blue" or "white".')
    closed: bool | None = Field(default=None, description="A push button: is it pressed now?")
    sure: bool = Field(default=False, description="True only when the measurements clearly "
                                                  "show this part.")


class CircuitGuess(BaseModel):
    """One hypothesis: the parts the reader thinks were built."""
    parts: list[GuessedPart] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list,
                             description="At most 3 short notes, e.g. what could not be seen.")


class CircuitReading(BaseModel):
    """The circuit reader's answer: up to 3 hypotheses, most likely first."""
    hypotheses: list[CircuitGuess] = Field(default_factory=list,
                                           description="1 to 3 different circuits that would "
                                                       "read like this, most likely first.")


READER_PROMPT = """\
You are the circuit reader. A middle school student built a circuit on a
breadboard that measures its own rows. There is no plan: work out what
was built from the measurements alone. You get, as JSON: the usable rows
(only these are measured), the voltage of every row that has something
driving it ("driven_rows"; every other usable row is floating: nothing
drives it, so it has no path to + or -), the current from the + line
("current_ma", null without a sensor), the supply voltage, the parts tray
(what the student says they used) and goal, and plain facts.

Reason like an electronics teacher:
- A row at the supply voltage is joined to + (directly, through wires, or
  through a part that carries no current). A row near 0 V is joined to -.
- An LED that is on drops about its color's forward voltage: red 1.9,
  yellow 2.0, green 2.1, blue or white 3.0 volts (a little more when more
  current flows). An LED that is off carries no current.
- A resistor carries (voltage drop / ohms). With no current, a resistor has
  no voltage across it.
- The + lines are "PT+" (top) and "PB+" (bottom), the - lines "PT-" and
  "PB-". An LED's long leg (+) is its first row.
- Parts in rows that are not usable cannot be seen: never place a part in
  a row that is not usable.
- Use the tray's parts first (their values and colors): a good answer
  uses every part in the tray. If you cannot tell what a part is, say
  kind "unknown" rather than invent one.
- A part can go straight to a + or - line: the lines are always there.
- A leg in a row the board does not measure: use "U1" (then "U2", ...).
- Every row with a voltage must be explained by your parts. Do not put
  parts in rows the board reads as empty: they would show there. The one
  exception is a single leg alone in a row (a leg in the wrong row, or a
  part whose other end goes into a row the board does not measure).
Several circuits can read exactly the same, above all when NO current
flows. Give up to 3 hypotheses, most likely first. When no current flows,
think of the common student mistakes before extra invisible wires: an LED
in backwards, a missing link to the + or - line, a leg in the wrong row,
a leg in a row the board does not measure, a push button not pressed.
If you are told what did not match your last answer, fix it.
Answer with CircuitReading.
"""

READER_DESCRIPTION = ("Circuit reader: works out which parts were built on which rows from "
                      "the measurements alone (free build), and returns a CircuitGuess.")


def reader_spec(session, model=None, middleware=None):
    """The reader as a deepagents SubAgent dict (like the diagnoser)."""
    spec = {"name": "circuit_reader", "description": READER_DESCRIPTION,
            "system_prompt": READER_PROMPT, "tools": [], "response_format": CircuitReading}
    if model is not None:
        spec["model"] = model
    if middleware:
        spec["middleware"] = list(middleware)
    return spec


def make_reader(session, model):
    """A function payload -> raw CircuitReading (a dict). Errors are left to
    the caller (read_and_check catches them)."""
    from langchain.agents import create_agent

    from agent.trace import TraceMiddleware
    spec = reader_spec(session, model, middleware=[TraceMiddleware(session, "reader")])
    agent = create_agent(model=spec["model"], tools=spec["tools"],
                         system_prompt=spec["system_prompt"],
                         response_format=spec["response_format"],
                         middleware=spec["middleware"])

    def run(payload):
        out = agent.invoke({"messages": [{"role": "user", "content": json.dumps(payload)}]},
                           {"recursion_limit": RECURSION_LIMIT})
        answer = out.get("structured_response")
        if hasattr(answer, "model_dump"):
            answer = answer.model_dump()
        return answer

    return run


# --- What the reader is given ---------------------------------------------------
def rows_spec(rows):
    """["T3", "T4", "T5", "B40"] -> "T3-T5,B40"."""
    out, run = [], []
    for r in rows + [None]:
        if run and r and r[0] == run[-1][0] and int(r[1:]) == int(run[-1][1:]) + 1:
            run.append(r)
            continue
        if run:
            out.append(run[0] if len(run) == 1 else f"{run[0]}-{run[-1][1:]}")
        run = [r] if r else []
    return ",".join(out)


def measured_of(summary):
    """What the board measured, for the physics check: {"rows": {row:
    volts, or None when floating}, "current_ma", "current_source", "supply_v"}."""
    floating = set(summary.get("floating", []))
    return {"rows": {r: (None if r in floating else v) for r, v in summary.get("rows", {}).items()
                     if is_row(r)},
            "current_ma": summary.get("current_ma"),
            "current_source": summary.get("current_source"),
            "supply_v": summary.get("supply_v")}


def reader_payload(measured, usable, tray=None, facts=None):
    """The reader's input: only what the board measured (never a netlist)."""
    driven = {r: round(v, 2) for r, v in measured["rows"].items() if v is not None}
    return {"usable_rows": rows_spec([r for r in usable if is_row(r)]),
            "driven_rows": driven,
            "floating": "Every other usable row is floating: nothing drives it.",
            "current_ma": None if measured.get("current_ma") is None
            else round(measured["current_ma"], 2),
            "current_source": measured.get("current_source"),
            "supply_v": round(measured.get("supply_v") or 0.0, 2),
            "tray": (tray or {}).get("parts", []) if tray else [],
            "tray_other": (tray or {}).get("other", "") if tray else "",
            "goal": (tray or {}).get("goal", "") if tray else "",
            "facts": [f["text"] for f in (facts or [])]}


# --- Cleaning and checking a guess ---------------------------------------------
def place_words(place):
    """"T5" -> "row 5"; "PB-" -> "the bottom - line"; "U1" -> "a row I cannot measure"."""
    if is_row(place):
        return row_label(place)
    return {"PT+": "the top + line", "PT-": "the top - line", "PB+": "the bottom + line",
            "PB-": "the bottom - line"}.get(place, "a row I cannot measure")


def part_words(part):
    """'220 ohm resistor: rows 5 and 10' (for the page and the tutor)."""
    k = part["kind"]
    if k == "resistor":
        o = part.get("ohms")
        name = (f"{o / 1000:g}k ohm resistor" if o and o >= 1000 else
                f"{o:g} ohm resistor" if o else "resistor")
    elif k == "led":
        name = f"{part.get('color') or 'red'} LED"
    elif k == "button":
        name = "push button"
    elif k == "unknown":
        name = "a part I cannot tell"
    else:
        name = "wire"
    where = [place_words(r) for r in part["rows"]]
    text = f"{name}: {where[0]} and {where[1]}"
    if k == "led" and is_row(part["rows"][0]):
        text += f" (long leg in {where[0]})"
    return text


def clean_guess(raw, usable):
    """A guess in one tidy form: {"parts": [...], "notes": [...], "dropped": n}.
    Parts with a place that is not a usable row or a + / - line are dropped."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    raw = raw if isinstance(raw, dict) else {}
    usable = set(usable)
    parts, dropped = [], 0
    for p in (raw.get("parts") or [])[:MAX_PARTS]:
        if hasattr(p, "model_dump"):
            p = p.model_dump()
        if not isinstance(p, dict):
            dropped += 1
            continue
        kind = str(p.get("kind", "")).lower()
        rows = [str(r).upper() for r in (p.get("rows") or [])]
        if kind not in KINDS or len(rows) != 2 or \
                any(not (r in RAILS or r in solver.UNSEEN or (is_row(r) and r in usable))
                    for r in rows):
            dropped += 1
            continue
        part = {"kind": kind, "rows": rows, "sure": bool(p.get("sure"))}
        if kind == "resistor":
            try:
                ohms = float(p.get("ohms"))
                if not ohms > 0 or math.isnan(ohms):
                    raise ValueError
                part["ohms"] = ohms
            except (TypeError, ValueError):
                part = dict(part, kind="unknown")  # a resistor with no value cannot be worked out
        if kind == "led":
            color = str(p.get("color") or "red").lower()
            part["color"] = color if color in LED_VF else "red"
        if kind == "button":
            part["closed"] = bool(p.get("closed"))
        part["words"] = part_words(part) + (" (not pressed)" if kind == "button" and
                                            not part["closed"] else "")
        parts.append(part)
    notes = [str(n)[:120] for n in (raw.get("notes") or [])[:3]]
    return {"parts": parts, "notes": notes, "dropped": dropped}


def _solver_parts(parts):
    """The guess for sim/solver.py ("unknown" parts are left out), and the
    index in the guess of each solver part."""
    out, index = [], []
    for i, p in enumerate(parts):
        if p["kind"] == "unknown":
            continue
        q = {"kind": p["kind"], "rows": list(p["rows"])}
        for key in ("ohms", "color", "closed"):
            if key in p:
                q[key] = p[key]
        out.append(q)
        index.append(i)
    return out, index


def physics_check(parts, measured):
    """Work the guessed circuit out with sim/solver.py and compare with what
    the board measured. Never raises. Returns {"score", "matched", "total",
    "passed", "mismatches", "parts_ok", "predicted"}."""
    try:
        sparts, index = _solver_parts(parts)
        sol = solver.solve(sparts) if sparts else {"rows": {}, "current_ma": 0.0,
                                                   "supply_v": solver.SUPPLY_V, "leds_on": {}}
    except Exception as e:  # a guess the solver cannot work out
        return {"score": 0.0, "matched": 0, "total": 1, "passed": False,
                "mismatches": [f"the guess cannot be worked out: {e}"],
                "parts_ok": [False] * len(parts), "predicted": None}
    pred = sol["rows"]
    rows_m = measured.get("rows", {})
    guessed_rows = {r for p in parts for r in p["rows"] if is_row(r)}
    check_rows = sorted({r for r, v in rows_m.items() if v is not None} |
                        (guessed_rows & set(rows_m)), key=lambda r: (r[0], int(r[1:])))
    # Every usable row counts: a row the board reads with a voltage must be
    # explained, and a row it reads empty must stay empty. (Rows nobody
    # touches that read empty agree by themselves, so they are not scored.)
    ok_rows, mismatches, error, unexplained = {}, [], 0.0, []
    for r in check_rows:
        m, p = rows_m.get(r), pred.get(r, "absent")
        if m is None:
            ok = p in ("absent", None)
            if not ok:
                mismatches.append(f"{r}: your guess drives it ({p:.1f} V), but the board reads it "
                                  "empty")
        elif p in ("absent", None):
            ok = False
            unexplained.append(r)
            mismatches.append(f"{r}: has {m:.1f} V, your guess leaves it empty")
        else:
            ok = abs(p - m) <= ROW_TOLERANCE_V
            error += abs(p - m)
            if not ok:
                mismatches.append(f"{r}: you predict {p:.1f} V, measured {m:.1f} V")
        ok_rows[r] = ok
    matched, total = sum(ok_rows.values()), len(ok_rows)
    current_ok = None
    cm = measured.get("current_ma")
    if measured.get("current_source") == "measured" and cm is not None:
        cp = sol["current_ma"]
        current_ok = abs(cp - cm) <= max(CURRENT_TOLERANCE_MA, CURRENT_TOLERANCE * abs(cm))
        error += abs(cp - cm) / max(1.0, abs(cm))
        total += 1
        matched += int(current_ok)
        if not current_ok:
            mismatches.append(f"current: you predict {cp:.1f} mA, measured {cm:.1f} mA")
    if not parts and any(v is not None for v in rows_m.values()):
        mismatches.insert(0, "your guess has no parts, but some rows are driven")
    total = max(total, 1)
    parts_ok = [p["kind"] != "unknown" and all(ok_rows.get(r, True) for r in p["rows"] if is_row(r))
                for p in parts]
    leds_on = {index[i]: on for i, on in sol.get("leds_on", {}).items()}
    # A row where two or more legs meet (a real joint between parts) that the
    # board reads empty: those parts would show, unless something blocks
    # them. A single leg alone in an empty row (a leg in the wrong row) is fine.
    legs = {}
    for p in parts:
        for r in p["rows"]:
            if is_row(r):
                legs[r] = legs.get(r, 0) + 1
    empty_joints = sorted(r for r, n in legs.items() if n >= 2 and r in rows_m
                          and rows_m[r] is None)
    return {"score": round(matched / total, 3), "matched": matched, "total": total,
            "explains_all": not unexplained, "unexplained": unexplained,
            "empty_joints": empty_joints,
            "passed": matched == total and bool(parts) and not mismatches,
            "mismatches": mismatches, "parts_ok": parts_ok, "current_ok": current_ok,
            "error": round(error, 4),
            "predicted": {"rows": pred, "current_ma": round(sol["current_ma"], 3),
                          "supply_v": sol["supply_v"], "leds_on": leds_on}}


def clean_reading(raw, usable):
    """The reader's hypotheses, tidied: a list of guesses (at most
    MAX_HYPOTHESES, no two the same). Takes {"hypotheses": [...]} or one
    guess {"parts": [...]}."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    raw = raw if isinstance(raw, dict) else {}
    found = raw.get("hypotheses")
    found = found if isinstance(found, list) else [raw]
    out, seen = [], set()
    for h in found[:MAX_HYPOTHESES]:
        guess = clean_guess(h, usable)
        key = json.dumps(sorted(json.dumps({k: v for k, v in p.items() if k not in
                                            ("sure", "words")}, sort_keys=True)
                                for p in guess["parts"]))
        if key not in seen:
            seen.add(key)
            out.append(guess)
    return out or [clean_guess({}, usable)]


def _tray_counts(tray_parts):
    """{"resistor:330": 1, "led:green": 1, "button": 1, "wire": 2}."""
    counts = {}
    for p in tray_parts or []:
        name, n = str(p.get("name", "")), int(p.get("count", 1) or 1)
        m = re.match(r"(\d+)(k?) ohm resistor", name)
        key = (f"resistor:{float(m.group(1)) * (1000 if m.group(2) else 1):g}" if m else
               f"led:{name.split()[0]}" if name.endswith("LED") else
               "button" if name == "push button" else "wire" if name == "wire" else None)
        if key:
            counts[key] = counts.get(key, 0) + n
    return counts


def _part_key(p):
    if p["kind"] == "resistor":
        return f"resistor:{p['ohms']:g}"
    if p["kind"] == "led":
        return f"led:{p.get('color', 'red')}"
    return p["kind"]


def problems_of(parts, predicted, supply=solver.SUPPLY_V):
    """The usual student mistakes a hypothesis contains (plain Python):
    an LED in backwards, a leg in a row the board does not measure (a
    missing link), a push button not pressed."""
    rows = (predicted or {}).get("rows", {})
    on = (predicted or {}).get("leds_on", {})

    def volts(place):
        return supply if place in ("PT+", "PB+") else 0.0 if place in ("PT-", "PB-") \
            else rows.get(place)

    out = []
    for i, p in enumerate(parts):
        unseen = [r for r in p["rows"] if r in solver.UNSEEN]
        if unseen and p["kind"] != "wire":
            seen = next((r for r in p["rows"] if r not in solver.UNSEEN), None)
            out.append({"kind": "missing_link", "part": i, "row": seen})
        elif p["kind"] == "led" and not on.get(i):
            va, vb = volts(p["rows"][0]), volts(p["rows"][1])
            if va is not None and vb is not None and vb - va > 0.5:
                out.append({"kind": "led_backwards", "part": i})
        elif p["kind"] == "button" and not p.get("closed"):
            out.append({"kind": "button_open", "part": i})
    return out


def rank(hypotheses, measured, tray_parts=None):
    """Hypotheses that pass the physics check first, then by how likely they
    are (plain Python): every tray part used (2 per unused part), no parts
    the tray does not list (1 each), and when no current flows a usual
    mistake beats a circuit with none (2), fewer wires (0.25 each), fewer
    unknown parts (0.5 each) and no joint between parts in a row the board
    reads empty (2 each). Those that explain every driven row always come
    first. The reader's own order breaks ties."""
    tray = _tray_counts(tray_parts)
    current, source = measured.get("current_ma"), measured.get("current_source")
    driven = [v for v in measured.get("rows", {}).values() if v is not None]
    supply = measured.get("supply_v") or solver.SUPPLY_V
    if source == "measured" and current is not None:
        no_current = current < 0.5 and bool(driven)
    else:
        no_current = bool(driven) and not any(0.1 < v < supply - 0.1 for v in driven)
    for order, h in enumerate(hypotheses):
        parts = h["guess"]["parts"]
        h["problems"] = problems_of(parts, h.get("predicted"), supply)
        have = {}
        for p in parts:
            if p["kind"] != "unknown":
                have[_part_key(p)] = have.get(_part_key(p), 0) + 1
        unknowns = sum(p["kind"] == "unknown" for p in parts)
        unused = sum(max(0, n - have.get(k, 0)) for k, n in tray.items() if k != "wire")
        unused = max(0, unused - unknowns)  # an unknown part may be one of them
        extra = sum(max(0, n - tray.get(k, 0)) for k, n in have.items()
                    if k != "wire") if tray else 0
        wires = sum(p["kind"] == "wire" for p in parts)
        empty_joints = len(h.get("empty_joints") or [])
        h["penalty"] = (2 * unused + extra + (2 if no_current and not h["problems"] else 0)
                        + 0.25 * wires + 0.5 * unknowns + 2 * empty_joints)
        h["order"] = order
    # Hypotheses that explain every driven row first, then those that pass
    # the whole check, then the preferences above.
    return sorted(hypotheses, key=lambda h: (not h.get("explains_all", True), not h["passed"],
                                             h["penalty"] if h["passed"] else 0,
                                             -h["score"], h["order"]))


def read_and_check(run, payload, measured, usable, trace, tray_parts=None):
    """Ask the reader for up to 3 hypotheses, check each with physics, and
    if none matches, tell the reader what did not match in its best one and
    let it try once more. Every hypothesis that matches is kept, ranked
    (rank()). Returns the top one as "guess", plus "hypotheses" (ranked):
    {"guess", "score", "matched", "total", "passed", "mismatches",
    "predicted", "hypotheses", "matching", "tries", "error"}. Never raises
    (a reply that was given up on still stops)."""
    checked_all, last_best, tries, error = [], None, 0, None
    for attempt in range(1, MAX_TRIES + 1):
        ask = payload
        if last_best is not None:
            ask = dict(payload, your_last_guess=[{k: v for k, v in p.items()
                                                  if k not in ("words", "ok")}
                                                 for p in last_best["guess"]["parts"]],
                       did_not_match=last_best["mismatches"][:8])
        trace.append({"who": "reader", "tool": "read", "args": {"try": attempt}})
        try:
            raw = run(ask)
        except RunAbandoned:
            raise
        except Exception as e:  # no answer: keep what we have
            error = f"{type(e).__name__}: {str(e)[:120]}"
            trace.append({"who": "reader", "tool": "error", "args": {"error": error}})
            break
        tries = attempt
        try:
            guesses = clean_reading(raw, usable)
            round_checked = []
            for guess in guesses:
                checked = physics_check(guess["parts"], measured)
                for p, ok in zip(guess["parts"], checked["parts_ok"]):
                    p["ok"] = ok
                round_checked.append(dict(checked, guess=guess))
        except Exception as e:  # never raise
            error = f"{type(e).__name__}: {str(e)[:120]}"
            break
        top = guesses[0]
        trace.append({"who": "reader", "tool": "answer",
                      "args": {"parts": len(top["parts"]), "sure": sum(p["sure"] for p in top["parts"]),
                               "hypotheses": len(guesses)}})
        passing = sum(c["passed"] for c in round_checked)
        best_round = max(round_checked, key=lambda c: c["score"])
        trace.append({"who": "physics_check", "tool": "check",
                      "args": {"matched": best_round["matched"], "total": best_round["total"],
                               "try": attempt, "hypotheses": len(guesses), "passing": passing}})
        checked_all += round_checked
        last_best = best_round
        if passing:
            break
    if not checked_all:
        return {"guess": None, "score": 0.0, "matched": 0, "total": 0, "passed": False,
                "mismatches": [], "predicted": None, "hypotheses": [], "matching": 0,
                "tries": tries, "error": error}
    try:
        ranked = rank(checked_all, measured, tray_parts)
    except Exception as e:  # never raise: keep the reader's order
        error = error or f"{type(e).__name__}: {str(e)[:120]}"
        ranked = sorted(checked_all, key=lambda c: -c["score"])
    matching = [h for h in ranked if h["passed"]]
    best = ranked[0]
    return {"guess": best["guess"], "score": best["score"], "matched": best["matched"],
            "total": best["total"], "passed": best["passed"], "mismatches": best["mismatches"],
            "predicted": best["predicted"], "current_ok": best.get("current_ok"),
            "explains_all": best.get("explains_all", True),
            "unexplained": best.get("unexplained", []),
            "problems": best.get("problems", []),
            "hypotheses": matching[:MAX_HYPOTHESES] or [best], "matching": len(matching),
            "tries": tries, "error": error}


# --- Offline: a plain-Python reader for one chain of parts ----------------------
def _tray_values(tray):
    ohms, colors = [], []
    for p in tray or []:
        name = str(p.get("name", ""))
        m = re.match(r"(\d+)(k?) ohm resistor", name)
        if m:
            ohms.append(float(m.group(1)) * (1000 if m.group(2) else 1))
        m = re.match(r"(red|yellow|green|blue|white) LED", name)
        if m:
            colors.append(m.group(1))
    return ohms, colors


def _snap(ohms, choices):
    return min(choices, key=lambda c: abs(math.log(c) - math.log(max(ohms, 1e-3))))


def heuristic_guess(payload):
    """Hypotheses for a single chain from + to - (the offline reader): the
    driven rows in order of voltage; between each two, the parts their
    voltage drop and the current could be. When no current flows, that
    includes the usual mistakes: an LED in backwards, a leg in a row the
    board does not measure (a missing link), a push button not pressed.
    Every combination is worked out with the solver; those that match the
    measurements are ranked like the AI reader's (rank()), best 3 kept."""
    rows = {r: v for r, v in (payload.get("driven_rows") or {}).items() if is_row(r)}
    supply = payload.get("supply_v") or solver.SUPPLY_V
    current = payload.get("current_ma")
    tray_parts = payload.get("tray") or []
    tray_ohms, tray_colors = _tray_values(tray_parts)
    tray_button = any(p.get("name") == "push button" for p in tray_parts)
    has_tray = bool(tray_parts)
    if not rows:
        return {"hypotheses": [{"parts": [], "notes": ["Nothing is plugged into a usable row "
                                                       "I can see."]}]}
    chain = sorted(rows, key=lambda r: (-rows[r], r))
    middle = [v for v in rows.values() if 0.1 < v < supply - 0.1]
    flows = (current > 0.02) if current is not None else bool(middle)
    amps = (current / 1000) if current is not None and current > 0.02 else None
    nodes = [f"P{chain[0][0]}+"] + chain + [f"P{chain[-1][0]}-"]
    volts = dict(rows, **{nodes[0]: supply, nodes[-1]: 0.0})
    colors = tray_colors or ([] if has_tray else list(LED_VF))
    options, unseen = [], iter(solver.UNSEEN)
    for a, b in zip(nodes, nodes[1:]):
        d = volts[a] - volts[b]
        here = []
        if flows:
            if d < 0.05:
                here.append({"kind": "wire", "rows": [a, b], "sure": True})
                if tray_button:
                    here.append({"kind": "button", "rows": [a, b], "closed": True, "sure": False})
            else:
                vf = d - (amps or 0.0) * solver.LED_ON_OHMS
                if colors:
                    color = min(colors, key=lambda c: abs(LED_VF[c] - vf))
                    if abs(LED_VF[color] - vf) < 0.2:
                        here.append({"kind": "led", "rows": [a, b], "color": color, "sure": True})
                ohms = d / amps if amps else (tray_ohms[0] if tray_ohms else 220)
                choice = _snap(ohms, tray_ohms or E_SERIES)
                here.append({"kind": "resistor", "rows": [a, b], "ohms": choice,
                             "sure": bool(amps) and abs(choice - ohms) / choice < 0.15})
        else:
            if d < 0.05:
                here.append({"kind": "wire", "rows": [a, b], "sure": False})
                here.append({"kind": "resistor", "rows": [a, b],
                             "ohms": tray_ohms[0] if tray_ohms else 1000, "sure": False})
                if tray_button:
                    here.append({"kind": "button", "rows": [a, b], "closed": True, "sure": False})
            else:
                color = tray_colors[0] if tray_colors else "red"
                if tray_colors or not has_tray:
                    # In backwards, or its leg missed the next row (a row the board
                    # does not measure).
                    here.append({"kind": "led", "rows": [b, a], "color": color, "sure": False})
                    here.append({"kind": "led", "rows": [a, next(unseen, "U9")], "color": color,
                                 "sure": False})
                if tray_button:
                    here.append({"kind": "button", "rows": [a, b], "closed": False, "sure": False})
                if not here:
                    here.append({"kind": "unknown", "rows": [a, b], "sure": False})
        options.append(here)
    measured = {"rows": dict(rows), "current_ma": current,
                "current_source": payload.get("current_source"), "supply_v": supply}
    checked = []
    for combo in itertools.islice(itertools.product(*options), 512):
        parts = [dict(p) for p in combo]
        result = physics_check(parts, measured)
        leds = sum(p["kind"] == "led" for p in parts)
        # Fewer LEDs than needed for no reason, a resistor: the usual circuit.
        result["guess"] = {"parts": parts, "notes": []}
        result["usual"] = (-max(0, leds - 1), any(p["kind"] == "resistor" for p in parts),
                           -result["error"])
        checked.append(result)
    passing = [c for c in checked if c["passed"]] or \
        [max(checked, key=lambda c: (c["score"], c["usual"]))]
    # The reader's own order: the usual circuit first; then rank() decides.
    passing.sort(key=lambda c: c["usual"], reverse=True)
    ranked = rank(passing, measured, tray_parts)
    out = []
    for h in ranked:
        if len(out) == MAX_HYPOTHESES:
            break
        out.append({"parts": h["guess"]["parts"], "notes": []})
    return {"hypotheses": out}


class HeuristicReaderModel(BaseChatModel):
    """The offline circuit reader: answers CircuitReading from heuristic_guess.
    seen: every payload it was given (tests check no netlist is in it)."""
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "heuristic-reader"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "{}")
        self.seen.append(text)
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
        guess = heuristic_guess(payload)
        msg = AIMessage(content="", tool_calls=[{"name": "CircuitReading", "args": guess,
                                                 "id": "guess"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])
