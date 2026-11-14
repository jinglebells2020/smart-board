"""Build the deep agent.

If ANTHROPIC_API_KEY is set, the real Claude model is used. If not,
the offline ScriptedTutorModel is used, so everything still runs.
"""

import os
import uuid

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.detective import ScriptedDetectiveModel, detective_spec
from agent.lesson_writer import ScriptedGraderModel, ScriptedLessonWriterModel, lesson_writer_spec
from agent.mock_model import ScriptedTutorModel
from agent.notebook import notebook_tools_backend
from agent.prompts import SYSTEM_PROMPT
from agent.tools import make_tools
from agent.trace import TraceMiddleware

DEFAULT_MODEL = "claude-opus-5-5"


def make_model():
    """Return (model, is_real). Never hard-code a key: it is read from the environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        name = os.environ.get("SMART_BOARD_MODEL", DEFAULT_MODEL)
        return f"anthropic:{name}", True
    return ScriptedTutorModel(), False


# What Zip asks before the power comes back on (human in the loop).
POWER_QUESTION = ("Turn the power back on? I will measure your circuit right away, "
                  "and switch it off again if it is still not safe.")


def build_agent(session, model=None, detective_model=None, ask_before_power=False,
                writer_model=None, grader_model=None):
    """The deep agent for one session.

    ask_before_power: pause before restore_power and ask the student (deepagents
    interrupt_on, human in the loop). The web page and the terminal tutor turn
    it on; answer_power() gives the student's yes or no."""
    if model is None:
        model, _ = make_model()
    # The detective subagent (task tool). Offline, it gets its own scripted
    # model; with Claude it uses the same model as the tutor.
    if detective_model is None and isinstance(model, ScriptedTutorModel):
        detective_model = ScriptedDetectiveModel()
    # The lesson writer subagent and its grader (RubricMiddleware). Offline,
    # scripted models; with Claude, the same model as the tutor.
    offline = isinstance(model, ScriptedTutorModel)
    writer_model = writer_model or (ScriptedLessonWriterModel() if offline else model)
    grader_model = grader_model or (ScriptedGraderModel() if offline else model)
    # Every tool call is recorded as it runs, for "How Zip worked".
    extra = {"subagents": [detective_spec(session, detective_model,
                                          middleware=[TraceMiddleware(session, "detective")]),
                           lesson_writer_spec(session, writer_model, grader_model)],
             "middleware": [TraceMiddleware(session, "tutor")]}
    # The student's notebook: loaded as memory at the start of every turn,
    # on disk, and the only place the agent may write.
    if session.notebook is not None:
        backend, permissions = notebook_tools_backend(
            session.notebook,
            on_memory_read=lambda: session.trace.append({"who": "memory", "tool": "memory"}))
        extra.update(backend=backend, permissions=permissions,
                     memory=[session.notebook.agent_path])
    if ask_before_power:
        # Human in the loop: the run pauses before restore_power until the
        # student answers. Pausing needs a checkpointer to keep the run.
        extra.update(interrupt_on={"restore_power": {"allowed_decisions": ["approve", "reject"],
                                                     "description": POWER_QUESTION}},
                     checkpointer=InMemorySaver())
    agent = create_deep_agent(
        model=model,
        tools=make_tools(session),
        system_prompt=SYSTEM_PROMPT,
        **extra,
    )
    agent.smart_board_checkpointed = ask_before_power
    agent.smart_board_pending = None  # a reply waiting for the student's yes or no
    # ask() tells the session what the student said, so plain Python (not
    # the AI) decides whether this message may turn power on.
    agent.smart_board_session = session
    return agent


def ask(agent, history, text):
    """Send one student message. history is the list of past messages,
    updated in place. Returns the agent's reply text and the new messages.

    With ask_before_power, the reply can stop before the power comes back
    on: then the reply is POWER_QUESTION, agent.smart_board_pending is set,
    and answer_power() finishes the reply."""
    session = getattr(agent, "smart_board_session", None)
    if session is not None:
        session.hear(text)
        session.trace = []  # a new reply: a new "How Zip worked"
    before = len(history)
    # Each reply is its own short thread: the checkpointer only keeps a reply
    # that is waiting for the student, and memory is read fresh every time.
    config = ({"configurable": {"thread_id": uuid.uuid4().hex}}
              if getattr(agent, "smart_board_checkpointed", False) else None)
    result = agent.invoke({"messages": history + [{"role": "user", "content": text}]}, config)
    return _finish(agent, history, before, result, config)


def answer_power(agent, history, yes):
    """The student's answer to "Turn the power back on?". Yes counts as the
    student asking for power; restore_power then runs, measures at once and
    cuts again if the circuit is still unsafe. No: the tool never runs."""
    pending = getattr(agent, "smart_board_pending", None)
    if pending is None:
        raise ValueError("nothing is waiting for an answer")
    session = agent.smart_board_session
    session.trace.append({"who": "you", "tool": "approval", "args": {"yes": bool(yes)}})
    session.restore_allowed = bool(yes)  # pressing Yes is asking for power
    decision = ({"type": "approve"} if yes else
                {"type": "reject", "message": "You said no: the power stays off."})
    result = agent.invoke(Command(resume={"decisions": [decision]}), pending["config"])
    return _finish(agent, history, pending["before"], result, pending["config"])


def _finish(agent, history, before, result, config):
    session = getattr(agent, "smart_board_session", None)
    if result.get("__interrupt__"):
        agent.smart_board_pending = {"config": config, "before": before}
        return POWER_QUESTION, result["messages"][before:]
    agent.smart_board_pending = None
    history[:] = [_without_thinking(m) for m in result["messages"]]
    if session is not None:
        session.notebook_after_turn()
    reply = result["messages"][-1].content
    if isinstance(reply, list):  # Claude can return a list of content blocks
        reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
    return reply, result["messages"][before:]


def _without_thinking(message):
    """The message without Claude's thinking blocks.

    A thinking block is signed for the conversation it was made in. The
    notebook (memory) changes the start of the conversation every turn, so
    sending an old thinking block back makes Claude answer "400 Invalid
    signature in thinking block". The kept history leaves them out.
    Returns the same message object when there is nothing to remove."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return message
    kept = [b for b in content
            if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
    if len(kept) == len(content):
        return message
    return message.model_copy(update={"content": kept})
