"""Part A: the power and hint rules, found by testing the page.

Bug 1: button 2 ("I need a hint") after a power cut for no_resistor ran
restore_power, read_rows, cut_power, and logged the mistake again.
Rule: the hint button never measures, never changes power, never adds a
mistake. It only gives the next level of help for the most recent
mistake (hint, then full explanation), or the current build step.

Bug 2: button 4 turned power on into a circuit that was still unsafe.
Rule (plain Python): every time power is turned on, by any button, tool
or message, measure at once, cut again if unsafe, and say why.

Rule 3: power may only be turned back on when the student asks for it
(button 4, or saying they fixed it). No other message may do it.
"""

import json
import unittest

from agent.build import ask, build_agent
from agent.buttons import handle_button
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from agent.tools import make_tools
from sim.fake_board import FakeBoard

LESSONS = load_lessons()


def mistake(lid, mid):
    return next(m for m in LESSONS[lid]["mistakes"] if m["id"] == mid)


class CountingBoard(FakeBoard):
    """A fake board that counts readings and remembers commands."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.reads = 0
        self.sent = []

    def read(self):
        self.reads += 1
        return super().read()

    def send(self, line):
        self.sent.append(line.strip())
        return super().send(line)

    def watch(self):
        """Start counting again from now."""
        self.reads, self.sent = 0, []


def setup(lesson=1, scenario="no_resistor", **opts):
    board = CountingBoard(lesson, scenario, seed=4, **opts)
    session = Session(board, lesson=lesson)
    agent = build_agent(session, ScriptedTutorModel())
    history = []
    tutor = lambda text: ask(agent, history, text)[0]
    return board, session, agent, history, tutor


class TestBug1HintButton(unittest.TestCase):
    def test_hint_after_a_power_cut_changes_nothing(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        handle_button(1, session, tutor)                 # check: power is cut
        self.assertFalse(board.power_on)
        self.assertEqual(len(session.history["mistakes"]), 1)

        board.watch()
        result = handle_button(2, session, tutor)        # I need a hint
        self.assertEqual(board.reads, 0, "the hint button measured")
        self.assertNotIn("RESTORE", board.sent, "the hint button turned power on")
        self.assertNotIn("CUT", board.sent)
        self.assertFalse(board.power_on)
        self.assertEqual(len(session.history["mistakes"]), 1, "the hint logged a mistake")
        # next level of help: the full explanation
        self.assertIn(mistake(1, "no_resistor")["explain"], result["reply"])

    def test_hint_levels_hint_then_explain(self):
        board, session, agent, history, tutor = setup(1, "led_backwards")
        first = handle_button(1, session, tutor)["reply"]
        self.assertIn(mistake(1, "led_backwards")["hint"], first)
        board.watch()
        second = handle_button(2, session, tutor)["reply"]
        third = handle_button(2, session, tutor)["reply"]
        self.assertIn(mistake(1, "led_backwards")["explain"], second)
        self.assertIn(mistake(1, "led_backwards")["explain"], third)  # stays at the top level
        self.assertEqual(board.reads, 0)
        self.assertEqual(len(session.history["mistakes"]), 1)

    def test_hint_without_a_mistake_gives_the_build_step(self):
        board, session, agent, history, tutor = setup(1, "correct")
        board.watch()
        reply = handle_button(2, session, tutor)["reply"]
        self.assertIn("Red wire", reply)           # step 1, not started yet
        self.assertEqual(session.step, -1)         # the hint did not move on
        handle_button(3, session, tutor)
        handle_button(3, session, tutor)           # now at step 2
        board.watch()
        reply = handle_button(2, session, tutor)["reply"]
        self.assertIn("220 ohm resistor", reply)
        self.assertEqual(session.step, 1)
        self.assertEqual(board.reads, 0)
        self.assertEqual(session.history["mistakes"], [])

    def test_a_fixed_mistake_is_no_longer_the_most_recent(self):
        board, session, agent, history, tutor = setup(1, "led_backwards")
        handle_button(1, session, tutor)
        board.send("SCENARIO 1 correct")
        handle_button(1, session, tutor)           # correct: moves to lesson 2
        reply = handle_button(2, session, tutor)["reply"]
        self.assertNotIn(mistake(1, "led_backwards")["explain"], reply)
        self.assertIn("Red wire", reply)           # lesson 2, step 1


class TestBug2PowerOnIntoAnUnsafeCircuit(unittest.TestCase):
    def test_power_button_measures_and_cuts_again(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        handle_button(1, session, tutor)           # cut
        board.watch()
        result = handle_button(4, session, tutor)  # power on, still unsafe
        self.assertIn("RESTORE", board.sent)
        self.assertGreater(board.reads, 0, "power went on without measuring")
        self.assertIn("CUT", board.sent[board.sent.index("RESTORE") + 1:],
                      "power was not cut again after turning on")
        self.assertFalse(board.power_on, "an unsafe circuit was left powered")
        self.assertFalse(result["power_on"])
        self.assertIn("still", result["say"].lower())
        self.assertIn("off again", " ".join(board.screen).lower())

    def test_power_button_into_a_safe_circuit_stays_on(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        handle_button(1, session, tutor)
        board.send("SCENARIO 1 correct")
        result = handle_button(4, session, tutor)
        self.assertTrue(board.power_on)
        self.assertTrue(result["power_on"])

    def test_restore_tool_measures_and_cuts_again(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        tools = {f.__name__: f for f in make_tools(session)}
        session.measure()                          # cut
        session.hear("I fixed it")                 # the student asked
        board.watch()
        out = json.loads(tools["restore_power"]())
        self.assertFalse(board.power_on)
        self.assertTrue(out["recut"])
        self.assertIn("still", out["why"].lower())

    def test_fixed_message_into_an_unsafe_circuit(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        tutor("Check my circuit")
        reply = tutor("I fixed it")                # but it is not fixed
        self.assertFalse(board.power_on)
        self.assertIn("still", reply.lower())


class TestRule3OnlyTheStudentTurnsPowerOn(unittest.TestCase):
    def test_check_message_does_not_turn_power_on(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        tutor("Check my circuit")
        board.send("SCENARIO 1 correct")
        board.watch()
        reply = tutor("Check my circuit")          # not "I fixed it"
        self.assertNotIn("RESTORE", board.sent)
        self.assertFalse(board.power_on)
        self.assertIn("4", reply)                  # says how to turn it on

    def test_restore_tool_refuses_without_the_student_asking(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        session.measure()
        tools = {f.__name__: f for f in make_tools(session)}
        session.hear("Check my circuit")
        out = json.loads(tools["restore_power"]())
        self.assertIn("error", out)
        self.assertFalse(board.power_on)

    def test_asking_works_once(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        session.measure()
        board.send("SCENARIO 1 correct")
        session.hear("I fixed it")
        tools = {f.__name__: f for f in make_tools(session)}
        self.assertTrue(json.loads(tools["restore_power"]())["power_on"])
        board.send("CUT")
        self.assertIn("error", json.loads(tools["restore_power"]()))  # not again

    def test_fixed_message_turns_power_on(self):
        board, session, agent, history, tutor = setup(1, "no_resistor")
        tutor("Check my circuit")
        board.send("SCENARIO 1 correct")
        reply = tutor("I fixed it")
        self.assertTrue(board.power_on)
        self.assertIn("correct", reply)

    def test_which_messages_count_as_asking(self):
        session = Session(FakeBoard())
        for text in ("I fixed it", "fixed!", "I added the resistor", "I flipped the LED",
                     "please turn the power on", "power on", "I moved the wire"):
            with self.subTest(text=text):
                session.hear(text)
                self.assertTrue(session.restore_allowed)
        for text in ("Check my circuit", "I need a hint", "what is wrong?", "check again",
                     "is the power off?"):
            with self.subTest(text=text):
                session.hear(text)
                self.assertFalse(session.restore_allowed)


class TestNoPowerSwitch(unittest.TestCase):
    def test_hint_button_does_not_touch_anything(self):
        board, session, agent, history, tutor = setup(1, "no_resistor", has_power_switch=False)
        handle_button(1, session, tutor)
        board.watch()
        handle_button(2, session, tutor)
        self.assertEqual(board.reads, 0)
        self.assertFalse({"CUT", "RESTORE"} & set(board.sent))
        self.assertEqual(len(session.history["mistakes"]), 1)


if __name__ == "__main__":
    unittest.main()
