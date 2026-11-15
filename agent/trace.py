"""What Zip really did in one reply, step by step ("How Zip worked").

Nothing here is guessed. The steps are recorded while the agent runs:

- TraceMiddleware (a deepagents/LangChain middleware) records every tool
  call the moment it runs: the tutor's own tools, the built-in ones
  (task, edit_file, ...) and, attached to each subagent, the subagent's
  tools. Subagent steps are marked with who did them.
- The notebook backend records when deepagents loads the student's
  notebook as memory at the start of the reply.
- The rubric grader records each grade it gives (agent/lesson_writer.py).
- Asking the student before power comes back on is recorded by ask()
  (agent/build.py).

describe_steps() turns those records into short plain sentences for the
Teacher view.
"""

from langchain.agents.middleware import AgentMiddleware

from agent.deadline import check_run

from protocol import row_label


class TraceMiddleware(AgentMiddleware):
    """Records each tool call in session.trace as it runs."""

    def __init__(self, session, who="tutor"):
        super().__init__()
        self.session = session
        self.who = who

    @property
    def name(self):  # one per agent; subagents have their own stacks
        return f"TraceMiddleware[{self.who}]"

    def wrap_model_call(self, request, handler):
        check_run()  # a reply that was given up on stops here
        return handler(request)

    def wrap_tool_call(self, request, handler):
        check_run()  # a given-up reply never touches the board again
        call = request.tool_call
        step = {"who": self.who, "tool": call.get("name"), "args": dict(call.get("args") or {})}
        if not (self.session.trace and self.session.trace[-1] == step
                and step["tool"] == "run_experiment"):  # (it runs again when the student answers)
            self.session.trace.append(step)
        return handler(request)


ALREADY_DIAGNOSED = "Already diagnosed: use the diagnosis in lesson_check."


class DetectiveGuard(AgentMiddleware):
    """Speed: when the latest check already has the diagnoser's answer, a
    call to the detective is answered at once by plain Python, without
    calling the model (both together took 40 s in a real test)."""

    def __init__(self, session):
        super().__init__()
        self.session = session

    @property
    def name(self):
        return "DetectiveGuard"

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        args = call.get("args") or {}
        if call.get("name") == "task" and args.get("subagent_type") == "detective":
            last = (self.session.last_check or ({}, {}))[1] or {}
            if last.get("diagnosis"):
                from langchain_core.messages import ToolMessage
                self.session.trace.append({"who": "tutor", "tool": "skip_detective"})
                return ToolMessage(content=ALREADY_DIAGNOSED, tool_call_id=call.get("id"),
                                   name="task")
        return handler(request)


def _rows(rows):
    if isinstance(rows, str):
        rows = rows.replace(",", " ").split()
    words = [row_label(r).replace("row ", "") for r in rows or []]
    if not words:
        return "rows"
    if len(words) == 1:
        return f"row {words[0]}"
    return "rows " + ", ".join(words[:-1]) + " and " + words[-1]


SUBAGENTS = {"detective": "the detective", "lesson_writer": "the lesson writer",
             "diagnoser": "the diagnoser"}


def describe_step(step, student="the student"):
    """One recorded step in plain words."""
    who, tool, args = step["who"], step.get("tool"), step.get("args", {})
    owner = "the student's" if student in ("", "student", "the student") else f"{student}'s"
    if who == "memory":
        return f"Read {owner} notebook"
    if who == "you":
        return ("Asked you: turn the power back on? You said yes" if args.get("yes")
                else "Asked you: turn the power back on? You said no")
    if who == "physics":
        n = args.get("count", 0)
        return f"Worked out {n} fact{'s' if n != 1 else ''} from the measurements"
    if who == "diagnoser" and tool == "diagnose":
        return "Asked the diagnoser"
    if who == "diagnoser" and tool == "answer":
        n = args.get("causes") or 0
        causes = f"{n} possible cause{'s' if n != 1 else ''}"
        return {"ok": "The diagnoser answered: the circuit looks right",
                "mistake": f"The diagnoser answered: a mistake, {causes}",
                "unsure": f"The diagnoser answered: not sure yet, {causes}"}.get(
            args.get("status"), f"The diagnoser answered ({causes})")
    if who == "diagnoser" and tool == "validate":
        dropped = args.get("dropped", 0)
        return ("Checked every cause against the facts" +
                (f" (left out {dropped} that the facts do not show)" if dropped else ""))
    if who == "diagnoser" and tool == "compare":
        return ("Kept both answers: the diagnoser and the lesson's own rules "
                f"({str(args.get('rule', '')).replace('_', ' ')}) disagree")
    if who == "grader":
        verdict = {"satisfied": "it passes every rule",
                   "needs_revision": "it needs another try",
                   "max_iterations_reached": "it still breaks a rule",
                   "failed": "it breaks a rule"}.get(args.get("result"), args.get("result", ""))
        return f"Graded the new lesson: {verdict}"
    words = {
        "read_rows": "Measured the rows",
        "read_current": "Measured the current",
        "cut_power": "Cut the power",
        "restore_power": "Turned the power back on and measured it at once",
        "get_hint": "Gave the next level of help",
        "clear_lights": "Turned the lights off",
        "log_mistake": f"Saved the mistake ({args.get('mistake_id', '').replace('_', ' ')})",
        "next_lesson": "Picked the next lesson",
        "edit_file": f"Wrote in {owner} notebook",
        "write_file": f"Wrote in {owner} notebook",
        "read_file": f"Read {owner} notebook",
        "write_todos": "Made a plan",
        "look_at_the_case": "Compared the rows with the plan",
        "skip_detective": "Did not ask the detective: the diagnosis already explains it",
        "get_lesson_facts": "Read the old mini-lesson and the comparisons already used",
    }
    if tool == "cut_power" and args.get("reason"):
        return f"Cut the power: {args['reason']}"
    if tool in words:
        return words[tool]
    if tool == "run_experiment":
        what = str(args.get("instruction", "")).strip().rstrip(".")
        return f"Asked you to {what[:1].lower() + what[1:]}" if what else "Asked you to try something"
    if tool == "experiment_result":
        if not args.get("done"):
            return "You could not do it"
        changed = args.get("changed") or []
        return ("Measured again: " + "; ".join(changed[:2])) if changed else \
            "Measured again: nothing changed"
    if tool == "guide_next_part":
        return "Looked at which parts are in"
    if tool == "light_row":
        return f"Lit {_rows(args.get('rows'))} {args.get('color', '')}".strip()
    if tool == "show_on_board":
        first = str(args.get("text", "")).split("|")[0]
        return f'Wrote "{first}" on the board screen'
    if tool == "task":
        sub = args.get("subagent_type", "")
        return f"Asked {SUBAGENTS.get(sub, 'a helper (' + sub + ')')}"
    if tool in ("DetectiveAnswer",):
        return "Answered with at most two questions"
    if tool in ("Diagnosis",):
        return "Answered with a diagnosis"
    return f"Used {tool}"


def describe_steps(trace, student="the student"):
    """[{"text", "who"}] for every recorded step, in order. "who" is
    "tutor" for Zip's own steps, otherwise the helper that did it."""
    return [{"text": describe_step(s, student), "who": s["who"]} for s in trace]
