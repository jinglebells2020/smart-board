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

        def result_of(name):
            return next((d for n, d in reversed(results) if n == name), {})

        last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        asked = str(messages[last_human].content).lower()

        # A hint request: only the next level of help. No measuring, no power.
        if "hint" in asked:
            if not results:
                return call("get_hint")
            return reply(result_of("get_hint")["say"])

        if not results:
            return call("read_rows")

        # restore_power also measures, so its answer counts as a reading.
        reads = [d for n, d in results
                 if n in ("read_rows", "read_current", "restore_power") and "lesson_check" in d]
        last_read = reads[-1] if reads else None
        check = last_read["lesson_check"] if last_read else {}
        status = check.get("status")

        # Power is off: ask for it to come back on. Plain Python decides
        # whether the student asked; if not, restore_power refuses.
        if (status == "power_off" and "restore_power" not in called
                and not last_read["safety_cut"]):
            return call("restore_power")
        if called[-1] == "restore_power":
            restored = result_of("restore_power")
            if "do_instead" in restored:
                return reply("This board cannot switch the power for you. Please switch "
                             "the power back on by hand, then tell me to check again.")
            if restored.get("error"):
                return reply("Power is off. When you have fixed the circuit, press button 4 "
                             "or tell me you fixed it, and the power will come back on.")
            # Power came on and was measured at once: carry on from that check.

        if status == "mistake":
            if check["action"] == "cut_power" and "cut_power" not in called:
                return call("cut_power")
            if "log_mistake" not in called:
                return call("log_mistake", mistake_id=check["mistake_id"])
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called:
                return call("light_row", rows=check["rows"], color="red")
            logged = result_of("log_mistake")
            manual = "do_instead" in result_of("cut_power")
            if "show_on_board" not in called:
                if manual:
                    note = "DANGER!|Switch the power OFF|by hand now."
                else:
                    note = f"{logged['mini_lesson']['title']}|Red rows: look here."
                return call("show_on_board", text=note)
            say = logged["say"]
            restored = result_of("restore_power")
            if restored.get("recut"):
                return reply(f"{restored['why']} {say}")
            if manual:
                return reply(f"Switch the power off by hand right now, to keep your parts "
                             f"safe. {say}")
            if check["action"] == "cut_power":
                return reply(f"I cut the power to keep your parts safe. {say}")
            return reply(f"{say} The red lights show where to look.")

        if status == "ok":
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called:
                return call("light_row", rows=last_read["lesson"]["rows"], color="green")
            if "next_lesson" not in called:
                return call("next_lesson")
            nxt = result_of("next_lesson")
            if "show_on_board" not in called:
                where = f"Next: lesson {nxt['lesson']}" if nxt["lesson"] else "All done!"
                return call("show_on_board", text=f"Correct! Well done.|{where}")
            note = ""
            if check.get("not_checked"):
                note = (" This board cannot measure current, so I could not check "
                        "whether the resistor is too big.")
            if nxt["lesson"] is None:
                return reply(f"Your circuit is correct! {nxt['reason']}{note}")
            return reply(f"Your circuit is correct! Next: lesson {nxt['lesson']}, "
                         f"{nxt['title']} ({nxt['circuit']}). {nxt['reason']}{note}")

        if status == "power_off":
            return reply("Power is off. Tell me when you have fixed the circuit "
                         "and I will turn it back on.")

        if status == "missing_rows":
            if "show_on_board" not in called:
                return call("show_on_board", text="Cannot check:|this board does not|"
                                                  "measure every row.")
            return reply(check["message"])

        # Unknown mistake: light the rows the clue is about yellow (careful),
        # and say the clue, never a guessed fix.
        clue = check.get("clue", {})
        rows = [d["row"] for d in clue.get("details", []) if d.get("row")]
        if rows and "light_row" not in called:
            if "clear_lights" not in called:
                return call("clear_lights")
            return call("light_row", rows=rows, color="yellow")
        if "show_on_board" not in called:
            return call("show_on_board", text="Something is off.|Yellow rows: careful.|"
                                              "Clue on the laptop.")
        text = clue.get("text", "")
        return reply(f"This is not a mistake I know yet, but here is a clue. {text}".strip())
