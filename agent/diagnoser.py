"""The diagnoser: Zip can diagnose ANY mistake, not only the ones the
lesson files list.

How it works, in three steps:
1. Plain Python works out physics facts from the measurements
   (agent/physics.py), each with an id: F1, F2, ...
2. Claude (the "diagnoser" subagent) reasons from those facts, like an
   electronics teacher, and answers with a Diagnosis: a status, up to 3
   causes (each citing the fact ids behind it) and, when the board cannot
   tell two causes apart, a question for the student.
3. Plain Python checks the answer is grounded (validate_diagnosis): causes
   with no evidence, made-up fact ids or rows that do not exist are
   dropped, and a question must end with "?". It never raises.

The diagnoser never decides safety or passing. Safety cuts and asking
before power comes back on are plain Python (agent/session.py), and they
happen before the diagnoser is asked. The lesson's own accept rules still
decide PASS: if they fail, the diagnosis can never be "ok" (it becomes
"unsure"); if they pass, it is "ok" (a cause can only stay as a
"possible" note).

The answer is a plain pydantic schema (response_format=Diagnosis), not
ToolStrategy: ToolStrategy forces a tool call, which claude-opus-5-5
rejects. Python runs the subagent on every check when Claude is available
(agent/session.py); it is not offered to the tutor through the task tool,
so it is never asked twice for the same check.
"""

import json

from pydantic import BaseModel, Field

from protocol import is_node, is_row

STATUSES = ("ok", "mistake", "unsure")
SURE = ("likely", "possible")
MAX_CAUSES = 3
MAX_CAUSE_CHARS = 120
RECURSION_LIMIT = 8  # one answer; a model that wanders is stopped early


class Cause(BaseModel):
    """One possible cause of what the board measured."""
    cause: str = Field(description="What is likely wrong, in plain words a 6th grader knows, "
                                   "at most 120 characters. Name rows as 'row 10'.")
    rows: list[str] = Field(default_factory=list,
                            description="The board rows this cause is about, e.g. ['T10', 'T12'].")
    evidence: list[str] = Field(default_factory=list,
                                description="The ids of the facts that show it, e.g. ['F3', 'F9'].")
    how_sure: str = Field(default="possible", description='"likely" or "possible".')


class Diagnosis(BaseModel):
    """The diagnoser's answer for one check."""
    status: str = Field(description='"ok" (the circuit works), "mistake" (something is wrong '
                                    'and the facts show what), or "unsure" (the facts cannot '
                                    'tell the causes apart).')
    causes: list[Cause] = Field(default_factory=list, description="At most 3, most likely first.")
    question: str | None = Field(default=None,
                                 description="Only when two causes look the same to the board: "
                                             "one question for the student, ending with '?'.")


DIAGNOSER_PROMPT = """\
You are the circuit diagnoser. A middle school student (age 11 to 14)
built a circuit on a breadboard that measures its own rows. You get, as
JSON: the lesson plan (which part goes in which rows, and the expected
voltage of each row), the facts that plain Python worked out from the
measurements (each with an id: F1, F2, ...), the lesson's own rule match
(a known mistake id, or null) as a hint, and the student's last mistakes.

Reason like an electronics teacher, from the facts only:
- A resistor with no voltage across it carries no current. An LED that
  is lit has about 2 volts across it. Two rows that should differ but read
  the same are joined together, or no current flows between them.
- Find what is wrong and where. Say which rows, using the row names from
  the plan (for example "T10"); in "cause" write "row 10".
- Every cause must cite the fact ids that show it (evidence). Never invent
  a measurement, a fact or a row.
- At most 3 causes, most likely first. "likely" when the facts point
  clearly at it; "possible" when it fits but something else could too.
- If the facts cannot tell two causes apart (for example an LED in
  backwards and an LED that is missing look the same to the board), say
  status "unsure" and ask ONE question the student can answer by looking.
- If every fact looks right, status "ok" with no causes.
- Never say the fix, only what is wrong: the student should work it out.
Answer with Diagnosis.
"""

DIAGNOSER_DESCRIPTION = (
    "Circuit diagnoser: reasons from the physics facts of one check (agent/physics.py) "
    "and returns a Diagnosis whose causes each cite the fact ids behind them.")


