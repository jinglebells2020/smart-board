"""The detective: a deepagents subagent for mistakes the lesson does not list.

The tutor calls it through deepagents' task tool, only when
lesson_check.status is "unknown". It looks at the case with one
read-only tool (the row voltages next to the lesson layout: which part
touches which row) and answers with at most two likely causes, each a
question for the student. It never says a fix for certain, because the
board cannot know.

Plain Python, not the AI, guards the answer: it is a structured
response (DetectiveAnswer). deepagents rejects an answer with more than
two questions, a sentence that is not a question, or a word that sounds
certain, and the model has to try again.
"""

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field, field_validator

from protocol import row_label

# Words that state a cause or a fix as certain. Not allowed in an answer.
CERTAIN = ["definitely", "certainly", "for sure", "surely", "obviously", "100%",
           "the problem is", "the fix is", "you must", "must be", "always", "guaranteed"]


class DetectiveAnswer(BaseModel):
    """The detective's answer: at most two likely causes of the mistake,
    each asked as a short question a middle school student can check on
    the breadboard."""

    questions: list[str] = Field(
        max_length=2,
        description="At most 2 questions, most likely first. Each one names a part and a "
                    "row and ends with '?'. Never say a fix is certain.")

    @field_validator("questions")
    @classmethod
    def _only_careful_questions(cls, questions):
        for q in questions:
            q = q.strip()
            if not q.endswith("?"):
                raise ValueError(f"every item must be a question ending with '?': {q!r}")
            if len(q) > 200:
                raise ValueError(f"keep each question under 200 characters: {q[:60]!r}")
            bad = [w for w in CERTAIN if re.search(rf"\b{re.escape(w)}\b", q.lower())]
            if bad:
                raise ValueError(f"do not sound certain ({bad[0]!r}): only ask what to look at")
        return [q.strip() for q in questions]


DETECTIVE_PROMPT = """\
You are the circuit detective. A middle school student (age 11 to 14)
built a circuit that is wrong in a way the lesson does not list. Find
the most likely causes.

1. Call look_at_the_case. It gives the lesson layout (which part goes in
   which rows), the expected and measured voltage of each row, the clue,
   and "leads": causes worked out from the numbers, most likely first.
2. Think about which parts touch the rows whose numbers are off.
3. Answer with DetectiveAnswer: at most TWO questions, most likely first.
   Each question names one part and one row the student can look at,
   for example "Is the LED's long leg pushed all the way into row 10?".
   Plain words a 6th grader knows. Say "row 10", never "T10".
   Never say a cause or a fix is certain: we only know the numbers.
If look_at_the_case answers with an error, answer with no questions.
"""

DETECTIVE_DESCRIPTION = (
    "Circuit detective. Use ONLY when lesson_check.status is \"unknown\" (a mistake the "
    "lesson does not list). It compares the row voltages with the lesson layout and "
    "returns at most two likely causes, as questions for the student. Describe the case "
    "in one sentence (the lesson and the clue).")


def _parts_in(layout, row):
    """[(part, leg)] for every leg the layout puts in this row."""
    return [(p["part"], leg["leg"]) for p in layout for leg in p["legs"] if leg["row"] == row]


def _the(part):
    """ "Top 10k resistor" -> "the top 10k resistor" (but "LED" stays "LED")."""
    if part[:1].isupper() and part[1:2].islower():
        part = part[0].lower() + part[1:]
    return part if part.startswith("the ") else f"the {part}"


