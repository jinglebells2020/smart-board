"""Free build, phase 2: the circuit reader. Zip works out ANY circuit from
the measurements, and plain Python checks the answer against physics.

1. The "circuit_reader" subagent (Claude) gets what the board measured:
   the usable rows, every row's voltage (floating rows are left out: no
   path), the current, the supply, the parts tray and goal, and the plain
   facts. It answers with a CircuitGuess: the parts it thinks are there,
   on which rows. A plain pydantic schema (response_format=CircuitGuess),
   not ToolStrategy, like the diagnoser.
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
                                        'or "PT+", "PT-", "PB+", "PB-". An LED: long leg (+) first.')
    ohms: float | None = Field(default=None, description="A resistor's value (use the parts "
                                                         "tray's values when given).")
    color: str | None = Field(default=None, description='An LED: "red", "yellow", "green", '
                                                        '"blue" or "white".')
    closed: bool | None = Field(default=None, description="A push button: is it pressed now?")
    sure: bool = Field(default=False, description="True only when the measurements clearly "
                                                  "show this part.")


class CircuitGuess(BaseModel):
    """The circuit reader's answer: the parts it thinks were built."""
    parts: list[GuessedPart] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list,
                             description="At most 3 short notes, e.g. what could not be seen.")


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
- Use the tray's parts first (their values and colors). If you cannot
  tell what a part is, say kind "unknown" rather than invent one.
If you are told what did not match your last guess, fix the guess.
Answer with CircuitGuess.
"""

READER_DESCRIPTION = ("Circuit reader: works out which parts were built on which rows from "
                      "the measurements alone (free build), and returns a CircuitGuess.")


def reader_spec(session, model=None, middleware=None):
    """The reader as a deepagents SubAgent dict (like the diagnoser)."""
    spec = {"name": "circuit_reader", "description": READER_DESCRIPTION,
            "system_prompt": READER_PROMPT, "tools": [], "response_format": CircuitGuess}
    if model is not None:
        spec["model"] = model
    if middleware:
        spec["middleware"] = list(middleware)
    return spec


def make_reader(session, model):
    """A function payload -> raw CircuitGuess (a dict). Errors are left to
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
    where = [row_label(r) if is_row(r) else
             {"PT+": "the top + line", "PT-": "the top - line", "PB+": "the bottom + line",
              "PB-": "the bottom - line"}[r] for r in part["rows"]]
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
                any(not (r in RAILS or (is_row(r) and r in usable)) for r in rows):
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
        part["words"] = part_words(part)
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
    ok_rows, mismatches, error = {}, [], 0.0
    for r in check_rows:
        m, p = rows_m.get(r), pred.get(r, "absent")
        if m is None:
            ok = p in ("absent", None)
            if not ok:
                mismatches.append(f"{r}: you predict {p:.1f} V, but nothing drives it (it is empty)")
        elif p == "absent":
            ok = False
            mismatches.append(f"{r}: measured {m:.1f} V, but your guess has no part there")
        elif p is None:
            ok = False
            mismatches.append(f"{r}: measured {m:.1f} V, but in your guess nothing drives it")
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
    return {"score": round(matched / total, 3), "matched": matched, "total": total,
            "passed": matched == total and bool(parts) and not mismatches,
            "mismatches": mismatches, "parts_ok": parts_ok, "current_ok": current_ok,
            "error": round(error, 4),
            "predicted": {"rows": pred, "current_ma": round(sol["current_ma"], 3),
                          "supply_v": sol["supply_v"], "leds_on": leds_on}}


