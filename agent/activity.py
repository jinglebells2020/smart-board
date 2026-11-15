"""Agent activity: everything Zip's AI agents (and plain-Python safety) do,
live, for the judges' panel on the page.

Nothing here changes what happens. It reads the same recorded steps as
"How Zip worked" (agent/trace.py) and adds a few things plain Python does
outside a reply (instant help noticing a change, the safety cut). Each item:

    {"time", "agent", "action", "kind"}

agent: one of AGENTS. "Python safety" is everything plain Python decides
(physics facts from the measurements, safety cuts), so the split between
AI and plain Python is easy to see. The diagnoser's own steps (asked, its
answer, its causes checked against the facts) are listed as "Diagnoser".
kind: one of KINDS.
tag: "instant" for the steps of an automatic check (instant help started
it; Zip and its helpers still do the work), else None.
"""

import threading
import time
from collections import Counter, deque

from agent.trace import describe_step

AGENTS = ["Zip (tutor)", "Diagnoser", "Detective", "Lesson writer", "Grader",
          "Build guide", "Instant help", "Python safety"]
KINDS = ["think", "measure", "act on board", "ask student", "check", "memory"]
KEEP = 50  # items shown on the page (the counts cover the whole session)

# The tutor's own steps (and the tools its helpers share).
TOOL_KIND = {
    "read_rows": "measure", "read_current": "measure", "guide_next_part": "measure",
    "look_at_the_case": "measure",
    "cut_power": "act on board", "restore_power": "act on board", "light_row": "act on board",
    "clear_lights": "act on board", "show_on_board": "act on board",
    "run_experiment": "ask student", "approval": "ask student",
    "log_mistake": "memory", "edit_file": "memory", "write_file": "memory",
    "read_file": "memory", "memory": "memory", "get_lesson_facts": "memory",
    "get_hint": "think", "next_lesson": "think", "write_todos": "think", "task": "think",
    "DetectiveAnswer": "think", "Diagnosis": "think", "diagnose": "think", "answer": "think",
    "validate": "check", "grade": "check",
}
HELPERS = {"detective": "Detective", "lesson writer": "Lesson writer",
           "lesson_writer": "Lesson writer", "grader": "Grader", "diagnoser": "Diagnoser"}
# Steps plain Python takes (no AI), whoever's reply they are in.
PYTHON = {("physics", "facts"): "check", ("diagnoser", "compare"): "check",
          ("tutor", "skip_detective"): "check"}
# Whose turn it is: the student's message and an automatic check are both
# Zip (tutor); a short build-guide turn is the Build guide.
MODE_AGENT = {"reply": "Zip (tutor)", "check": "Zip (tutor)", "guide": "Build guide"}
MODE_TAG = {"check": "instant"}


def classify(step, mode="reply"):
    """(agent, kind) for one recorded step. mode: "reply" (the student
    asked), "check" (an automatic check by instant help) or "guide" (a
    short build-guide turn)."""
    who, tool = step.get("who"), step.get("tool")
    if (who, tool) in PYTHON:
        return "Python safety", PYTHON[(who, tool)]
    if who == "safety":
        return "Python safety", "act on board" if tool == "cut" else "check"
    if tool == "experiment_result":
        kind = "measure" if (step.get("args") or {}).get("done") else "ask student"
        return MODE_AGENT.get(mode, "Zip (tutor)"), kind
    if who in HELPERS:
        agent = HELPERS[who]
        kind = "check" if agent == "Grader" else TOOL_KIND.get(tool, "think")
        return agent, kind
    # The tutor, its memory, and the student's yes / no.
    return MODE_AGENT.get(mode, "Zip (tutor)"), TOOL_KIND.get(tool, "think")


class Activity:
    """The live list (newest first, at most KEEP) and the counts."""

    def __init__(self, keep=KEEP, clock=time.time):
        self.items = deque(maxlen=keep)
        self.clock = clock
        self.lock = threading.Lock()
        self.by_agent = Counter()
        self.total = self.replies = self.experiments = self.lights = self.cuts = 0
        self._trace = None  # the trace list being read, and how far
        self._seen = 0

    def add(self, agent, action, kind, tool=None, when=None, tag=None):
        with self.lock:
            self._add(agent, action, kind, tool, when, tag)

    def _add(self, agent, action, kind, tool=None, when=None, tag=None):
        self.items.appendleft({"time": when if when is not None else self.clock(),
                               "agent": agent, "action": action, "kind": kind, "tag": tag})
        self.total += 1
        self.by_agent[agent] += 1
        if tool == "run_experiment":
            self.experiments += 1
        elif tool == "light_row":
            self.lights += 1
        elif tool in ("cut_power", "cut"):
            self.cuts += 1

    def reply(self):
        """One more reply by the agents (a student's message or an automatic
        turn; a reply that waits for the student is still one reply)."""
        with self.lock:
            self.replies += 1

    def read_trace(self, trace, mode="reply", student="the student"):
        """Add the steps of `trace` (session.trace) not seen yet. A new reply
        starts a new trace list: then reading starts again from its start."""
        with self.lock:
            if trace is not self._trace:
                self._trace, self._seen = trace, 0
            steps = list(trace[self._seen:])
            self._seen += len(steps)
            for step in steps:
                agent, kind = classify(step, mode)
                self._add(agent, describe_step(step, student), kind, step.get("tool"),
                          tag=MODE_TAG.get(mode))

    def view(self):
        with self.lock:
            return {"activity": [dict(i) for i in self.items],
                    "activity_count": {"total": self.total,
                                       "by_agent": {a: self.by_agent[a] for a in AGENTS
                                                    if self.by_agent[a]},
                                       "replies": self.replies,
                                       "experiments": self.experiments,
                                       "lights_set": self.lights,
                                       "power_cuts": self.cuts}}
