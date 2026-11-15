"""Instant help: Zip reacts the moment the board changes, without Check.
Plain Python notices a steady change and decides (building / mistake now /
fixed / complete); only then the normal check (with AI when available)
explains. Safety, power approval and PASS do not change."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.build import ask, build_agent
from agent.detective import ScriptedDetectiveModel
from agent.live import ChangeDetector, classify
from agent.mock_model import ScriptedTutorModel
from agent.prompts import SYSTEM_PROMPT
from agent.session import Session
from agent.trace import ALREADY_DIAGNOSED, describe_steps
from sim.fake_board import PARTS, FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def reading(driven, current=0.0, power=True):
    rows = {f"T{n}": 0.0 for n in range(1, 21)}
    rows.update(driven)
    return {"power_on": power, "supply_v": 5.0, "current_ma": current, "rows": rows,
            "floating": [r for r in rows if r not in driven]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestDebounce(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.live = ChangeDetector(clock=self.clock)

    def feed(self, r, seconds, step=0.25):
        events = []
        for _ in range(int(seconds / step)):
            self.clock.t += step
            e = self.live.feed(r)
            if e:
                events.append(e)
        return events

    def test_a_change_counts_only_after_it_stays_for_1_5_s(self):
        self.assertEqual(self.feed(reading({"T5": 5.0}), 2), [])  # the starting point
        self.assertEqual(self.feed(reading({"T5": 5.0, "T10": 5.0}), 1.25), [])  # not yet
        events = self.feed(reading({"T5": 5.0, "T10": 5.0}), 0.5)
        self.assertEqual(events, [{"kind": "change", "added": ["T10"], "removed": []}])

    def test_noise_is_not_a_change(self):
        self.feed(reading({"T5": 5.0}, 13.6), 2)
        self.assertEqual(self.feed(reading({"T5": 5.08}, 13.9), 3), [])

    def test_a_short_wiggle_is_not_a_change(self):
        self.feed(reading({"T5": 5.0}), 2)
        self.feed(reading({"T5": 5.0, "T10": 5.0}), 0.5)  # gone again before 1.5 s
        self.assertEqual(self.feed(reading({"T5": 5.0}), 3), [])

    def test_flicker_is_reported_once(self):
        self.feed(reading({"T5": 5.0, "T10": 2.0}), 2)
        events = []
        for i in range(24):  # 6 s of a wobbling leg
            self.clock.t += 0.25
            e = self.live.feed(reading({"T5": 5.0, "T10": 2.0} if i % 2 else {"T5": 5.0}))
            if e:
                events.append(e)
        self.assertEqual(events, [{"kind": "flicker", "rows": ["T10"]}])

    def test_nothing_while_power_is_off(self):
        self.feed(reading({"T5": 5.0}), 2)
        self.assertEqual(self.feed(reading({}, power=False), 3), [])
        self.assertEqual(self.feed(reading({"T5": 5.0, "T10": 2.0}), 3), [])  # new starting point


class InstantApp(unittest.TestCase):
    def make(self, scenario="correct", lesson=1, instant=True, model=None, built=0, **opts):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = FakeBoard(lesson, scenario, seed=1, **opts)
        self.app = App(self.board, lesson=lesson, history_path=Path(self.tmp.name) / "h.json",
                       model=model or ScriptedTutorModel(), instant=instant)
        self.app.instant_inline = self.app.auto_inline = True
        self.clock = Clock()
        self.app.live.clock = self.clock
        if built is not None:
            self.board.send(f"BUILD {built}")
        self.settle()
        return self.app

    def settle(self, seconds=2.5):
        for _ in range(int(seconds / 0.25)):
            self.clock.t += 0.25
            self.app.instant_tick()

    def step(self, command):
        self.assertEqual(self.board.send(command)["type"], "ack", command)
        self.settle()

    def said(self, who="tutor"):
        return [m for m in self.app.log if m["who"] == who]


class TestBuilding(InstantApp):
    def test_part_by_part_correct_is_quiet_until_complete(self):
        self.make()
        for n in (1, 2, 3):
            self.step(f"BUILD {n}")
            self.assertEqual(self.app.log, [], n)  # no chat while building
        self.assertEqual(self.app.building, {"parts_in": 2, "parts": 4})
        self.assertEqual(self.app.state()["building"], {"parts_in": 2, "parts": 4})
        self.step("BUILD 4")
        replies = self.said()
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0]["auto"])
        self.assertIn("Your circuit is correct!", replies[0]["text"])
        self.assertEqual(self.app.session.lesson_id, 2)  # passed, by the normal rules

    def test_bypassed_led_is_said_once_current_flows(self):
        self.make("led_bypassed")
        self.step("BUILD 2")
        self.step("BUILD 3")  # the LED goes in, both legs joined, but the black wire
        self.assertEqual(self.app.log, [])  # is not: 0 mA, so its 0 V says nothing
        # Only the guide's blue "put a part here" lights: no red or yellow.
        self.assertEqual(set(self.board.lights.values()) - {"blue"}, set())
        self.assertEqual(self.app.building, {"parts_in": 3, "parts": 4})
        self.step("BUILD 4")  # now current flows around the LED
        first = self.said()[0]
        self.assertTrue(first["instant"])
        self.assertIn("while current flows: the current goes around it", first["text"])
        self.assertEqual(self.app.instant_mistake["rows"], ["T10", "T12"])
        self.assertTrue(self.said()[1]["auto"])  # then Zip explains, with a normal check

    def test_stray_part_mid_build_is_said_at_once(self):
        self.make("stray_part")
        self.step("BUILD 1")
        self.assertEqual(self.app.log, [])
        self.step("BUILD 2")
        first = self.said()[0]
        self.assertEqual((first["instant"], first["text"]),
                         (True, "Something is connected in row 30, which is not in the plan."))
        self.assertIn("Something is plugged into row 30", self.said()[1]["text"])

    def test_fixed(self):
        self.make("stray_part")
        self.step("BUILD 2")
        n = len(self.app.log)
        self.step("SCENARIO 1 correct")  # the stray part comes out
        self.assertEqual(self.said()[-1]["text"], "That fixed it.")
        self.assertEqual(len(self.app.log), n + 1)  # no AI call: still building
        self.assertIsNone(self.app.instant_mistake)
        self.assertEqual(self.board.lights, {})

    def test_fixed_and_complete_passes(self):
        self.make("led_bypassed", built=None)
        self.app.log.clear()
        self.app.instant_mistake = {"rows": ["T10", "T12"], "text": "x"}
        self.step("SCENARIO 1 correct")
        texts = [m["text"] for m in self.said()]
        self.assertEqual(texts[0], "That fixed it.")
        self.assertIn("Your circuit is correct!", texts[1])

    def test_instant_off_says_nothing(self):
        self.make("stray_part", instant=False)
        for n in (1, 2, 3, 4):
            self.step(f"BUILD {n}")
        self.assertEqual(self.app.log, [])
        self.assertIsNone(self.app.state()["building"])
        self.assertFalse(self.app.state()["instant"])

    def test_the_instant_check_changes_no_lesson_state(self):
        self.make("led_backwards")
        self.app.trigger_auto = lambda: None  # only the instant layer
        self.step("BUILD 4")
        self.assertEqual(self.said()[0]["instant"], True)
        s = self.app.session
        self.assertEqual(s.history["mistakes"], [])  # nothing logged
        self.assertFalse(s.passed_current)
        self.assertIsNone(s.last_check)

    def test_a_dangerous_circuit_is_cut_at_once(self):
        # No current sensor: the laptop sees it from the rows and cuts at once.
        self.make("no_resistor", has_current=False)
        self.step("BUILD 4")
        self.assertFalse(self.board.power_on)
        self.assertTrue(self.said()[0]["text"].startswith(
            "I switched the power off to keep your parts safe."))
        self.assertEqual([m for m in self.app.log if m.get("auto")], [])  # no AI call

    def test_the_boards_own_cut_is_left_to_the_normal_check(self):
        # With a current sensor the board cuts by itself: the power is off, so
        # instant help stays quiet; the next check explains the board's cut.
        self.make("no_resistor")
        self.step("BUILD 4")
        self.assertFalse(self.board.power_on)
        self.assertEqual(self.app.log, [])

    def test_switch_and_state(self):
        self.make(instant=False)
        self.assertEqual(self.app.set_instant(True), {"ok": True, "instant": True})
        self.assertTrue(self.app.state()["instant"])
        self.assertEqual(self.app.build("correct", parts=2)["type"], "ack")
        self.assertEqual(self.board.built, 2)
        json.dumps(self.app.state())


class TestFixesFromTheRealTest(InstantApp):
    """PR #19 real test: pass cascade, mid-build focus, empty board, count."""

    def build_up(self, upto):
        for n in range(1, upto + 1):
            self.step(f"BUILD {n}")

    def test_no_pass_cascade(self):
        self.make()
        self.build_up(4)
        self.assertEqual(self.app.session.lesson_id, 2)  # lesson 1 passed (one automatic check)
        self.assertEqual(self.board.built, 0)  # the new lesson starts on an empty board
        self.settle(10)  # nobody touches the board
        self.assertEqual(self.app.session.lesson_id, 2)
        self.assertEqual(len(self.said()), 1)

    def test_guard_never_passes_two_lessons_without_a_board_change(self):
        self.make()
        self.build_up(4)
        self.assertEqual(self.app.session.lesson_id, 2)
        self.board.send("BUILD ALL")  # lesson 2's correct circuit, but no steady change seen
        self.app.trigger_auto()
        self.assertEqual(self.app.session.lesson_id, 2)  # the guard: no second pass
        self.assertEqual(len(self.said()), 1)
        self.settle()  # (the board as it is now: the silent starting point)
        self.step("BUILD 0")  # the student clears it and builds lesson 2:
        self.build_up(4)      # real board changes, so it is checked as normal
        self.assertEqual(self.app.session.lesson_id, 3)

    def test_a_real_board_keeps_its_old_circuit_after_a_pass(self):
        self.make(built=None)
        self.app.ask("Check my circuit")  # passed by hand: on to lesson 2
        self.assertEqual(self.app.session.lesson_id, 2)
        self.board.send("BUILD ALL")  # like a real board: the old wires are still in
        self.settle(10)
        self.assertEqual(self.app.session.lesson_id, 2)  # the new starting point, silently
        self.assertEqual(len(self.said()), 1)

    def test_the_fake_board_starts_empty_only_with_instant_help(self):
        self.make()
        self.build_up(4)
        self.assertEqual(self.board.built, 0)
        self.make(instant=False, built=None)
        self.app.ask("Check my circuit")
        self.assertIsNone(self.board.built)  # without instant help: a correct build, as before
        self.assertEqual(self.app.build("correct")["type"], "ack")
        self.assertIsNone(self.board.built)  # a practice circuit is the whole circuit

    def test_mid_build_the_automatic_check_has_the_trigger_and_no_red_on_empty_rows(self):
        self.make("stray_part")
        self.build_up(2)
        result = self.app.session.last_check[1]
        self.assertEqual(result["instant_trigger"]["fact"], "extra_rows")
        self.assertEqual(result["instant_trigger"]["rows"], ["T30"])
        self.assertEqual(result["building"], {"parts_in": 2, "parts": 4})
        self.assertEqual(self.board.lights, {"T30": "yellow"})
        self.assertIsNone(self.app.session.instant_context)  # only for that check

    def test_the_light_tool_skips_empty_plan_rows_mid_build(self):
        from agent.tools import make_tools
        self.make("stray_part")
        self.build_up(2)
        s = self.app.session
        s.measure()
        s.instant_context = {"trigger": {"rows": ["T30"]}, "building": {"parts_in": 2, "parts": 4},
                             "empty_rows": ["T12"]}
        light_row = next(t for t in make_tools(s) if t.__name__ == "light_row")
        self.board.send("LED CLEAR")
        light_row(["T5", "T10", "T12", "T30"], "red")
        self.assertEqual(self.board.lights, {"T30": "red"})  # only the trigger's rows
        light_row(["T12"], "blue")  # "put a part here" is always fine
        self.assertEqual(self.board.lights["T12"], "blue")
        s.instant_context = None
        light_row(["T12"], "red")  # a normal check lights what it asks for
        self.assertEqual(self.board.lights["T12"], "red")

    def test_an_empty_board_is_quiet_and_resets(self):
        self.make("stray_part")
        self.build_up(2)
        self.assertIsNotNone(self.app.instant_mistake)
        n = len(self.app.log)
        self.step("BUILD 0")  # the board is cleared
        self.assertEqual(len(self.app.log), n)  # no "That fixed it."
        self.assertIsNone(self.app.instant_mistake)
        self.assertEqual(self.board.lights, {})

    def test_that_fixed_it_needs_a_plan_row_still_in(self):
        self.make("stray_part")
        self.build_up(2)
        self.step("SCENARIO 1 correct")
        self.assertEqual(self.said()[-1]["text"], "That fixed it.")

    def test_bypassed_led_mid_build_is_not_a_finished_build(self):
        self.make("led_bypassed")
        self.build_up(3)  # the black wire is not in yet
        self.assertEqual(self.app.building, {"parts_in": 3, "parts": 4})
        self.assertNotIn("Your circuit is correct", " ".join(m["text"] for m in self.said()))

    def test_prompt_rules(self):
        for needed in ('"instant_trigger"', "Explain that trigger first", '"when you add the ..."',
                       "never light those empty"):
            self.assertIn(needed, SYSTEM_PROMPT)


