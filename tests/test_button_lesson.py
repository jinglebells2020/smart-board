"""Lesson 4 "Button switch": a guided lesson in two steps. Hold the button
and check (current flows, the LED lights), then let go and check (nothing
flows). Each mistake is found at its own step. Lessons 0-3 are unchanged."""

import json
import tempfile
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.fallback import STATE
from agent.lesson_writer import ALT_LESSONS, check_lesson
from agent.lessons import SAFETY_MAX_MA, check, load_lessons, summarize
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from protocol import FIRMWARE_CUT_MA
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
L4 = LESSONS[4]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
PRESS = "Press and hold the button, then check."
LET_GO = "Now let go of the button and check again."


def board_and_session(scenario="correct", **opts):
    board = FakeBoard(4, scenario, seed=1, **opts)
    return board, Session(board, lesson=4)


def check_with(board, session, hold):
    board.send("HOLD " + ("on" if hold else "off"))
    return session.measure()[1]


class TestLessonFile(unittest.TestCase):
    def test_rows_layout_and_steps(self):
        self.assertEqual((L4["id"], L4["title"]), (4, "Button switch"))
        self.assertEqual(L4["circuit"], "3.3 V -> push button -> 220 ohm resistor -> red LED -> ground")
        self.assertEqual(L4["rows"], ["T5", "T8", "T13", "T15"])
        legs = {p["part"]: [leg["row"] for leg in p["legs"]] for p in L4["layout"]}
        self.assertEqual(legs, {"Red wire": ["PT+", "T5"], "Push button": ["T5", "T8"],
                                "220 ohm resistor": ["T8", "T13"], "Red LED": ["T13", "T15"],
                                "Black wire": ["T15", "PT-"]})
        pressed, released = L4["states"]["pressed"], L4["states"]["released"]
        self.assertEqual(pressed["accept"]["current_ma"], {"min": 4.0, "max": 7.5})
        self.assertEqual(pressed["expected"]["current_ma"], 5.9)
        self.assertEqual(pressed["expected"]["math"], "(3.3 - 2) / 220 = 0.0059 A = 5.9 mA")
        self.assertEqual(released["accept"]["current_ma"], {"max": 1})
        self.assertNotIn("T8", released["accept"]["rows"])  # may be empty when let go
        self.assertEqual({m["id"]: m.get("step") for m in L4["mistakes"]},
                         {"button_always_on": "released", "button_not_connecting": "pressed",
                          "led_backwards": "pressed", "no_resistor": None})

    def test_texts_follow_the_rules(self):
        self.assertIn("A button is like a drawbridge: down, cars cross; up, the road is broken.",
                      L4["intro_lesson"]["idea"])
        for ml in [L4["intro_lesson"]] + [m["mini_lesson"] for m in L4["mistakes"]]:
            with self.subTest(title=ml["title"]):
                self.assertEqual([g for _, ok, g in check_lesson(ml) if not ok], [])
        for m in L4["mistakes"]:
            with self.subTest(mistake=m["id"]):
                for key in ("description", "hint", "explain"):
                    self.assertTrue(m[key])
                self.assertNotIn("quarter turn", m["hint"])  # the hint never gives the fix
                self.assertIn(m["id"], ALT_LESSONS)  # the lesson writer has one too

    def test_safety_limits_did_not_change(self):
        self.assertEqual(SAFETY_MAX_MA, 30.0)
        self.assertEqual(FIRMWARE_CUT_MA, 50)


