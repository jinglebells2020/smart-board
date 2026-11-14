"""Build the deep agent.

If ANTHROPIC_API_KEY is set, the real Claude model is used. If not,
the offline ScriptedTutorModel is used, so everything still runs.
"""

import os

from deepagents import create_deep_agent

from agent.mock_model import ScriptedTutorModel
from agent.prompts import SYSTEM_PROMPT
from agent.tools import make_tools

DEFAULT_MODEL = "claude-opus-5-5"


def make_model():
    """Return (model, is_real). Never hard-code a key: it is read from the environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        name = os.environ.get("SMART_BOARD_MODEL", DEFAULT_MODEL)
        return f"anthropic:{name}", True
    return ScriptedTutorModel(), False


def build_agent(session, model=None):
    if model is None:
        model, _ = make_model()
    return create_deep_agent(
        model=model,
        tools=make_tools(session),
        system_prompt=SYSTEM_PROMPT,
    )


def ask(agent, history, text):
    """Send one student message. history is the list of past messages,
    updated in place. Returns the agent's reply text and the new messages."""
    before = len(history)
    result = agent.invoke({"messages": history + [{"role": "user", "content": text}]})
    history[:] = result["messages"]
    reply = result["messages"][-1].content
    if isinstance(reply, list):  # Claude can return a list of content blocks
        reply = "".join(b.get("text", "") for b in reply if isinstance(b, dict))
    return reply, result["messages"][before:]