class TestNoCurrentNoShort(InstantApp):
    """A 0 V drop means nothing while no current flows (real test after PR #20)."""

    def facts(self, scenario, built=None, **opts):
        from agent.lessons import load_lessons, summarize
        from agent.physics import observations
        board = FakeBoard(1, scenario, seed=1, noise=False, **opts)
        if built is not None:
            board.send(f"BUILD {built}")
        lesson = load_lessons()[1]
        return observations(summarize([board.read() for _ in range(5)], lesson), lesson)

    def kind(self, facts, kind):
        return [f for f in facts if f["kind"] == kind]

    def test_led_with_no_current_is_not_shorted(self):
        led = self.kind(self.facts("led_bypassed", built=3), "led_state")[0]
        self.assertEqual(led["values"]["state"], "no_current")
        self.assertIn("No current flows yet", led["text"])
        led = self.kind(self.facts("led_bypassed"), "led_state")[0]  # all in: 22.7 mA
        self.assertEqual(led["values"]["state"], "shorted")

    def test_same_voltage_with_no_current_is_not_joined(self):
        same = self.kind(self.facts("led_bypassed", built=3), "same_voltage")
        self.assertTrue(same)
        self.assertTrue(all(not f["values"]["joined"] for f in same))
        self.assertTrue(all("tells nothing" in f["text"] for f in same))
        same = self.kind(self.facts("led_backwards"), "same_voltage")  # rows 5 and 10, 0 mA
        self.assertEqual([f["values"]["joined"] for f in same], [False])
        joined = self.kind(self.facts("led_bypassed"), "same_voltage")
        self.assertEqual([(f["rows"], f["values"]["joined"]) for f in joined],
                         [(["T10", "T12"], True)])
        # No sensor, but the + line is pulled down: a big current flows anyway.
        pulled = self.kind(self.facts("no_resistor", has_current=False), "same_voltage")
        self.assertTrue(all(f["values"]["joined"] for f in pulled))

    def test_not_a_mistake_now_for_instant_help(self):
        lesson = {"rows": ["T5", "T10", "T12"], "layout": []}
        summary = {"rows": {"T5": 5.0, "T10": 5.0, "T12": 5.0}, "floating": [], "current_ma": 0.0}
        for fact in ({"kind": "led_state", "rows": ["T10", "T12"], "values": {"state": "no_current"},
                      "text": "x"},
                     {"kind": "same_voltage", "rows": ["T5", "T12"], "values": {"joined": False},
                      "text": "x"}):
            self.assertNotEqual(classify({"status": "unknown"}, summary, [fact], lesson)["kind"],
                                "mistake")

    def test_automatic_check_while_building_lights_only_the_trigger(self):
        self.make("stray_part")
        for n in (1, 2):
            self.step(f"BUILD {n}")
        self.assertEqual(self.board.lights, {"T30": "yellow"})  # not rows 5, 10 or 12


