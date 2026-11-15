"""How well does Zip read circuits it did not plan? (free build, phase 2)

Builds N random judge circuits on the fake board (sim/random_circuit.py,
seeded, so every run is the same), runs one free build check on each
(physics facts -> circuit reader -> physics check -> feedback), and
reports:

- reads:    % of circuits where the reader's guess passes the physics
            check (every row within 0.3 V, the current within 20 % or 2 mA);
- parts:    % of circuits where the guessed parts (not counting wires)
            are exactly the real ones: the same kinds, values and colours.
            Passing the physics check does not prove this: two different
            circuits can read the same on every row;
- problems: % of circuits where the main feedback item is the right kind
            for the circuit's mistake (or no problem, for a right circuit),
            or the safety cut caught it.

The reader is Claude when ANTHROPIC_API_KEY is set, otherwise the
plain-Python offline reader. The true netlist is only used here to score
the answer: the reader never sees it.

    python tools/eval_free_build.py
    python tools/eval_free_build.py --n 40 --seed 100 --tray
    python tools/eval_free_build.py --usable "T1-T30"
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.boardlink import BoardLink  # noqa: E402
from agent.lessons import FREE_ID  # noqa: E402
from agent.reader import HeuristicReaderModel, make_reader  # noqa: E402
from agent.session import Session  # noqa: E402
from agent.tray import TRAY_PARTS  # noqa: E402
from sim.fake_board import FakeBoard  # noqa: E402
from sim.random_circuit import random_circuit  # noqa: E402

# The main feedback item kinds that count as "found" for each mistake. The
# board cannot tell some mistakes apart (an LED in backwards and a leg in
# the wrong row both stop the current), so related kinds count too.
FOUND = {
    None: {None, "led_on", "working", "current_ok", "goal_met"},
    "led_backwards": {"led_backwards"},
    "missing_link": {"open_path", "led_backwards", "does_nothing", "led_off", "led_too_little"},
    "short": {"short", "short_supply", "too_much_current", "does_nothing", "led_off",
              "led_too_little"},
    "no_resistor": {"too_much_current", "short_supply"},
}


def tray_of(parts):
    """What a student would tap in the parts tray for these parts."""
    counts = Counter()
    for p in parts:
        if p["kind"] == "wire":
            counts["wire"] += 1
        elif p["kind"] == "resistor":
            o = p["ohms"]
            counts[f"{o / 1000:g}k ohm resistor" if o >= 1000 else f"{o:g} ohm resistor"] += 1
        elif p["kind"] == "led":
            counts[f"{p['color']} LED"] += 1
        elif p["kind"] == "button":
            counts["push button"] += 1
    return {"parts": [{"name": n, "count": counts[n]} for n in TRAY_PARTS if n in counts],
            "other": "", "goal": ""}


def _parts_key(parts):
    """The parts that matter (no wires), as a sorted list, for comparing."""
    return sorted((p["kind"], round(p.get("ohms") or 0), p.get("color") or "")
                  for p in parts if p["kind"] != "wire")


def make_model():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain.chat_models import init_chat_model

        from agent.build import CALL_TIMEOUT, DEFAULT_MODEL
        name = os.environ.get("SMART_BOARD_MODEL", DEFAULT_MODEL)
        return init_chat_model(f"anthropic:{name}", timeout=CALL_TIMEOUT, max_retries=1), name
    return HeuristicReaderModel(), "offline reader (plain Python)"


def evaluate(n=20, seed=0, usable="T3-T8,T20-T24,B40-B45", tray=False, model=None, quiet=False):
    model, name = (model, "given model") if model is not None else make_model()
    rows = Counter()
    for i in range(n):
        board = FakeBoard(FREE_ID, "correct", seed=seed + i)
        link = BoardLink(board, usable)
        session = Session(link, lesson=FREE_ID, free_build=True)
        session.reader = make_reader(session, model)
        made = random_circuit(seed + i, 2 + i % 4, link.usable_rows)
        board.set_netlist(made["parts"])
        board.send("HOLD on")  # push buttons follow HOLD: the student holds them pressed
        if tray:
            session.tray = tray_of(made["parts"])
        _, result, cut = session.measure()
        main = (result.get("main_problem") or {}).get("kind")
        if main is None and result.get("feedback"):
            main = next((f["kind"] for f in result["feedback"] if f["level"] == "good"), None)
        found = (cut and made["mistake"] in ("no_resistor", "short")) or main in FOUND[made["mistake"]]
        if made["mistake"] is None and (cut or (result.get("main_problem") or {}).get("level")
                                        == "problem"):
            found = False
        passed = result.get("guess_score") == 1.0
        guessed = (result.get("circuit_guess") or {}).get("parts") or []
        same = _parts_key(guessed) == _parts_key(made["parts"])
        key = made["mistake"] or "none"
        rows[(key, "n")] += 1
        rows[(key, "read")] += int(passed)
        rows[(key, "found")] += int(bool(found))
        rows[(key, "parts")] += int(same)
        rows[(key, "cut")] += int(bool(cut))
        if not quiet:
            print(f"{seed + i:4d} {len(made['parts'])} parts  mistake={key:14s} "
                  f"cut={'yes' if cut else 'no ':3s} guess={result.get('guess_matched', '-'):8s} "
                  f"main={main}")
    total = sum(v for (k, w), v in rows.items() if w == "n")
    read = sum(v for (k, w), v in rows.items() if w == "read")
    found = sum(v for (k, w), v in rows.items() if w == "found")
    same = sum(v for (k, w), v in rows.items() if w == "parts")
    report = {"reader": name, "n": total, "read_pct": round(100 * read / max(total, 1)),
              "found_pct": round(100 * found / max(total, 1)),
              "parts_pct": round(100 * same / max(total, 1)), "by_mistake": {}}
    for key in sorted({k for k, _ in rows}):
        report["by_mistake"][key] = {"n": rows[(key, "n")], "read": rows[(key, "read")],
                                     "found": rows[(key, "found")], "cut": rows[(key, "cut")],
                                     "parts": rows[(key, "parts")]}
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Free build: how well Zip reads random circuits.")
    p.add_argument("--n", type=int, default=20, help="how many circuits (default 20)")
    p.add_argument("--seed", type=int, default=0, help="first seed (default 0)")
    p.add_argument("--usable", default="T3-T8,T20-T24,B40-B45", help="usable rows")
    p.add_argument("--tray", action="store_true",
                   help="fill the parts tray with the parts used (like a student would)")
    args = p.parse_args(argv)
    report = evaluate(args.n, args.seed, args.usable, args.tray)
    print()
    print(f"Reader: {report['reader']}. Circuits: {report['n']}.")
    print(f"Guess passes the physics check: {report['read_pct']}%")
    print(f"Guessed parts exactly right (not counting wires): {report['parts_pct']}%")
    print(f"Main problem found (or safety cut): {report['found_pct']}%")
    print("By mistake: " + ", ".join(
        f"{k} {v['found']}/{v['n']} found, {v['read']}/{v['n']} read"
        for k, v in report["by_mistake"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
