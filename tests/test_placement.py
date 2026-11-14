"""The circuit can be built anywhere on the breadboard (same parts and
spacing, other rows, top or bottom half): Zip finds it and checks it there."""

import unittest

from agent.lessons import load_lessons
from agent.placement import HOME, describe, find_placement, shift_lesson, shift_name
from agent.session import Session
from sim.fake_board import FakeBoard


def measure(lesson, scenario, place, rows=None):
    board = FakeBoard(lesson, scenario, seed=1, rows=rows)
    board.set_place(*place)
    session = Session(board, lesson=lesson)
    _, result, _ = session.measure()
    return session, result, board


class TestShift(unittest.TestCase):
    def test_names(self):
        self.assertEqual(shift_name("T5", ("T", 15)), "T20")
        self.assertEqual(shift_name("T5", ("B", 0)), "B5")
        self.assertEqual(shift_name("PT+", ("B", 3)), "PB+")
        self.assertEqual(shift_name("red", ("B", 3)), "red")

    def test_lesson_copy_moves_every_row_name_and_keeps_the_original(self):
        l1 = load_lessons()[1]
        moved = shift_lesson(l1, ("T", 15))
        self.assertEqual(moved["rows"], ["T20", "T25", "T27"])
        self.assertIn("T25", moved["accept"]["rows"])
        self.assertEqual(moved["layout"][0]["legs"][0]["row"], "PT+")
        self.assertEqual(l1["rows"], ["T5", "T10", "T12"])
        self.assertIs(shift_lesson(l1, HOME), l1)

    def test_describe(self):
        self.assertEqual(describe(("B", 30), load_lessons()[1]), "rows 35 to 42 (bottom half)")


class TestFindPlacement(unittest.TestCase):
    def test_built_anywhere_is_checked_there(self):
        cases = [(1, "correct", ("T", 15), "ok", None),
                 (1, "correct", ("B", 30), "ok", None),
                 (1, "led_backwards", ("T", 15), "mistake", ["T25", "T27"]),
                 (1, "no_resistor", ("B", 3), "mistake", ["B8", "B13"]),
                 (1, "loose_led_leg", ("T", 40), "mistake", ["T50"]),
                 (2, "correct", ("T", 30), "ok", None),
                 (2, "middle_floating", ("B", 10), "mistake", ["B20"])]
        for lesson, scenario, place, status, rows in cases:
            with self.subTest(scenario=scenario, place=place):
                session, result, _ = measure(lesson, scenario, place)
                self.assertEqual(session.placement, place)
                self.assertEqual(result["status"], status)
                self.assertIn("moved", result)
                if rows:
                    self.assertEqual(result["rows"], rows)

    def test_home_is_not_moved(self):
        session, result, _ = measure(1, "correct", HOME)
        self.assertEqual(session.placement, HOME)
        self.assertNotIn("moved", result)

    def test_a_mistake_at_home_stays_at_home(self):
        for scenario in ("open_circuit", "no_resistor", "led_bypassed", "resistor_150"):
            with self.subTest(scenario=scenario):
                session, result, _ = measure(1, scenario, HOME)
                self.assertEqual(session.placement, HOME)
                self.assertNotIn("moved", result)

    def test_moving_back_home(self):
        board = FakeBoard(1, "correct", seed=1)
        board.set_place("T", 15)
        session = Session(board, lesson=1)
        session.measure()
        board.set_place("T", 0)
        _, result, _ = session.measure()
        self.assertEqual(session.placement, HOME)
        self.assertEqual(result["status"], "ok")

    def test_power_off_keeps_the_placement(self):
        lesson = load_lessons()[1]
        off = {"power_on": False, "rows": {"T5": 0.0}, "floating": [], "supply_v": 0.0}
        self.assertEqual(find_placement([off], lesson, ("T", 15)), ("T", 15))

    def test_only_measured_rows_are_used(self):
        # A board that measures only rows 1 to 16 cannot see a circuit in row 20.
        session, result, _ = measure(1, "correct", ("T", 15), rows="T1-T16")
        self.assertEqual(session.placement, HOME)

    def test_a_new_lesson_starts_at_home(self):
        session, _, _ = measure(1, "correct", ("T", 15))
        session.set_lesson(2)
        self.assertEqual(session.placement, HOME)
        self.assertEqual(session.lesson["rows"], ["T5", "T10", "T15"])


class TestFakeBoardPlace(unittest.TestCase):
    def test_place_command(self):
        board = FakeBoard(1, "correct", seed=1)
        self.assertEqual(board.send("PLACE B 3")["type"], "ack")
        self.assertEqual(board.place, ("B", 3))
        self.assertEqual(board.send("PLACE X 3")["type"], "error")
        r = board.read()
        self.assertNotIn("B8", r["floating"])
        self.assertIn("T5", r["floating"])


if __name__ == "__main__":
    unittest.main()