class SlowTutor(ScriptedTutorModel):
    """The offline tutor, slower (like a real model), to overlap checks."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        time.sleep(0.05)
        return super()._generate(messages, stop, run_manager, **kwargs)


class TestOneAtATime(InstantApp):
    def test_a_manual_check_during_an_automatic_one_gives_one_reply(self):
        self.make(model=SlowTutor(), built=None)
        self.app.auto_inline = False
        self.app.trigger_auto()
        time.sleep(0.05)
        out = self.app.ask("Check my circuit")
        self.assertTrue(out.get("merged"))
        self.app.wait_for_auto()
        self.assertEqual(len(self.said()), 1)  # one reply, not two
        self.assertEqual(len(self.said("student")), 1)

    def test_an_automatic_check_while_the_student_checks_is_skipped(self):
        self.make(built=None)
        with self.app.agent_lock:  # the student's own check is running
            self.app.trigger_auto()
        self.assertEqual(self.app.log, [])

    def test_a_change_during_an_automatic_check_runs_it_once_more(self):
        # (A circuit that does not pass: a passing one must not run again on
        # the same board, see TestNoPassCascade.)
        self.make("led_backwards", model=SlowTutor(), built=None)
        self.app.auto_inline = False
        runs = []
        real_turn = self.app._turn
        self.app._turn = lambda *a, **k: runs.append(1) or real_turn(*a, **k)
        self.app.trigger_auto()
        self.app.trigger_auto()  # the board changed while it ran
        self.app.trigger_auto()  # (twice: still only once more)
        self.app.wait_for_auto()
        self.assertEqual(len(runs), 2)


class CountingDetective(ScriptedDetectiveModel):
    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        return super()._generate(messages, stop, run_manager, **kwargs)


class TinyDiagnoser(BaseChatModel):
    """A diagnoser that always answers (the facts are not needed here)."""
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "tiny-diagnoser"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        answer = {"status": "unsure", "causes": [], "question": "Is the resistor red, red, brown?"}
        msg = AIMessage(content="", tool_calls=[{"name": "Diagnosis", "args": answer, "id": "d"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


class TestNoDoubleCall(unittest.TestCase):
    def run_check(self, with_diagnoser):
        board = FakeBoard(1, "resistor_150", seed=1)
        session = Session(board, lesson=1)
        detective = CountingDetective()
        agent = build_agent(session, ScriptedTutorModel(), detective_model=detective,
                            diagnoser_model=TinyDiagnoser() if with_diagnoser else None)
        reply, _ = ask(agent, [], "Check my circuit")
        return session, detective, reply

    def test_detective_not_called_when_a_diagnosis_exists(self):
        session, detective, reply = self.run_check(True)
        self.assertEqual(session.last_check[1]["status"], "unknown")
        self.assertIn("diagnosis", session.last_check[1])
        self.assertEqual(detective.calls, 0)
        self.assertIn("Did not ask the detective: the diagnosis already explains it",
                      [s["text"] for s in describe_steps(session.trace)])

    def test_detective_still_used_without_a_diagnosis(self):
        _, detective, _ = self.run_check(False)
        self.assertGreater(detective.calls, 0)

    def test_prompt_rule(self):
        self.assertIn('NEVER when lesson_check has\n     "diagnosis"', SYSTEM_PROMPT)
        self.assertTrue(ALREADY_DIAGNOSED.startswith("Already diagnosed: use the diagnosis"))


class TestClassify(unittest.TestCase):
    LESSON = {"rows": ["T5", "T10", "T12"], "layout": [
        {"part": "Red wire", "legs": [{"row": "PT+"}, {"row": "T5"}]},
        {"part": "220 ohm resistor", "legs": [{"row": "T5"}, {"row": "T10"}]},
        {"part": "Red LED", "legs": [{"row": "T10"}, {"row": "T12"}]},
        {"part": "Black wire", "legs": [{"row": "T12"}, {"row": "PT-"}]}]}

    def summary(self, rows, current=0.0):
        # No "supply_v": the + line counts as the station's 3.3 V.
        return {"rows": {r: 3.3 for r in self.LESSON["rows"]}, "current_ma": current,
                "floating": [r for r in self.LESSON["rows"] if r not in rows]}

    def test_same_voltage_mid_build_is_normal_without_current(self):
        # agent/physics.py marks same_voltage "joined" only when current flows
        fact = {"kind": "same_voltage", "rows": ["T5", "T10"], "values": {"joined": False},
                "text": "same"}
        out = classify({"status": "unknown"}, self.summary(["T5", "T10"]), [fact], self.LESSON)
        self.assertEqual((out["kind"], out["parts_in"], out["parts"]), ("building", 2, 4))
        joined = dict(fact, values={"joined": True})
        out = classify({"status": "unknown"}, self.summary(["T5", "T10", "T12"], 20.0), [joined],
                       self.LESSON)
        self.assertEqual(out["kind"], "mistake")

    def test_far_too_much_current_and_complete(self):
        far = {"kind": "current_vs_expected", "rows": [], "values": {"percent": 80}, "text": "far"}
        self.assertEqual(classify({}, self.summary(["T5"]), [far], self.LESSON)["kind"], "mistake")
        self.assertEqual(classify({"status": "ok"}, self.summary(["T5", "T10", "T12"]), [],
                                  self.LESSON)["kind"], "complete")


class TestFakeBoardAndPage(unittest.TestCase):
    def test_build_command_and_parts_match_the_lessons(self):
        from agent.lessons import load_lessons
        for lid, lesson in load_lessons().items():
            self.assertEqual([(p[1], p[2]) for p in PARTS[lid]],
                             [(p["legs"][0]["row"], p["legs"][1]["row"]) for p in lesson["layout"]])
        board = FakeBoard(1, "correct", seed=1)
        self.assertEqual(board.send("BUILD 2")["type"], "ack")
        r = board.read()
        self.assertIn("T12", r["floating"])
        self.assertNotIn("T10", r["floating"])
        self.assertEqual(r["current_ma"] < 0.1, True)
        self.assertEqual(board.send("BUILD x")["type"], "error")
        self.assertEqual(board.send("BUILD ALL")["cmd"], "BUILD ALL")

    def test_page(self):
        for needed in ('id="instant-toggle"', 'post("/api/instant"', ">instant</span>",
                       'id="build-progress"', "parts in`", '" (instant)"'):
            self.assertIn(needed, HTML)
        server = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"--no-instant"', server)
        self.assertIn("instant=not args.no_instant", server)


if __name__ == "__main__":
    unittest.main()