class TestSteps(unittest.TestCase):
    def test_pressed_then_released_passes(self):
        board, session = board_and_session()
        self.assertEqual(session.button_step, "pressed")
        result = check_with(board, session, True)
        self.assertEqual((result["status"], result["step"], result["next_step"]),
                         ("step_ok", "pressed", LET_GO))
        self.assertFalse(session.passed_current)
        self.assertEqual(session.button_step, "released")
        result = check_with(board, session, False)
        self.assertEqual((result["status"], result["step"]), ("ok", "released"))
        self.assertTrue(session.passed_current)
        self.assertEqual(session.history["mistakes"], [])

    def test_released_first_asks_to_press_first(self):
        board, session = board_and_session()
        result = check_with(board, session, False)
        self.assertEqual((result["status"], result["next_step"]), ("press_first", PRESS))
        self.assertFalse(session.passed_current)
        self.assertEqual(session.history["mistakes"], [])  # not a mistake
        self.assertEqual(session.button_step, "pressed")

    def test_always_on_is_caught_when_let_go(self):
        board, session = board_and_session("button_always_on")
        self.assertEqual(check_with(board, session, True)["status"], "step_ok")
        result = check_with(board, session, False)
        self.assertEqual((result["mistake_id"], result["step"], result["rows"]),
                         ("button_always_on", "released", ["T5", "T8"]))
        self.assertEqual(session.button_step, "released")  # a mistake keeps the step
        self.assertFalse(session.passed_current)

    def test_not_connecting_is_caught_at_the_pressed_step(self):
        board, session = board_and_session("button_not_connecting")
        self.assertEqual(check_with(board, session, True)["status"], "press_first")
        result = check_with(board, session, True)  # still nothing while held
        self.assertEqual((result["mistake_id"], result["step"]), ("button_not_connecting", "pressed"))
        self.assertEqual(session.button_step, "pressed")

    def test_led_backwards_is_caught(self):
        board, session = board_and_session("led_backwards")
        result = check_with(board, session, True)
        self.assertEqual((result["mistake_id"], result["rows"]), ("led_backwards", ["T13", "T15"]))
        self.assertEqual(session.button_step, "pressed")

    def test_no_resistor_cuts_the_power(self):
        for has_current in (True, False):
            with self.subTest(has_current=has_current):
                board, session = board_and_session("no_resistor", has_current=has_current)
                board.send("HOLD on")
                _, result, cut = session.measure()
                self.assertTrue(cut)
                self.assertFalse(board.power_on)
                if not has_current:  # found from the rows by the laptop
                    self.assertEqual((result["mistake_id"], result["action"]),
                                     ("no_resistor", "cut_power"))

    def test_every_mistake_at_its_step_with_noise_and_both_sensor_options(self):
        steps = {"button_always_on": [True, False], "button_not_connecting": [True, True],
                 "led_backwards": [True], "no_resistor": [True]}
        for scenario, holds in steps.items():
            for has_current in (True, False):
                for seed in range(4):
                    with self.subTest(scenario=scenario, current=has_current, seed=seed):
                        board = FakeBoard(4, scenario, seed=seed, has_current=has_current)
                        session = Session(board, lesson=4)
                        for hold in holds:
                            result = check_with(board, session, hold)
                        self.assertEqual(result["mistake_id"], scenario)

    def test_the_lesson_files_own_rules_are_the_pressed_step(self):
        board = FakeBoard(4, "correct", seed=1)
        board.send("HOLD on")
        s = summarize([board.read() for _ in range(5)], L4)
        self.assertEqual(check(s, L4)["status"], "ok")  # button_always_on is not looked for


class TestPlacement(unittest.TestCase):
    def test_placement_only_from_a_pressed_check(self):
        board, session = board_and_session()
        board.send("PLACE T 20")
        result = check_with(board, session, False)  # nothing flows: no place found
        self.assertEqual(session.placement, ("T", 0))
        self.assertEqual(result["status"], "press_first")
        result = check_with(board, session, True)
        self.assertEqual((result["status"], session.placement), ("step_ok", ("T", 20)))
        self.assertIn("moved", result)
        result = check_with(board, session, False)  # let go: keeps the place
        self.assertEqual((result["status"], session.placement), ("ok", ("T", 20)))

    def test_placement_without_a_current_sensor(self):
        board, session = board_and_session(has_current=False)
        board.send("PLACE T 20")
        check_with(board, session, False)
        self.assertEqual(session.placement, ("T", 0))
        self.assertEqual(check_with(board, session, True)["status"], "step_ok")
        self.assertEqual(session.placement, ("T", 20))

    def test_any_spacing_while_pressed(self):
        board, session = board_and_session()
        board.send("MAP T20 T24 T30 T32")
        result = check_with(board, session, True)
        self.assertEqual(result["status"], "step_ok")
        self.assertEqual(session.lesson["rows"], ["T20", "T24", "T30", "T32"])
        self.assertEqual(check_with(board, session, False)["status"], "ok")


