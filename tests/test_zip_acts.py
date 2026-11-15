"""Zip acts: tools that act on the board and the student.

run_experiment (one hand action, then measure again), guide_next_part
(which parts are in; the tutor lights the next one blue), cut_power(reason).
Safety stays plain Python: the tutor may switch power off at any time, but
power only comes on through the "Turn the power back on?" approval."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.build import POWER_QUESTION, answer_experiment, answer_power, ask, build_agent
from agent.detective import ScriptedDetectiveModel
from agent.experiments import MAX_EXPERIMENTS, diff_facts, placed_parts, validate_instruction
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from agent.tools import make_tools
from agent.trace import describe_steps
from sim.fake_board import FakeBoard
from web.server import App
from tests.test_instant import InstantApp

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
PROMPTS = (ROOT / "agent" / "prompts.py").read_text(encoding="utf-8")
TURN_LED = "Turn around the LED in rows 10 and 12."


class ScriptModel(BaseChatModel):
    """A fake "Claude": each reply follows the script, one step per model call
    since the student's message: {"tool": name, "args": {...}} or {"reply": text}.
    seen: every tool result it was given (parsed JSON when possible)."""
    script: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "script"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        done = sum(isinstance(m, AIMessage) for m in messages[last_human:])
        if messages and isinstance(messages[-1], ToolMessage):
            try:
                self.seen.append(json.loads(messages[-1].content))
            except (TypeError, ValueError):
                self.seen.append(messages[-1].content)
        step = self.script[done] if done < len(self.script) else {"reply": "OK."}
        if "reply" in step:
            msg = AIMessage(content=step["reply"])
        else:
            msg = AIMessage(content="", tool_calls=[{"name": step["tool"], "args": step.get("args", {}),
                                                     "id": f"c{done}"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def experiment(instruction=TURN_LED, done="I turned it", cant="I can't"):
    return {"tool": "run_experiment",
            "args": {"instruction": instruction, "done_label": done, "cant_label": cant}}


def make_agent(board, script, lesson=1):
    session = Session(board, lesson=lesson)
    model = ScriptModel(script=script)
    agent = build_agent(session, model, detective_model=ScriptedDetectiveModel(),
                        ask_before_power=True)
    return session, model, agent


def texts(session):
    return [s["text"] for s in describe_steps(session.trace)]


def tool(session, name, can_pause=False):
    return next(t for t in make_tools(session, can_pause=can_pause) if t.__name__ == name)


class TestInstruction(unittest.TestCase):
    def setUp(self):
        self.lesson = Session(FakeBoard(1, "correct", seed=1), lesson=1).lesson

    def ok(self, text):
        ok, error, parsed = validate_instruction(text, self.lesson)
        self.assertTrue(ok, f"{text}: {error}")
        return parsed

    def bad(self, text):
        ok, error, parsed = validate_instruction(text, self.lesson)
        self.assertFalse(ok, text)
        self.assertIsNone(parsed)
        return error

    def test_allowed_actions(self):
        self.assertEqual(self.ok(TURN_LED),
                         {"action": "turn around", "part": "Red LED", "rows": [10, 12]})
        self.assertEqual(self.ok("Push the red wire in firmly in row 5.")["action"], "push in")
        self.assertEqual(self.ok("Pull the 220 ohm resistor out of row 10.")["action"],
                         "pull out")
        self.assertEqual(self.ok("Move the black wire to row 12.")["action"], "move")
        self.assertEqual(self.ok("Press the LED leg in row 12 down.")["action"], "press")
        self.assertEqual(self.ok("Wait a moment, then look at the LED in row 10.")["action"],
                         "wait")
        self.assertEqual(self.ok("Let go of the resistor in row 5.")["action"], "let go")
        self.assertEqual(self.ok("Pull out the extra part in row 30.")["part"], "the extra part")
        self.assertEqual(self.ok("Turn around the LED in T10 and T12.")["rows"], [10, 12])

    def test_blocked(self):
        self.assertIn("power", self.bad("Turn the power off, then turn around the LED in row 10."))
        self.assertIn("battery", self.bad("Pull out the battery wire in row 5."))
        self.assertIn("unplug", self.bad("Unplug the LED in row 10."))
        self.assertIn("ONE action",
                      self.bad("Pull out the LED in row 10 and push in the resistor in row 5."))
        self.assertIn("ONE action", self.bad("Look at the LED in row 10."))
        self.assertIn("ONE action", self.bad("Bite the LED in row 10."))
        self.assertIn("Rephrase", json.loads(tool(Session(FakeBoard(1, "correct", seed=1),
                                                          lesson=1), "run_experiment", True)(
            "Look at the LED in row 10."))["msg"])

    def test_unknown_part_or_row(self):
        self.assertIn("part of this lesson", self.bad("Turn around the capacitor in row 10."))
        self.assertIn("row", self.bad("Turn around the LED."))
        self.assertIn("1 to 63", self.bad("Move the LED to row 70."))
        self.assertIn("short", self.bad(""))
        self.assertIn("short", self.bad("Move the LED in row 10 " + "slowly " * 30))


class TestExperiment(unittest.TestCase):
    def test_pause_then_done_returns_before_after_and_changed(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        session, model, agent = make_agent(board, [{"tool": "read_rows"}, experiment(),
                                                   {"reply": "It was backwards!"}])
        history = []
        reply, _ = ask(agent, history, "Check my circuit")
        self.assertEqual(reply, TURN_LED)  # the run waits for the student
        pending = agent.smart_board_pending
        self.assertEqual(pending["kind"], "experiment")
        card = pending["experiment"]
        self.assertEqual((card["done_label"], card["cant_label"], card["action"], card["rows"]),
                         ("I turned it", "I can't", "turn around", [10, 12]))
        self.assertEqual(history, [])  # nothing kept until the reply finishes
        with self.assertRaises(ValueError):
            answer_power(agent, history, True)  # an experiment is not a power question

        board.set_scenario(1, "correct")  # the student turns the LED around
        reply, _ = answer_experiment(agent, history, True)
        self.assertEqual(reply, "It was backwards!")
        self.assertIsNone(agent.smart_board_pending)
        out = model.seen[-1]
        self.assertTrue(out["done"])
        self.assertTrue(out["before_facts"] and out["after_facts"])
        self.assertEqual(out["lesson_check"]["status"], "ok")
        led = [c for c in out["changed"] if c["kind"] == "led_state"]
        self.assertEqual(len(led), 1)
        self.assertIn("blocked", led[0]["before"])
        self.assertIn("should be lit", led[0]["after"])
        self.assertTrue(any(w.startswith("row 10 changed from 3.3 V") for w in
                            out["changed_words"]), out["changed_words"])
        steps = texts(session)
        self.assertIn("Asked you to turn around the LED in rows 10 and 12", steps)
        self.assertEqual(steps.count("Asked you to turn around the LED in rows 10 and 12"), 1)
        self.assertTrue(any(s.startswith("Measured again: row 10 changed from 3.3 V")
                            for s in steps), steps)
        self.assertTrue(history)  # the finished reply is kept

    def test_cant(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        session, model, agent = make_agent(board, [experiment(), {"reply": "OK, a question then."}])
        ask(agent, [], "Check my circuit")
        reply, _ = answer_experiment(agent, [], False)
        self.assertEqual(reply, "OK, a question then.")
        self.assertEqual(model.seen[-1], {"done": False, "say": "The student could not do it."})
        self.assertIn("You could not do it", texts(session))
        self.assertTrue(board.power_on)  # an experiment never touches the power

    def test_nothing_changed(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        session, model, agent = make_agent(board, [experiment(), {"reply": "Hmm."}])
        ask(agent, [], "Check my circuit")
        answer_experiment(agent, [], True)  # the board did not change
        self.assertEqual(model.seen[-1]["changed"], [])
        self.assertIn("Measured again: nothing changed", texts(session))

    def test_at_most_two_per_reply(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        second = experiment("Push the LED in firmly in row 12.")
        third = experiment("Move the black wire to row 12.")
        session, model, agent = make_agent(board, [experiment(), second, third,
                                                   {"reply": "Done."}])
        self.assertEqual(MAX_EXPERIMENTS, 2)
        self.assertEqual(ask(agent, [], "Check my circuit")[0], TURN_LED)
        self.assertEqual(answer_experiment(agent, [], True)[0],
                         "Push the LED in firmly in row 12.")
        self.assertEqual(answer_experiment(agent, [], True)[0], "Done.")  # no third card
        self.assertIn("At most 2 experiments", model.seen[-1]["msg"])
        # A new reply may run experiments again.
        model.script = [experiment(), {"reply": "Again."}]
        self.assertEqual(ask(agent, [], "Check my circuit")[0], TURN_LED)

    def test_rejected_instruction_does_not_pause(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        session, model, agent = make_agent(board, [
            experiment("Unplug the battery and turn around the LED in row 10."),
            experiment(), {"reply": "Thanks."}])
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("Rephrase", model.seen[0]["msg"])  # back to the tutor, who rephrased
        self.assertEqual(reply, TURN_LED)

    def test_offline_tutor_has_no_experiments(self):
        session = Session(FakeBoard(1, "correct", seed=1), lesson=1)
        names = [t.__name__ for t in make_tools(session)]
        self.assertNotIn("run_experiment", names)  # needs a pause (a checkpointer)
        self.assertNotIn("run_experiment", (ROOT / "agent" / "mock_model.py").read_text())

    def test_diff_skips_noise_and_power(self):
        before = [{"kind": "power", "rows": [], "text": "on 5.0"},
                  {"kind": "row_voltage", "rows": ["T10"], "text": "a",
                   "values": {"measured": 2.0}}]
        after = [{"kind": "power", "rows": [], "text": "on 4.9"},
                 {"kind": "row_voltage", "rows": ["T10"], "text": "b",
                  "values": {"measured": 2.1}},
                 {"kind": "plan_row_floating", "rows": ["T12"], "text": "empty"}]
        changed, words = diff_facts(before, after)
        self.assertEqual([c["kind"] for c in changed], ["plan_row_floating"])
        self.assertEqual(words, ["row 12 is now empty"])


class TestPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = FakeBoard(1, "led_backwards", seed=1)
        self.model = ScriptModel(script=[experiment(), {"reply": "It was backwards!"}])
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=self.model, confirm_power=True)

    def test_card_then_done(self):
        out = self.app.ask("Check my circuit")
        self.assertTrue(out["pending"])
        state = self.app.state()
        self.assertEqual(state["experiment"]["instruction"], TURN_LED)
        self.assertEqual(state["experiment"]["done_label"], "I turned it")
        self.assertFalse(state["asks_power"])  # not the Yes / No power card
        self.board.set_scenario(1, "correct")
        out = self.app.answer_experiment(True)
        self.assertEqual(out["reply"], "It was backwards!")
        self.assertIsNone(self.app.state()["experiment"])
        self.assertIn("I turned it", [m["text"] for m in self.app.log if m["who"] == "student"])

    def test_a_new_message_counts_as_cant(self):
        self.app.ask("Check my circuit")
        self.model.script = [experiment(), {"reply": "OK."}]
        self.app.ask("I need a hint")
        self.assertEqual(self.model.seen[-1]["done"], False)
        self.assertIn("I can't", [m["text"] for m in self.app.log if m["who"] == "student"])

    def test_answer_without_a_card(self):
        self.assertIn("error", self.app.answer_experiment(True))

    def test_page_has_the_card(self):
        for marker in ('id="experiment"', 'id="exp-text"', 'id="exp-done"', 'id="exp-cant"',
                       "/api/experiment", "state.experiment"):
            self.assertIn(marker, HTML)
        self.assertIn("guide_next_part", HTML)
        self.assertIn("run_experiment", HTML)


class TestGuide(unittest.TestCase):
    def guide(self, session):
        return json.loads(tool(session, "guide_next_part")())

    def test_next_missing_part(self):
        board = FakeBoard(1, "correct", seed=1)
        board.send("BUILD 1")
        out = self.guide(Session(board, lesson=1))
        self.assertEqual([p["placed"] for p in out["parts"]], [True, False, False, False])
        self.assertEqual((out["placed"], out["total"], out["first_missing"]),
                         (1, 4, "220 ohm resistor"))
        self.assertEqual(out["parts"][1]["rows"], ["T5", "T10"])
        self.assertFalse(board.lights)  # Python only reports: the tutor lights

    def test_out_of_order(self):
        board = FakeBoard(1, "correct", seed=1)
        lesson = Session(board, lesson=1).lesson
        # The LED and the black wire are in (rows 10 and 12 at 0 V), nothing else.
        summary = {"power_on": True, "supply_v": 5.0, "rows": {"T5": 0.0, "T10": 0.0,
                                                               "T12": 0.0},
                   "floating": ["T5"]}
        placed = {p["part"]: p["placed"] for p in placed_parts(lesson, summary)}
        self.assertEqual(placed, {"Red wire": False, "220 ohm resistor": False,
                                  "Red LED": True, "Black wire": True})
        # A wire with 5 V across it is not in.
        summary = {"power_on": True, "supply_v": 5.0, "rows": {"T5": 5.0, "T10": 5.0,
                                                               "T12": 5.0}, "floating": []}
        self.assertFalse(placed_parts(lesson, summary)[3]["placed"])

    def test_offline_tutor_lights_the_next_part_blue(self):
        board = FakeBoard(1, "correct", seed=1)
        board.send("BUILD 1")
        session = Session(board, lesson=1)
        agent = build_agent(session, ScriptedTutorModel())
        reply, _ = ask(agent, [], App.GUIDE_TEXT)
        self.assertEqual(board.lights, {"T5": "blue", "T10": "blue"})
        self.assertIn("220 ohm resistor", reply)
        self.assertTrue(board.screen and board.screen[0].startswith("Next:"))
        self.assertTrue(all(len(line) <= 20 for line in board.screen))
        self.assertLessEqual(len(board.screen), 4)
        self.assertIn("Looked at which parts are in", texts(session))
        self.assertTrue(board.power_on)

    def test_offline_tutor_adapts_to_the_order(self):
        board = FakeBoard(1, "correct", seed=1)
        session = Session(board, lesson=1)
        agent = build_agent(session, ScriptedTutorModel())
        reading = board.read()
        reading = dict(reading, rows=dict(reading["rows"], T5=0.0, T10=0.0, T12=0.0),
                       floating=[r for r in reading["rows"] if r not in ("T10", "T12")],
                       current_ma=0.0)
        board.read = lambda: json.loads(json.dumps(reading))
        reply, _ = ask(agent, [], App.GUIDE_TEXT)
        self.assertIn("Red wire", reply)  # the LED and black wire are in already
        self.assertEqual(board.lights, {"T5": "blue"})

    def test_all_in(self):
        board = FakeBoard(1, "correct", seed=1)
        agent = build_agent(Session(board, lesson=1), ScriptedTutorModel())
        reply, _ = ask(agent, [], App.GUIDE_TEXT)
        self.assertIn("Every part is in", reply)


class TestPower(unittest.TestCase):
    def test_cut_power_switches_off_and_logs_the_reason(self):
        board = FakeBoard(1, "correct", seed=1)
        session, model, agent = make_agent(board, [
            {"tool": "cut_power", "args": {"reason": "the + line is pulled down"}},
            {"reply": "I switched the power off: the + line is pulled down."}])
        self.assertTrue(board.power_on)
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertFalse(board.power_on)
        self.assertEqual(session.power_log, [{"cut": "the + line is pulled down"}])
        self.assertIn("Cut the power: the + line is pulled down", texts(session))
        self.assertIn("pulled down", reply)

    def test_no_tool_switches_power_on_without_asking(self):
        board = FakeBoard(1, "correct", seed=1)
        session = Session(board, lesson=1)
        names = {t.__name__ for t in make_tools(session, can_pause=True)}
        self.assertEqual(names, {"read_rows", "read_current", "cut_power", "restore_power",
                                 "get_hint", "light_row", "clear_lights", "show_on_board",
                                 "log_mistake", "next_lesson", "guide_next_part",
                                 "run_experiment", "plan_experiment"})
        json.loads(tool(session, "cut_power")("test"))
        self.assertFalse(board.power_on)
        out = json.loads(tool(session, "restore_power")())  # the student did not ask
        self.assertEqual(out["error"], "not allowed")
        for name in ("guide_next_part", "read_rows", "read_current"):
            tool(session, name)()
            self.assertFalse(board.power_on, name)

    def test_power_on_still_needs_approval(self):
        board = FakeBoard(1, "correct", seed=1)
        session, model, agent = make_agent(board, [
            {"tool": "cut_power", "args": {"reason": "too much current"}},
            {"tool": "restore_power"}, {"reply": "Power is back on."}])
        reply, _ = ask(agent, [], "I fixed it")
        self.assertEqual(reply, POWER_QUESTION)  # paused: Zip asks first
        self.assertFalse(board.power_on)
        self.assertEqual(agent.smart_board_pending["kind"], "power")
        with self.assertRaises(ValueError):
            answer_experiment(agent, [], True)
        reply, _ = answer_power(agent, [], True)
        self.assertTrue(board.power_on)

    def test_prompts(self):
        for words in ("run_experiment", "guide_next_part", "cut_power(reason)",
                      "Turn around the LED in rows 10 and 12."):
            self.assertIn(words, PROMPTS)


class TestInstantGuide(InstantApp):
    def test_a_part_added_lights_the_next_one_quietly(self):
        self.make()
        self.assertEqual(self.board.lights, {})  # an empty board: quiet
        self.step("BUILD 1")
        self.assertEqual(self.board.lights, {"T5": "blue", "T10": "blue"})
        self.assertTrue(self.board.screen[0].startswith("Next:"))
        self.assertEqual(self.app.log, [])  # no chat message
        self.step("BUILD 2")
        self.assertEqual(self.board.lights, {"T10": "blue", "T12": "blue"})
        self.assertEqual(self.app.log, [])
        self.assertEqual(self.app.session.lesson_id, 1)

    def test_one_automatic_turn_at_a_time(self):
        self.make(built=1)
        calls, active, peak = [], [0], [0]
        gate = threading.Event()
        real = self.app.replies.ask

        def slow(agent, chat, text, *args, **kwargs):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            calls.append(text)
            try:
                if len(calls) == 1:
                    gate.wait(10)
                return real(agent, chat, text, *args, **kwargs)
            finally:
                active[0] -= 1

        self.app.replies.ask = slow
        self.app.auto_inline = False
        self.app.trigger_auto("guide")
        for _ in range(200):
            if calls:
                break
            time.sleep(0.01)
        self.app.trigger_auto("guide")
        self.app.trigger_auto("check")  # a check wins over a guide
        self.app.trigger_auto("guide")
        gate.set()
        self.assertTrue(self.app.wait_for_auto())
        self.assertEqual(peak[0], 1)
        self.assertEqual(calls, [App.GUIDE_TEXT, App.CHECK_TEXT])


if __name__ == "__main__":
    unittest.main()