def read_and_check(run, payload, measured, usable, trace):
    """Ask the reader, check the guess with physics, and if it does not
    match, tell the reader what did not match and let it try once more.
    Returns the best guess: {"guess", "score", "matched", "total", "passed",
    "mismatches", "predicted", "tries", "error"}. Never raises (a reply that
    was given up on still stops)."""
    best, last, tries, error = None, None, 0, None
    for attempt in range(1, MAX_TRIES + 1):
        ask = payload
        if last is not None:
            ask = dict(payload, your_last_guess=[{k: v for k, v in p.items() if k != "words"}
                                                 for p in last["guess"]["parts"]],
                       did_not_match=last["mismatches"][:8])
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
            guess = clean_guess(raw, usable)
            checked = physics_check(guess["parts"], measured)
        except Exception as e:  # never raise
            error = f"{type(e).__name__}: {str(e)[:120]}"
            break
        for p, ok in zip(guess["parts"], checked["parts_ok"]):
            p["ok"] = ok
        trace.append({"who": "reader", "tool": "answer",
                      "args": {"parts": len(guess["parts"]),
                               "sure": sum(p["sure"] for p in guess["parts"])}})
        trace.append({"who": "physics_check", "tool": "check",
                      "args": {"matched": checked["matched"], "total": checked["total"],
                               "try": attempt}})
        last = dict(checked, guess=guess)
        if best is None or checked["score"] > best["score"]:
            best = last
        if checked["passed"]:
            break
    if best is None:
        return {"guess": None, "score": 0.0, "matched": 0, "total": 0, "passed": False,
                "mismatches": [], "predicted": None, "tries": tries, "error": error}
    return {"guess": best["guess"], "score": best["score"], "matched": best["matched"],
            "total": best["total"], "passed": best["passed"], "mismatches": best["mismatches"],
            "predicted": best["predicted"], "current_ok": best.get("current_ok"),
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
    """A guess for a single chain from + to - (the offline reader): the
    driven rows in order of voltage; between each two, the parts their
    voltage drop and the current could be (a wire, an LED, a resistor).
    Every combination is worked out with the solver, and the one that
    matches the measurements best is the answer."""
    rows = {r: v for r, v in (payload.get("driven_rows") or {}).items() if is_row(r)}
    supply = payload.get("supply_v") or solver.SUPPLY_V
    current = payload.get("current_ma")
    tray_ohms, tray_colors = _tray_values(payload.get("tray"))
    has_tray = bool(payload.get("tray"))
    if not rows:
        return {"parts": [], "notes": ["Nothing is plugged into a usable row I can see."]}
    chain = sorted(rows, key=lambda r: (-rows[r], r))
    middle = [v for v in rows.values() if 0.1 < v < supply - 0.1]
    flows = (current > 0.02) if current is not None else bool(middle)
    amps = (current / 1000) if current is not None and current > 0.02 else None
    nodes = [f"P{chain[0][0]}+"] + chain + [f"P{chain[-1][0]}-"]
    volts = dict(rows, **{nodes[0]: supply, nodes[-1]: 0.0})
    colors = tray_colors or ([] if has_tray else list(LED_VF))
    options, notes = [], []
    for a, b in zip(nodes, nodes[1:]):
        d = volts[a] - volts[b]
        here = []
        if flows:
            if d < 0.05:
                here.append({"kind": "wire", "rows": [a, b], "sure": True})
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
                here.append({"kind": "resistor", "rows": [a, b],
                             "ohms": tray_ohms[0] if tray_ohms else 1000, "sure": False})
                here.append({"kind": "wire", "rows": [a, b], "sure": False})
            elif tray_colors or not has_tray:
                color = tray_colors[0] if tray_colors else "red"
                here.append({"kind": "led", "rows": [b, a], "color": color, "sure": False})
                notes.append(f"No current flows: the LED between {a} and {b} may be backwards.")
            else:
                here.append({"kind": "unknown", "rows": [a, b], "sure": False})
        options.append(here)
    measured = {"rows": dict(rows), "current_ma": current,
                "current_source": payload.get("current_source")}
    best, best_key = None, None
    for combo in itertools.islice(itertools.product(*options), 256):
        parts = [dict(p) for p in combo]
        checked = physics_check(parts, measured)
        # The best match; when two match equally well, the more usual circuit:
        # one LED with a resistor, rather than several LEDs and no resistor.
        leds = sum(p["kind"] == "led" for p in parts)
        # Then the closest fit to the measured numbers.
        key = (checked["score"], -max(0, leds - 1), any(p["kind"] == "resistor" for p in parts),
               -checked["error"])
        if best_key is None or key > best_key:
            best, best_key = parts, key
    return {"parts": best, "notes": notes[:3]}


class HeuristicReaderModel(BaseChatModel):
    """The offline circuit reader: answers CircuitGuess from heuristic_guess.
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
        msg = AIMessage(content="", tool_calls=[{"name": "CircuitGuess", "args": guess,
                                                 "id": "guess"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])
