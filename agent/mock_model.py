"""A fake chat model used when there is no ANTHROPIC_API_KEY.

It does not understand language. It follows the steps in
agent/prompts.py exactly, by looking at which tools it has already
called this turn and what they returned. That is enough to run the
real deepagents loop and the real tools in tests and offline demos.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from protocol import BUTTON_COLORS, MANUAL_OFF_SCREEN, MANUAL_OFF_WORDS, MANUAL_ON_WORDS, \
    button_words, row_label


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
        # deepagents puts the student's notebook (memory) in the system prompt.
        memory = " ".join(m.text for m in messages if isinstance(m, SystemMessage))
        memory = memory.split("<agent_memory>", 1)[1].split("</agent_memory>", 1)[0] \
            if "<agent_memory>" in memory else ""

        def write_notebook(next_time):
            """Add this check to the notebook with the built-in edit_file."""
            task = last_read.get("notebook") if last_read else None
            if not task or "edit_file" in called:
                return None
            return call("edit_file", file_path=task["file"], old_string=task["add_above"],
                        new_string=f"{task['entry']} Next time: {next_time}\n"
                                   f"{task['add_above']}")

        # A hint request: only the next level of help. No measuring, no power.
        if "hint" in asked:
            if not results:
                return call("get_hint")
            return reply(result_of("get_hint")["say"])

        # The quiz was answered wrong: ask the lesson writer for a new
        # mini-lesson. No measuring, no power.
        if "another way" in asked:
            if not results:
                return call("task", subagent_type="lesson_writer",
                            description="The student answered the quiz wrong. Write a new "
                                        "mini-lesson for the same mistake.")
            written = result_of("task")
            if written.get("accepted"):
                return reply(f"Let's look at it another way. I wrote you a new mini-lesson: "
                             f"\"{written['title']}\". It compares it to "
                             f"{written['comparison']}. Have a look and try its question.")
            return reply("Let's read the mini-lesson again together, slowly. Look at the "
                         "picture in your head first, then try the question again.")

        # Instant help's guide turn: show where the next part goes (lights
        # and the board screen only; the offline tutor never runs experiments).
        if "next part goes" in asked:
            if not results:
                return call("guide_next_part")
            guide = result_of("guide_next_part")
            nxt = next((p for p in guide.get("parts", []) if not p["placed"]), None)
            if nxt is None:
                return reply("Every part is in. Press Check when you are ready.")
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called and nxt["rows"]:
                return call("light_row", rows=nxt["rows"], color="blue")
            if "show_on_board" not in called:
                legs = "|".join(f"{leg['leg'].split(' (')[0]}: {leg['row'].lstrip('T')}"
                                for leg in nxt["legs"] if leg["row"][0] in "TB")
                return call("show_on_board", text=f"Next: {nxt['part']}|{legs}")
            return reply(f"Next: the {nxt['part']}.")

        if not results:
            return call("read_rows")

        broken = next((d for n, d in results if "board_error" in d), None)
        if broken is not None:
            return reply(f"{broken['say']} ({broken['board_error']})")

        # restore_power also measures, so its answer counts as a reading.
        reads = [d for n, d in results
                 if n in ("read_rows", "read_current", "restore_power") and "lesson_check" in d]
        last_read = reads[-1] if reads else None
        check = last_read["lesson_check"] if last_read else {}
        status = check.get("status")
        if check.get("moved"):  # built somewhere else on the board: say so first
            plain_reply = reply

            def reply(text):
                return plain_reply(f"{check['moved']} {text}")
        if check.get("found") or check.get("extra_rows"):
            # An extra part comes first; then how many LED circuits were found.
            said_reply = reply
            extra_first = (check["extra_say"] if check.get("extra_rows")
                           and check.get("mistake_id") != "extra_part" else "")
            found = check.get("found", "")

            def reply(text):
                if check.get("mistake_id") == "extra_part":  # the text is the extra part
                    return said_reply(" ".join(t for t in (text, found) if t))
                return said_reply(" ".join(t for t in (extra_first, found, text) if t))

        if check.get("next_step") and status not in ("step_ok", "press_first"):
            # A lesson with steps (lesson 4): always end with the next step.
            stepped_reply = reply

            def reply(text):
                step = check["next_step"]
                return stepped_reply(text if step in text else f"{text} {step}")

        # Power is off: ask for it to come back on. Plain Python decides
        # whether the student asked; if not, restore_power refuses.
        if (status == "power_off" and "restore_power" not in called
                and not last_read["safety_cut"]):
            return call("restore_power")
        if called[-1] == "restore_power":
            restored = result_of("restore_power")
            if "rejected" in str(restored.get("raw", "")).lower():
                # The student said no when asked "Turn the power back on?".
                return reply("OK, the power stays off. When you have fixed the circuit, "
                             f"press {button_words(4)} or tell me you fixed it.")
            if "do_instead" in restored:
                return reply("This board cannot switch the power for you. Please switch "
                             f"the power back on by hand: {MANUAL_ON_WORDS}. Then tell me to "
                             "check again.")
            if restored.get("error"):
                return reply(f"Power is off. When you have fixed the circuit, press "
                             f"{button_words(4)} or tell me you fixed it, and the power will "
                             "come back on.")
            # Power came on and was measured at once: carry on from that check.

        if status == "free":
            # Free build: no plan. Phase 2: the circuit reader's physics-checked
            # guess gives a feedback list (agent/feedback.py), most important first.
            items = check.get("feedback")
            if check.get("action") == "cut_power":
                if "show_on_board" not in called:
                    return call("show_on_board", text="Power OFF:|too much current.")
                return reply(f"I switched the power off to keep your parts safe: "
                             f"{check.get('danger', 'too much current')}. Look for an LED with "
                             "no resistor, or a wire straight from + to -.")
            rows = check.get("measured_rows", {})
            if not rows:
                return reply("Free build: nothing is plugged into a row I can measure yet. "
                             "Build something, then press Check.")
            if items is not None:
                # Lights: green = working part, yellow = possible, red = likely problem
                # (a row gets the most serious colour). Usable rows only.
                colour_of = {}
                for colour in ("green", "yellow", "red"):
                    for item in items:
                        if item.get("light") == colour:
                            colour_of.update({r: colour for r in item["rows"]})
                groups = [(c, [r for r, k in colour_of.items() if k == c])
                          for c in ("red", "yellow", "green")]
                groups = [(c, rs) for c, rs in groups if rs]
                if groups and "clear_lights" not in called:
                    return call("clear_lights")
                lit = called.count("light_row")
                if lit < len(groups):
                    return call("light_row", rows=groups[lit][1], color=groups[lit][0])
                main = next((i for i in items if i["level"] in ("problem", "maybe")), None)
                good = [i["text"] for i in items if i["level"] == "good"]
                info = [i["text"] for i in items if i["level"] == "info"]
                plan = [i["text"] for i in items if i["kind"] == "experiment"]
                info = plan + [t for t in info if t not in plan]  # the experiment to try first
                if main:
                    text = main["text"]  # the hint: where to look, not the whole fix
                    if main["kind"] != "not_sure" and good:
                        text += " " + good[0]
                else:
                    text = "Your circuit works! " + " ".join(good[:2])
                return reply(" ".join([text] + info[:1]).strip())
            said = ", ".join(f"{row_label(r)} {max(v, 0):.1f} V" for r, v in list(rows.items())[:6])
            more = f" and {len(rows) - 6} more" if len(rows) > 6 else ""
            current = check.get("current_ma")
            amps = ("" if current is None else f" The current is {max(current, 0):.1f} mA.")
            return reply(f"Free build: I measured {len(rows)} row{'s' if len(rows) != 1 else ''} "
                         f"with something in {'them' if len(rows) != 1 else 'it'}: {said}{more}."
                         f"{amps} Tell me what it should do, and press Check after each change.")

        if status == "mistake":
            if check["action"] == "cut_power" and "cut_power" not in called:
                return call("cut_power")
            if "log_mistake" not in called:
                return call("log_mistake", mistake_id=check["mistake_id"])
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called:
                # Red: problem here. A loose leg is yellow: careful, push it in.
                return call("light_row", rows=check["rows"], color=check.get("light", "red"))
            if (check.get("extra_rows") and check["mistake_id"] != "extra_part"
                    and called.count("light_row") < 2):
                # A part outside the plan, besides the mistake: yellow, careful.
                return call("light_row", rows=check["extra_rows"], color="yellow")
            logged = result_of("log_mistake")
            manual = "do_instead" in result_of("cut_power")
            if "show_on_board" not in called:
                if manual:
                    note = MANUAL_OFF_SCREEN
                else:
                    where = ("Yellow: look here." if check.get("light") == "yellow"
                             else "Red rows: look here.")
                    note = f"{logged['mini_lesson']['title']}|{where}"
                return call("show_on_board", text=note)
            # The same mistake again: ask the lesson writer for a new mini-lesson.
            if logged.get("new_lesson_wanted") and "task" not in called:
                return call("task", subagent_type="lesson_writer",
                            description=f"The same mistake ({check['mistake_id']}) happened "
                                        "again. Write a new mini-lesson for it.")
            title = logged["mini_lesson"]["title"]
            note = write_notebook(f"remember the idea \"{title}\" and look at that "
                                  "part first.")
            if note:
                return note
            say = logged["say"]
            written = result_of("task")
            if written.get("accepted"):
                say += (f" I also wrote you a new mini-lesson: \"{written['title']}\". "
                        "Press Learn why to read it.")
            if f'mistake "{title}"' in memory:  # loaded before this check was written
                say = f"Your notebook says this happened before, so look extra carefully. {say}"
            restored = result_of("restore_power")
            if restored.get("recut"):
                return reply(f"{restored['why']} {say}")
            if manual:
                return reply(f"Switch the power off by hand right now: {MANUAL_OFF_WORDS}. "
                             f"That keeps your parts safe. {say}")
            if check["action"] == "cut_power":
                if check.get("board_cut"):  # the board did it by itself
                    first = check["board_cut"]["say"].split(". ")[0] + "."
                    return reply(f"{first} {say}")
                return reply(f"I cut the power to keep your parts safe. {say}")
            color = check.get("light", "red")  # a loose leg is yellow: careful
            many = len(check.get("rows") or []) != 1
            return reply(f"{say} The {color} light{'s show' if many else ' shows'} "
                         "where to look.")

        if status == "ok":
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called and last_read["lesson"]["rows"]:
                return call("light_row", rows=last_read["lesson"]["rows"], color="green")
            if "next_lesson" not in called:
                return call("next_lesson")
            nxt = result_of("next_lesson")
            if "show_on_board" not in called:
                where = f"Next: lesson {nxt['lesson']}" if nxt["lesson"] else "All done!"
                return call("show_on_board", text=f"Correct! Well done.|{where}")
            note = write_notebook(f"start lesson {nxt['lesson']}, {nxt['title']}, the same "
                                  "careful way." if nxt["lesson"] else "try building a circuit "
                                  "of your own.")
            if note:
                return note
            note = ""
            if check.get("not_checked"):
                note = (" This board cannot measure current, so I could not check "
                        "whether the resistor is too big.")
            praise = ("You know how a breadboard connects!" if check.get("explore")
                      else "Your circuit is correct!")
            if nxt["lesson"] is None:
                return reply(f"{praise} {nxt['reason']}{note}")
            return reply(f"{praise} Next: lesson {nxt['lesson']}, "
                         f"{nxt['title']} ({nxt['circuit']}). {nxt['reason']}{note}")

        if status == "step_ok":  # lesson 4: it works with the button held
            if "clear_lights" not in called:
                return call("clear_lights")
            if "light_row" not in called and last_read["lesson"]["rows"]:
                return call("light_row", rows=last_read["lesson"]["rows"], color="green")
            if "show_on_board" not in called:
                return call("show_on_board",
                            text=f"Works when held!|Now let go, then|press {BUTTON_COLORS[1]}.")
            return reply("It works while you hold the button: current flows and the LED "
                         f"lights up. {check['next_step']}")

        if status == "press_first":  # lesson 4: nothing flows yet, hold the button
            if "show_on_board" not in called:
                return call("show_on_board",
                            text=f"Hold the button down|and press {BUTTON_COLORS[1]} again.")
            return reply(check["message"])

        if status == "power_off":
            if check.get("board_cut"):
                return reply(check["board_cut"]["say"])
            return reply("Power is off. Tell me when you have fixed the circuit "
                         "and I will turn it back on.")

        if status == "quiz_first":  # lesson 0: answer its question first
            return reply(check["message"])

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
        # Not on the list: ask the detective subagent for likely causes.
        if status == "unknown" and "task" not in called:
            return call("task", subagent_type="detective",
                        description=f"Lesson {last_read['lesson']['id']}: a mistake that is "
                                    f"not on the list. Clue: {clue.get('text', '')}")
        questions = []
        found = result_of("task")
        if isinstance(found, dict):
            questions = found.get("questions", [])
        note = write_notebook(f"check first: {questions[0]}" if questions else
                              "compare each part with the lesson plan, one at a time.")
        if note:
            return note
        text = f"This is not a mistake I know yet, but here is a clue. {clue.get('text', '')}"
        if questions:
            text += " The detective has " + ("a question" if len(questions) == 1 else
                                             "two questions") + " for you: " + " ".join(questions)
        return reply(text.strip())
