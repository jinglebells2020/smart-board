"""Tests for Part 3: the laptop side of protocol version 2.

The board's open questions (how many rows, current sensor, power switch,
screen) can have any answer, so the whole tutor is run on boards with
different answers: no current sensor, no power switch, only 16 rows.
"""

import json
import unittest

import protocol as P
from agent.boardlink import BoardLink
from agent.build import ask, build_agent
from agent.buttons import handle_button
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from agent.tools import make_tools
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard

LESSONS = load_lessons()


def run(lesson, scenario, text="Check my circuit", history=None, **board_options):
    board = FakeBoard(lesson, scenario, seed=5, **board_options)
    session = Session(board, lesson=lesson)
    agent = build_agent(session, ScriptedTutorModel())
    reply, new = ask(agent, history if history is not None else [], text)
    calls = [(c["name"], c["args"]) for m in new for c in (getattr(m, "tool_calls", None) or [])]
    return board, session, agent, reply, calls


def mistake(lid, mid):
    return next(m for m in LESSONS[lid]["mistakes"] if m["id"] == mid)


class RecordingBoard(FakeBoard):
    """A fake board that remembers every command it was sent."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = []

    def send(self, line):
        self.sent.append(line)
        return super().send(line)


class TestBoardLink(unittest.TestCase):
    def test_needs_protocol_2(self):
        class OldBoard(FakeBoard):
            def hello(self):
                return dict(super().hello(), protocol=1)
        with self.assertRaises(ValueError):
            BoardLink(OldBoard())

    def test_capabilities(self):
        link = BoardLink(FakeBoard(rows="T1-T16", has_current=False, has_power_switch=False))
        self.assertEqual(link.capabilities(), {"rows": 16, "has_current": False,
                                               "has_power_switch": False, "has_screen": True,
                                               "buttons": 4})

    def test_lights_are_checked_before_sending(self):
        board = RecordingBoard()
        link = BoardLink(board)
        self.assertEqual(link.set_light("T10", "red")["type"], "ack")
        self.assertEqual(link.set_light("T10", "purple")["type"], "error")
        self.assertEqual(link.set_light("PT+", "red")["type"], "error")
        self.assertEqual(board.sent, ["LED T10 red"])  # the bad ones were never sent
        self.assertEqual(link.lights, {"T10": "red"})
        link.set_light("T10", "off")
        self.assertEqual(link.lights, {})

    def test_screen_text_always_fits(self):
        board = RecordingBoard()
        link = BoardLink(board)
        answer = link.show("This is a very long message that could never fit on a small "
                           "screen of four lines with twenty characters each, ever.")
        self.assertEqual(answer["type"], "ack")
        lines = board.sent[-1][len("SHOW "):].split("|")
        self.assertLessEqual(len(lines), P.SCREEN_LINES)
        self.assertTrue(all(len(l) <= P.SCREEN_CHARS for l in lines))
        self.assertEqual(link.screen, lines)

    def test_no_screen(self):
        board = RecordingBoard(has_screen=False)
        self.assertEqual(BoardLink(board).show("hi")["msg"], "no screen")
        self.assertEqual(board.sent, [])


class TestButtons(unittest.TestCase):
    def test_one_place_for_the_mapping(self):
        asked = []
        session = Session(FakeBoard(), lesson=1)
        tutor = lambda text: asked.append(text) or "ok"
        self.assertEqual(handle_button(1, session, tutor)["action"], "check")
        self.assertEqual(handle_button(2, session, tutor)["action"], "hint")
        self.assertEqual(asked, ["Check my circuit", "I need a hint"])
        self.assertEqual(handle_button(3, session, tutor)["action"], "next_step")
        self.assertEqual(handle_button(4, session, tutor)["action"], "power")
        self.assertIn("error", handle_button(9, session, tutor))
        self.assertEqual(asked, ["Check my circuit", "I need a hint"])  # 3, 4: no AI

    def test_button_events_come_from_the_board(self):
        board = FakeBoard()
        board.send("PRESS 2")
        self.assertEqual(BoardLink(board).events(), [{"type": "button", "id": 2}])


class TestBuildingStepByStep(unittest.TestCase):
    def test_each_step_lights_its_rows_blue_and_names_the_part(self):
        for lid, lesson in LESSONS.items():
            board = FakeBoard(lid)
            session = Session(board, lesson=lid)
            for i, part in enumerate(lesson["layout"]):
                with self.subTest(lesson=lid, step=i + 1):
                    info = session.next_step()
                    rows = [l["row"] for l in part["legs"] if P.is_row(l["row"])]
                    self.assertEqual((info["step"], info["part"]), (i + 1, part["part"]))
                    self.assertEqual(board.lights, {r: "blue" for r in rows})
                    self.assertEqual(board.screen[0], f"Step {i + 1} of {len(lesson['layout'])}")
                    self.assertEqual(board.screen[1], part["part"])
                    self.assertLessEqual(len(board.screen), P.SCREEN_LINES)
            done = session.next_step()
            self.assertTrue(done["done"])
            self.assertEqual(board.lights, {})
            self.assertIn("Press 1", " ".join(board.screen))

    def test_led_step_says_which_leg_goes_where(self):
        session = Session(FakeBoard(1), lesson=1)
        for _ in range(3):
            info = session.next_step()
        self.assertEqual(info["part"], "Red LED")
        self.assertIn("long leg: row 10", session.board.screen)
        self.assertIn("short leg: row 12", session.board.screen)

    def test_new_lesson_starts_from_step_one(self):
        session = Session(FakeBoard(1), lesson=1)
        session.next_step()
        session.set_lesson(2)
        self.assertEqual(session.next_step()["step"], 1)


class TestNoCurrentSensor(unittest.TestCase):
    OPTS = {"has_current": False}

    def test_every_mistake_except_resistor_too_big_is_still_found(self):
        for lid, scenarios in SCENARIOS.items():
            for scenario in scenarios:
                if scenario == "correct" or scenario in UNLISTED[lid] or scenario == "resistor_too_big":
                    continue
                with self.subTest(lesson=lid, mistake=scenario):
                    board, session, _, reply, calls = run(lid, scenario, **self.OPTS)
                    self.assertIn(("log_mistake", {"mistake_id": scenario}), calls)
                    self.assertIn(mistake(lid, scenario)["hint"], reply)

    def test_read_current_says_calculated(self):
        tools = {f.__name__: f for f in make_tools(Session(FakeBoard(1, "correct", seed=1,
                                                                     **self.OPTS), lesson=1))}
        out = json.loads(tools["read_current"]())
        self.assertEqual(out["current_source"], "calculated")
        self.assertAlmostEqual(out["current_ma"], 13.6, delta=0.3)

    def test_says_plainly_what_it_could_not_check(self):
        _, _, _, reply, _ = run(1, "resistor_too_big", **self.OPTS)
        self.assertIn("correct", reply)
        self.assertIn("cannot measure current", reply)


class TestNoPowerSwitch(unittest.TestCase):
    OPTS = {"has_power_switch": False}

    def test_danger_tells_the_student_to_switch_off_by_hand(self):
        board, session, _, reply, calls = run(1, "no_resistor", **self.OPTS)
        self.assertTrue(reply.startswith("Switch the power off by hand"))
        self.assertIn(mistake(1, "no_resistor")["hint"], reply)
        screen = " ".join(board.screen)
        self.assertIn("Switch the power OFF", screen)
        self.assertIn("by hand", screen)
        self.assertTrue(board.power_on)  # nothing could switch it
        self.assertEqual(board.lights, {"T5": "red", "T10": "red"})

    def test_session_puts_it_on_the_screen_without_the_ai(self):
        board = FakeBoard(1, "no_resistor", seed=1, **self.OPTS)
        session = Session(board, lesson=1)
        _, result, safety_cut = session.measure()
        self.assertTrue(result["manual_power_off"])
        self.assertFalse(safety_cut)
        self.assertIn("by hand", " ".join(board.screen))

    def test_cut_tool_says_what_to_do_instead(self):
        tools = {f.__name__: f for f in make_tools(Session(FakeBoard(**self.OPTS)))}
        out = json.loads(tools["cut_power"]())
        self.assertEqual(out["msg"], "no power switch")
        self.assertIn("by hand", out["do_instead"])

    def test_power_button_explains(self):
        board = FakeBoard(**self.OPTS)
        result = handle_button(4, Session(board), lambda t: "")
        self.assertEqual(result["error"], "no power switch")
        self.assertIn("No power switch.", board.screen)

    def test_with_a_switch_power_is_cut(self):
        board, _, _, reply, _ = run(1, "no_resistor")
        self.assertFalse(board.power_on)
        self.assertTrue(reply.startswith("I cut the power"))


class TestSixteenRows(unittest.TestCase):
    OPTS = {"rows": "T1-T16"}

    def test_both_lessons_work(self):
        for lid in LESSONS:
            with self.subTest(lesson=lid):
                _, session, _, reply, _ = run(lid, "correct", **self.OPTS)
                self.assertIn("correct", reply)
        board, _, _, reply, calls = run(2, "top_1k", **self.OPTS)
        self.assertIn(("log_mistake", {"mistake_id": "top_1k"}), calls)

    def test_without_sensor_and_switch_too(self):
        board, _, _, reply, _ = run(1, "led_bypassed", rows="T1-T16", has_current=False,
                                    has_power_switch=False)
        self.assertIn(mistake(1, "led_bypassed")["hint"], reply)

    def test_rows_the_board_does_not_measure(self):
        board, session, _, reply, calls = run(1, "led_backwards", rows="T1-T8")
        self.assertIn("does not measure T10, T12", reply)
        self.assertNotIn("log_mistake", [n for n, _ in calls])
        self.assertEqual(session.history["mistakes"], [])


class TestColorsAndScreen(unittest.TestCase):
    def test_tutor_uses_colors_by_their_meaning(self):
        board, session, _, _, calls = run(1, "correct")
        self.assertIn(("light_row", {"rows": LESSONS[1]["rows"], "color": "green"}), calls)
        # ...then the lesson moves on, and a new lesson starts with dark lights.
        self.assertEqual(session.lesson_id, 2)
        self.assertEqual(board.lights, {})
        board, *_ = run(1, "open_circuit")
        self.assertEqual(board.lights, {"T10": "red"})
        board, *_ = run(2, "bottom_4k7")
        self.assertEqual(set(board.lights.values()), {"yellow"})

    def test_light_tool_rejects_other_colors(self):
        tools = {f.__name__: f for f in make_tools(Session(FakeBoard()))}
        self.assertIn("error", json.loads(tools["light_row"](["T10"], "purple"))["type"])
        out = json.loads(tools["light_row"]("T10, T12", "blue"))
        self.assertEqual([a["type"] for a in out], ["ack", "ack"])

    def test_tutor_never_sends_too_much_text(self):
        for lid, scenarios in SCENARIOS.items():
            for scenario in scenarios:
                for opts in ({}, {"has_power_switch": False}, {"rows": "T1-T8"}):
                    with self.subTest(lesson=lid, scenario=scenario, opts=opts):
                        board = RecordingBoard(lid, scenario, seed=1, **opts)
                        agent = build_agent(Session(board, lesson=lid), ScriptedTutorModel())
                        ask(agent, [], "Check my circuit")
                        for cmd in board.sent:
                            if cmd.startswith("SHOW "):
                                lines = cmd[5:].split("|")
                                self.assertLessEqual(len(lines), P.SCREEN_LINES, cmd)
                                self.assertTrue(all(len(l) <= P.SCREEN_CHARS for l in lines), cmd)


class TestWholeDemoOffline(unittest.TestCase):
    def test_demo(self):
        board = FakeBoard(1, "correct", seed=9)
        session = Session(board, lesson=1)
        agent = build_agent(session, ScriptedTutorModel())
        history = []
        tutor = lambda text: ask(agent, history, text)[0]
        for _ in range(4):
            handle_button(3, session, tutor)           # build step by step
        board.send("SCENARIO 1 led_backwards")
        first = handle_button(1, session, tutor)["reply"]
        second = handle_button(2, session, tutor)["reply"]
        self.assertIn(mistake(1, "led_backwards")["hint"], first)
        self.assertIn(mistake(1, "led_backwards")["explain"], second)
        board.send("SCENARIO 1 no_resistor")
        self.assertIn("cut the power", handle_button(1, session, tutor)["reply"])
        board.send("SCENARIO 1 correct")
        reply = tutor("I fixed it")
        self.assertIn("correct", reply)
        self.assertEqual(session.lesson_id, 1)  # same mistake twice: practice again
        self.assertIn("correct", handle_button(1, session, tutor)["reply"])
        self.assertEqual(session.lesson_id, 2)


if __name__ == "__main__":
    unittest.main()
