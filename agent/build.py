"""Build the deep agent.

If ANTHROPIC_API_KEY is set, the real Claude model is used. If not,
the offline ScriptedTutorModel is used, so everything still runs.
"""

import os

from deepagents import create_deep_agent

from agent.detective import ScriptedDetectiveModel, detective_spec
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


def build_agent(session, model=None, detective_model=None):
    if model is None:
        model, _ = make_model()
    # The detective subagent (task tool). Offline, it gets its own scripted
    # model; with Claude it uses the same model as the tutor.
    if detective_model is None and isinstance(model, ScriptedTutorModel):
        detective_model = ScriptedDetectiveModel()
    # Every tool call is recorded as it runs, for "How Zip worked".
    extra = {"subagents": [detective_spec(session, detective_model,
                                          middleware=[TraceMiddleware(session, "detective")])],
             "middleware": [TraceMiddleware(session, "tutor")]}
    # The student's notebook: loaded as memory at the start of every turn,
    # on disk, and the only place the agent may write.
    if session.notebook is not None:
        backend, permissions = notebook_tools_backend(
            session.notebook,
            on_memory_read=lambda: session.trace.append({"who": "memory", "tool": "memory"}))
        extra.update(backend=backend, permissions=permissions,
                     memory=[session.notebook.agent_path])
    agent = create_deep_agent(
        model=model,
        tools=make_tools(session),
        system_prompt=SYSTEM_PROMPT,
        **extra,
    )
    # ask() tells the session what the student said, so plain Python (not
    # the AI) decides whether this message may turn power on.
    agent.smart_board_session = session
    return agent


def ask(agent, history, text):
    """Send one student message. history is the list of past messages,
    updated in place. Returns the agent's reply text and the new messages."""
    session = getattr(agent, "smart_board_session", None)
    if session is not None:
        session.hear(text)
        session.trace = []  # a new reply: a new "How Zip worked"
    before = len(history)
    result = agent.invoke({"messages": history + [{"role": "user", "content": text}]})
    history[:] = result["messages"]
    if session is not None:
        session.notebook_after_turn()
    reply = result["messages"][-1].content
    if isinstance(reply, list):  # Claude can return a list of content blocks
        reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
    return reply, result["messages"][before:]
