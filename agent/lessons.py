"""Load lessons/*.json and compare readings against them.

Plain Python, no AI here. The agent's tools use this so that the
decision "is this circuit right, and if not, which mistake is it?" is
the same every time, whichever model is running.
"""

import json
import statistics
from pathlib import Path

LESSONS_DIR = Path(__file__).resolve().parent.parent / "lessons"

# Any current above this is unsafe for every lesson, so power is cut
# at once, without waiting for the AI. Lesson 1 normally draws 13.6 mA.
SAFETY_MAX_MA = 30.0


def load_lessons(folder=LESSONS_DIR):
    """Return {lesson_id: lesson_dict} for every lessons/lesson*.json."""
    lessons = {}
    for path in sorted(Path(folder).glob("lesson*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        lessons[data["id"]] = data
    return lessons


def summarize(readings):
    """Average a list of readings into one set of numbers."""
    probe = [r["voltages"]["probe1"] for r in readings]
    return {
        "current_ma": round(statistics.mean(r["current_ma"] for r in readings), 3),
        "supply_v": round(statistics.mean(r["supply_v"] for r in readings), 3),
        "probe1_v": round(statistics.mean(probe), 3),
        "probe1_v_spread": round(max(probe) - min(probe), 3),
        "power_on": all(r["power_on"] for r in readings),
    }


def in_range(value, rule):
    """rule is {"min": a, "max": b} (either may be left out) or [a, b]."""
    if isinstance(rule, list):
        rule = {"min": rule[0], "max": rule[1]}
    return rule.get("min", float("-inf")) <= value <= rule.get("max", float("inf"))


def matches(summary, detect):
    return all(in_range(summary[field], rule) for field, rule in detect.items())


def check(summary, lesson):
    """Decide what a summary means for a lesson.

    Returns a dict with "status" set to one of:
      "power_off" : power is cut, nothing to measure
      "mistake"   : matches a known mistake (see "mistake_id")
      "ok"        : the circuit is correct
      "unknown"   : wrong, but not a mistake we know about
    """
    if not summary["power_on"]:
        return {"status": "power_off"}
    for m in lesson["mistakes"]:
        if matches(summary, m["detect"]):
            result = {"status": "mistake", "mistake_id": m["id"], "action": m["action"]}
            if m.get("show_row") and lesson.get("probe1_row") is not None:
                result["row"] = lesson["probe1_row"]
            return result
    if matches(summary, lesson["accept"]):
        return {"status": "ok"}
    return {"status": "unknown"}