def diagnoser_spec(session, model=None, middleware=None):
    """The diagnoser as a deepagents SubAgent dict (the same shape as the
    detective's). It needs no tools: everything it may use is in the
    message (the facts)."""
    spec = {"name": "diagnoser", "description": DIAGNOSER_DESCRIPTION,
            "system_prompt": DIAGNOSER_PROMPT, "tools": [], "response_format": Diagnosis}
    if model is not None:
        spec["model"] = model
    if middleware:
        spec["middleware"] = list(middleware)
    return spec


def make_diagnoser(session, model):
    """A function payload -> raw Diagnosis (a dict), running the diagnoser
    subagent once. agent/session.py calls it on every check when Claude is
    available; errors are left to the caller (the check goes on without)."""
    from langchain.agents import create_agent

    from agent.trace import TraceMiddleware
    spec = diagnoser_spec(session, model, middleware=[TraceMiddleware(session, "diagnoser")])
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


# --- Plain Python checks the answer -------------------------------------------

def _as_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def validate_diagnosis(raw, facts, valid_rows, accept_passed):
    """The diagnosis as the student may see it. Never raises.

    - Causes with no evidence, with a fact id that does not exist, or with a
      row that is not a real row of this check are dropped; at most 3 stay.
    - A cause longer than 120 characters is cut at a word.
    - how_sure is "likely" or "possible" (anything else: "possible").
    - The question must end with "?", else it is dropped.
    - Veto: if the lesson's accept rules fail, the status cannot be "ok"
      ("unsure"). If they pass, the status is "ok": the AI cannot fail a
      passing circuit, so its causes stay only as "possible" notes.
    Returns (diagnosis, number of causes dropped)."""
    raw = _as_dict(raw)
    ids = {f["id"] for f in facts}
    valid_rows = set(valid_rows)
    status = raw.get("status") if raw.get("status") in STATUSES else "unsure"
    causes, dropped = [], 0
    for c in raw.get("causes") or []:
        c = _as_dict(c)
        text = str(c.get("cause") or "").strip()
        evidence = [str(e) for e in (c.get("evidence") or [])]
        rows = [str(r) for r in (c.get("rows") or [])]
        if (not text or not evidence or any(e not in ids for e in evidence)
                or any(r not in valid_rows or not is_node(r) for r in rows)):
            dropped += 1
            continue
        if len(text) > MAX_CAUSE_CHARS:
            text = text[:MAX_CAUSE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        how = c.get("how_sure") if c.get("how_sure") in SURE else "possible"
        causes.append({"cause": text, "rows": rows, "evidence": evidence, "how_sure": how})
    dropped += max(0, len(causes) - MAX_CAUSES)
    causes = causes[:MAX_CAUSES]
    question = raw.get("question")
    question = question.strip() if isinstance(question, str) and question.strip().endswith("?") \
        else None
    if accept_passed:
        status = "ok"
        for c in causes:
            c["how_sure"] = "possible"  # a note at most: the circuit passes
    else:
        if status == "ok":
            status = "unsure"
        if status == "mistake" and not causes:
            status = "unsure"
    out = {"status": status, "causes": causes}
    if question:
        out["question"] = question
    return out, dropped


def lights_from(diagnosis):
    """{row: color}: rows of "likely" causes red, "possible" ones yellow
    (red wins). Only rows with a light (not the + and - lines)."""
    lights = {}
    for c in diagnosis.get("causes", []):
        color = "red" if c["how_sure"] == "likely" else "yellow"
        for row in c["rows"]:
            if is_row(row) and lights.get(row) != "red":
                lights[row] = color
    return lights


def compare_with_rules(result, diagnosis):
    """The lesson's own rule match next to the diagnosis, for later
    evaluation: do they agree?"""
    status = result.get("status")
    rule_rows = set(result.get("rows") or [])
    top = diagnosis["causes"][0] if diagnosis.get("causes") else None
    if status in ("ok", "step_ok"):
        # The rules pass: they agree only if the diagnoser found nothing at
        # all (a "possible" note after the veto still counts as different).
        agrees = diagnosis["status"] == "ok" and not diagnosis.get("causes")
    elif status == "mistake":
        agrees = bool(top and rule_rows & set(top["rows"])) if rule_rows else \
            diagnosis["status"] != "ok"
    else:
        agrees = diagnosis["status"] != "ok"
    return {"status": status, "mistake_id": result.get("mistake_id"),
            "rows": sorted(rule_rows), "agrees": agrees}
