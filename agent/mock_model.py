"""A fake chat model used when there is no ANTHROPIC_API_KEY.

It does not understand language. It follows the steps in
agent/prompts.py exactly, by looking at which tools it has already
called this turn and what they returned. That is enough to run the
real deepagents loop and the real tools in tests and offline demos.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _this_turn(messages):
    """Tool results since the student's last message, oldest first."""
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    results = []
    for m in messages[last_human + 1:]:
        if isinstance(m, ToolMessage):
            try:
                data = json.loads(m.content)
            except (TypeError, ValueError):
                data = {"raw": m.content}
            results.append((m.name, data))
    return results


class ScriptedTutorModel(BaseChatModel):
    """Stand-in for Claude. Same tools, same steps, fixed wording."""

    @property
    def _llm_type(self):
        return "scripted-tutor"

    def bind_tools(self, tools, **kwargs):
        return self  # it already knows the tool names

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next(messages)
        msg.id = f"mock-{len(messages)}"
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _next(self, messages):
        results = _this_turn(messages)
        called = [name for name, _ in results]

        def call(name, **args):
            return AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": f"call-{len(messages)}"}])

        def reply(text):
            return AIMessage(content=text)

        if not results:
            return call("read_current")

        reads = [d for n, d in results if n in ("read_current", "read_voltages")]
        last_read = reads[-1] if reads else None
        check = last_read["lesson_check"] if last_read else {}

        # Power is off and the student is back: turn it on and measure again.
        if (check.get("status") == "power_off" and "restore_power" not in called
                and not last_read["safety_cut"]):
            return call("restore_power")
        if called[-1] == "restore_power":
            return call("read_current")
        if (called[-1] == "read_current" and not last_read["safety_cut"]
                and check.get("status") != "power_off"):
            return call("read_voltages")

        status = check.get("status")
        if status == "mistake":
            if check["action"] == "cut_power" and "cut_power" not in called:
                return call("cut_power")
            if "log_mistake" not in called:
                return call("log_mistake", mistake_id=check["mistake_id"])
            if "row" in check and "light_row_led" not in called:
                return call("light_row_led", row=check["row"])
            say = next(d for n, d in results if n == "log_mistake")["say"]
            if check["action"] == "cut_power":
                return reply(f"I cut the power to keep your parts safe. {say}")
            if "row" in check:
                return reply(f"{say} I lit up row {check['row']} to show you where to look.")
            return reply(say)

        if status == "ok":
            if "next_lesson" not in called:
                return call("next_lesson")
            nxt = next(d for n, d in results if n == "next_lesson")
            if nxt["lesson"] is None:
                return reply(f"Your circuit is correct! {nxt['reason']}")
            return reply(f"Your circuit is correct! Next: lesson {nxt['lesson']}, "
                         f"{nxt['title']} ({nxt['circuit']}). {nxt['reason']}")

        if status == "power_off":
            return reply("Power is off. Tell me when you have fixed the circuit "
                         "and I will turn it back on.")

        # Unknown mistake: say the clue from lesson_check, never a guessed fix.
        text = check.get("clue", {}).get("text", "")
        return reply(f"This is not a mistake I know yet, but here is a clue. {text}".strip())