class TestTutor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = FakeBoard(4, "correct", seed=1)
        self.app = App(self.board, lesson=4, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel())

    def tearDown(self):
        self.tmp.cleanup()

    def test_tutor_walks_through_the_steps(self):
        out = self.app.ask("Check my circuit")  # not held yet
        self.assertIn("Press and hold the button", out["reply"])
        self.assertEqual(self.app.session.history["mistakes"], [])
        self.app.hold(True)
        out = self.app.ask("Check my circuit")
        self.assertIn("It works while you hold the button", out["reply"])
        self.assertTrue(out["reply"].endswith(LET_GO))
        self.assertEqual(self.board.lights, {r: "green" for r in L4["rows"]})
        self.assertEqual(self.app.state()["button_step"], "released")
        self.app.hold(False)
        out = self.app.ask("Check my circuit")
        self.assertIn("Your circuit is correct!", out["reply"])
        self.assertNotEqual(self.app.session.lesson_id, 4)  # passed: on to the next one

    def test_a_mistake_reply_ends_with_the_next_step(self):
        self.board.send("SCENARIO 4 button_always_on")
        self.app.hold(True)
        self.app.ask("Check my circuit")
        self.app.hold(False)
        out = self.app.ask("Check my circuit")
        self.assertIn("still flowing", out["reply"])
        self.assertTrue(out["reply"].endswith(LET_GO))
        self.assertEqual(self.board.lights, {"T5": "red", "T8": "red"})
        self.assertEqual(self.app.state()["mini_lesson"]["title"], "A bridge that never goes up")

    def test_page_state_and_hold(self):
        state = self.app.state()
        self.assertEqual((state["button_step"], state["held"]), ("pressed", False))
        self.assertIn("states", state["lesson"])
        self.assertEqual(state["lesson"]["accept"]["current_ma"], {"min": 4.0, "max": 7.5})
        self.assertEqual(state["mini_lesson"]["title"], "Buttons are drawbridges")  # intro card
        self.assertEqual(self.app.hold(True)["type"], "ack")
        self.assertTrue(self.app.state()["held"])
        self.app.ask("Check my circuit")
        self.assertEqual(self.app.state()["lesson"]["accept"]["current_ma"], {"max": 1})
        json.dumps(self.app.state())

    def test_state_is_put_back_when_claude_fails(self):
        self.assertIn("button_step", STATE)
        self.assertIn("press_reminded", STATE)


class TestPage(unittest.TestCase):
    def test_hold_toggle_and_button_drawing(self):
        self.assertIn('id="hold-btn"', HTML)
        self.assertIn('post("/api/hold"', HTML)
        self.assertIn("Hold the button", HTML)
        self.assertIn('name.includes("button")', HTML)
        self.assertIn('class: "bb-cap"', HTML)
        self.assertIn('button_always_on: "Button always on"', HTML)

    def test_hold_command(self):
        board = FakeBoard(4, "correct", seed=1)
        self.assertEqual(board.send("HOLD on")["type"], "ack")
        self.assertTrue(board.held)
        self.assertEqual(board.send("HOLD maybe")["type"], "error")
        board.set_scenario(1, "correct")  # a new lesson: nobody holds a button
        self.assertFalse(board.held)


class TestOtherLessonsUnchanged(unittest.TestCase):
    def test_lessons_one_to_three_have_no_steps(self):
        for lesson in (1, 2, 3):
            for scenario in SCENARIOS[lesson]:
                with self.subTest(lesson=lesson, scenario=scenario):
                    board = FakeBoard(lesson, scenario, seed=1)
                    session = Session(board, lesson=lesson)
                    self.assertIsNone(session.button_step)
                    result = session.measure()[1]
                    self.assertNotIn("step", result)
                    self.assertNotIn("next_step", result)
                    if scenario == "correct":
                        self.assertEqual(result["status"], "ok")
                    elif scenario not in UNLISTED[lesson]:
                        self.assertEqual(result["mistake_id"], scenario)


if __name__ == "__main__":
    unittest.main()