def leads(lesson, clue):
    """Likely causes from the clue and the layout, most likely first.
    Each is a question. This is the detective's evidence, worked out in
    plain Python; the detective may reword it but must stay this careful."""
    layout = lesson.get("layout", [])
    resistors = [p["part"] for p in layout if "resistor" in p["part"].lower()]
    out = []

    def add(q):
        if q not in out:
            out.append(q)

    for d in clue.get("details", []):
        row = d.get("row")
        where = row_label(row) if row else None
        here = _parts_in(layout, row) if row else []
        direction = d["direction"]
        if direction == "empty":
            for part, leg in here:
                add(f"Is {_the(part)}'s {leg} really pushed into {where}, or did it slip "
                    "into the row next to it?")
        elif direction == "almost all":
            later = [(p, l) for p, l in here if not p.lower().endswith("wire")] or here
            for part, leg in later:
                add(f"Is {_the(part)} pushed in firmly, with its {leg} in {where}, so "
                    "current can get past it?")
        elif direction == "almost none":
            add(f"Is something joining {where} straight to the - line (a wire or a leg "
                "in the wrong row)?")
            for part, leg in here:
                add(f"Does {_the(part)} really reach {where} with its {leg}?")
        elif direction in ("lower", "higher"):
            near = [p for p, _ in here if "resistor" in p.lower()] or resistors
            for part in near:
                add(f"Could {_the(part)} be a different value than the plan says "
                    "(compare its color bands with the lesson)?")
            for part, leg in here:
                add(f"Is {_the(part)}'s {leg} in {where} and pushed in firmly?")
        elif d["number"] == "current":
            if "lower" in direction or "none" in direction:
                add("Is every part pushed in firmly, so the loop is closed all the way "
                    "around?")
            for part in resistors:
                add(f"Could {_the(part)} be a different value than the plan says "
                    "(compare its color bands with the lesson)?")
        elif d["number"] == "power voltage":
            add("Is the power supply plugged in well, and is it set to 5 volts?")
    if not out:
        add("Could one part be in the row next to where the plan puts it (compare "
            "each part with the lesson layout, one at a time)?")
    return out


def case_file(session):
    """What look_at_the_case returns: the last check, next to the layout."""
    last = session.last_check
    if last is None or last[1].get("status") != "unknown":
        return {"error": "The detective only works on a mistake the lesson does not list "
                         "(lesson_check status \"unknown\"). Check the circuit first."}
    summary, result = last
    lesson = session.lesson
    expected = lesson.get("expected", {}).get("rows", {})
    rows = {}
    for name in lesson.get("rows", []):
        rows[row_label(name)] = {
            "name": name,
            "expected_v": expected.get(name),
            "measured_v": None if name in summary["floating"] else summary["rows"].get(name),
            "empty": name in summary["floating"],
            "parts_here": [f"{p} ({l})" for p, l in _parts_in(lesson.get("layout", []), name)],
        }
    return {
        "lesson": {"id": session.lesson_id, "title": lesson["title"],
                   "circuit": lesson["circuit"]},
        "layout": [{"part": p["part"],
                    "legs": [f"{l['leg']} in {row_label(l['row'])}" for l in p["legs"]]}
                   for p in lesson.get("layout", [])],
        "rows": rows,
        "current_ma": summary.get("current_ma"),
        "current_source": summary.get("current_source"),
        "clue": result["clue"]["text"],
        "leads": leads(lesson, result["clue"]),
    }


def detective_spec(session, model=None):
    """The deepagents SubAgent for create_deep_agent(subagents=[...])."""

    def look_at_the_case() -> str:
        """The lesson layout, each row's expected and measured voltage, the
        clue, and leads (likely causes as questions, most likely first).
        Read-only: it does not measure or change the board."""
        return json.dumps(case_file(session))

    spec = {"name": "detective", "description": DETECTIVE_DESCRIPTION,
            "system_prompt": DETECTIVE_PROMPT, "tools": [look_at_the_case],
            "response_format": DetectiveAnswer}
    if model is not None:
        spec["model"] = model
    return spec


class ScriptedDetectiveModel(BaseChatModel):
    """Offline stand-in for Claude as the detective: looks at the case,
    then answers with the first two leads."""

    @property
    def _llm_type(self):
        return "scripted-detective"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        seen = [m for m in messages[start + 1:] if isinstance(m, ToolMessage)]
        case = next((json.loads(m.content) for m in seen if m.name == "look_at_the_case"), None)
        rejected = any(m.name == "DetectiveAnswer" for m in seen)
        if rejected:  # the answer did not pass the checks: say nothing rather than guess
            call = {"name": "DetectiveAnswer", "args": {"questions": []},
                    "id": f"det-{len(messages)}"}
        elif case is None:
            call = {"name": "look_at_the_case", "args": {}, "id": f"det-{len(messages)}"}
        else:
            call = {"name": "DetectiveAnswer", "args": {"questions": case.get("leads", [])[:2]},
                    "id": f"det-{len(messages)}"}
        msg = AIMessage(content="", tool_calls=[call])
        msg.id = f"detective-{len(messages)}"
        return ChatResult(generations=[ChatGeneration(message=msg)])
