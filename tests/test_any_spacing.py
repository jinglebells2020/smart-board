"""Any spacing: the lesson circuit is found even when parts span a
different number of rows than the lesson file (resistor in T20-T27, LED
in T27-T29 instead of T5-T10, T10-T12). The exact spacing is tried first,
so everything that worked before works the same."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.fallback import STATE
from agent.lessons import SAFETY_MAX_MA, load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.placement import (HOME, describe, find_flexible, find_placement, fits,
                             is_row_map, shift_lesson, shift_name)
from agent.session import Session
from protocol import FIRMWARE_CUT_MA
from sim.fake_board import FakeBoard
from web.server import App

LESSONS = load_lessons()
SPACED = (("T5", "T20"), ("T10", "T27"), ("T12", "T29"))
SPACED_WORDS = ("You built it in rows 20 to 29 (top half), with the parts spaced differently "
                "from the lesson. That is fine: I am checking it there.")


def measure(lesson, scenario="correct", command=None, extra=None, **opts):
    """Build on the fake board (command: MAP ... or COPIES ...), measure once.
    extra: {row: row_it_copies}, a dangling part from that row."""
    board = FakeBoard(lesson, scenario, seed=1, **opts)
    if command:
        assert board.send(command)["type"] == "ack", command
    if extra:
        plain = board.read

        def read():
            r = plain()
            for row, source in extra.items():
                if row in r["floating"]:
                    r["floating"].remove(row)
                r["rows"][row] = r["rows"][source]
            return r

        board.read = read
    session = Session(board, lesson=lesson)
    _, result, cut = session.measure()
    return session, result, cut, board


class TestRowMaps(unittest.TestCase):
    def test_names_lessons_and_words(self):
        self.assertTrue(is_row_map(SPACED))
        self.assertFalse(is_row_map(HOME))
        self.assertFalse(is_row_map(("B", 3)))
        self.assertEqual(shift_name("T10", SPACED), "T27")
        self.assertEqual(shift_name("PT+", SPACED), "PT+")
        bottom = (("T5", "B20"), ("T10", "B27"), ("T12", "B29"))
        self.assertEqual(shift_name("PT-", bottom), "PB-")
        self.assertEqual(shift_name("T40", SPACED), "T40")  # not in the map: as is
        moved = shift_lesson(LESSONS[1], SPACED)
        self.assertEqual(moved["rows"], ["T20", "T27", "T29"])
        self.assertEqual(moved["layout"][1]["legs"][1]["row"], "T27")
        self.assertEqual(moved["expected"]["rows"], {"T20": 5.0, "T27": 2.0, "T29": 0.0})
        self.assertEqual(LESSONS[1]["rows"], ["T5", "T10", "T12"])  # the original stays
        self.assertEqual(describe(SPACED, LESSONS[1]), "rows 20 to 29 (top half)")
        self.assertEqual(describe(bottom, LESSONS[1]), "rows 20 to 29 (bottom half)")
        self.assertTrue(fits(LESSONS[1], SPACED, {"T20", "T27", "T29"}))
        self.assertFalse(fits(LESSONS[1], SPACED, {"T20", "T27"}))

    def test_row_maps_are_plain_tuples(self):
        self.assertEqual(len({SPACED, HOME, tuple(SPACED)}), 2)  # hashable
        self.assertEqual(copy.deepcopy(SPACED), SPACED)
        self.assertIn("placement", STATE)  # put back when a Claude reply fails


class TestLessonOne(unittest.TestCase):
    def test_correct_with_other_spacing(self):
        session, result, cut, _ = measure(1, command="MAP T20 T27 T29")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.placement, SPACED)
        self.assertEqual(session.lesson["rows"], ["T20", "T27", "T29"])
        self.assertEqual(result["moved"], SPACED_WORDS)
        self.assertFalse(cut)

    def test_built_the_other_way_round(self):
        session, result, _, _ = measure(1, command="MAP T29 T27 T20")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.placement, (("T5", "T29"), ("T10", "T27"), ("T12", "T20")))

    def test_led_backwards_lights_the_leds_mapped_rows(self):
        _, result, _, _ = measure(1, "led_backwards", "MAP T20 T24 T26")
        self.assertEqual((result["status"], result["mistake_id"]), ("mistake", "led_backwards"))
        self.assertEqual(result["rows"], ["T24", "T26"])
        board = FakeBoard(1, "led_backwards", seed=1)
        board.send("MAP T20 T24 T26")
        session = Session(board, lesson=1)  # the tutor's own first check
        reply, _ = ask(build_agent(session, ScriptedTutorModel()), [], "Check my circuit")
        self.assertEqual(board.lights, {"T24": "red", "T26": "red"})
        self.assertTrue(reply.startswith("You built it in rows 20 to 26 (top half), with the "
                                         "parts spaced differently"), reply)

    def test_a_dangling_part_in_the_same_group_is_an_extra_part(self):
        for extra in ({"T23": "T27"}, {"T33": "T29"}, {"T16": "T20"}):
            with self.subTest(extra=extra):
                session, result, cut, board = measure(1, command="MAP T20 T27 T29", extra=extra)
                self.assertEqual(session.placement, SPACED)  # the circuit's own rows
                self.assertEqual(result["mistake_id"], "extra_part")
                self.assertEqual(result["extra_rows"], list(extra))
                self.assertFalse(cut)

    def test_bottom_half(self):
        session, result, _, _ = measure(1, command="MAP B20 B27 B29")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.lesson["layout"][0]["legs"][0]["row"], "PB+")

    def test_a_dangerous_circuit_still_cuts_power(self):
        _, result, cut, board = measure(1, "no_resistor", "MAP T20 T27 T29")
        self.assertEqual(result["mistake_id"], "no_resistor")
        self.assertTrue(cut)
        self.assertFalse(board.power_on)

    def test_safety_limits_did_not_change(self):
        self.assertEqual(SAFETY_MAX_MA, 30.0)
        self.assertEqual(FIRMWARE_CUT_MA, 50)

    def test_missing_wire_still_reported_as_before(self):
        # Only 2 rows connected: any spacing does not guess, the exact spacing decides.
        for scenario, rows in (("open_circuit", ["T10"]),):
            session, result, _, _ = measure(1, scenario)
            self.assertEqual((result["mistake_id"], result["rows"]), (scenario, rows))
            self.assertEqual(session.placement, HOME)
        session, result, _, _ = measure(1, "open_circuit", "PLACE T 15")
        self.assertEqual((result["mistake_id"], result["rows"]), ("open_circuit", ["T25"]))

    def test_part_in_a_wrong_row_at_home_stays_at_home(self):
        # The LED's long leg in row 11 instead of 10: 3 rows are connected,
        # but the student is building at the lesson's rows, so the exact
        # spacing still says what is missing.
        session, result, _, _ = measure(1, "open_circuit", extra={"T11": "T12"})
        self.assertEqual(session.placement, HOME)
        self.assertEqual(result["mistake_id"], "open_circuit")
        self.assertEqual(result["extra_rows"], ["T11"])

    def test_two_separate_groups_are_not_guessed(self):
        board = FakeBoard(1, "correct", seed=1)
        board.send("COPIES T20/T27/T29 T45/T52/T54")
        readings = [board.read() for _ in range(5)]
        self.assertEqual(len(find_flexible(readings, LESSONS[1])), 2)
        self.assertEqual(find_placement(readings, LESSONS[1]), HOME)  # not exactly one


class TestRebuiltOnTheSameRows(unittest.TestCase):
    """A row map is worked out again at every check, never kept just because
    its rows are still in use."""
    FORWARD = SPACED
    REVERSED = (("T5", "T29"), ("T10", "T27"), ("T12", "T20"))

    def setUp(self):
        self.board = FakeBoard(1, "correct", seed=1)
        self.session = Session(self.board, lesson=1)

    def build(self, rows, scenario="correct"):
        self.assertEqual(self.board.send("MAP " + rows)["type"], "ack")
        self.assertEqual(self.board.send(f"SCENARIO 1 {scenario}")["type"], "ack")
        return self.session.measure()[1]

    def test_rebuilt_the_other_way_round(self):
        self.assertEqual(self.build("T20 T27 T29")["status"], "ok")
        self.assertEqual(self.session.placement, self.FORWARD)
        result = self.build("T29 T27 T20")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.session.placement, self.REVERSED)
        self.assertEqual(result["moved"], SPACED_WORDS)

    def test_forward_reversed_forward(self):
        for rows, placement in (("T20 T27 T29", self.FORWARD), ("T29 T27 T20", self.REVERSED),
                                ("T20 T27 T29", self.FORWARD)):
            with self.subTest(rows=rows):
                self.assertEqual(self.build(rows)["status"], "ok")
                self.assertEqual(self.session.placement, placement)

    def test_the_reported_steps(self):
        # MAP T20 T27 T29 with a stray part, then the same rows reversed and correct.
        result = self.build("T20 T27 T29", "stray_part")
        self.assertEqual(result["mistake_id"], "extra_part")
        self.assertEqual(self.build("T29 T27 T20")["status"], "ok")
        self.assertEqual(self.session.placement, self.REVERSED)

    def test_rebuilt_with_the_lessons_spacing(self):
        self.build("T20 T27 T29")
        result = self.build("T5 T10 T12")  # back where the lesson shows
        self.assertEqual((result["status"], self.session.placement), ("ok", HOME))
        self.assertNotIn("moved", result)
        result = self.build("T20 T25 T27")  # the lesson's spacing, moved down
        self.assertEqual((result["status"], self.session.placement), ("ok", ("T", 15)))
        self.assertIn("not where the lesson shows", result["moved"])

    def test_stray_part_follows_the_mapped_middle_row(self):
        _, result, _, _ = measure(1, "stray_part", "MAP T20 T27 T29")
        self.assertEqual(result["extra_rows"], ["T47"])
        _, result, _, _ = measure(1, "stray_part", "MAP T29 T27 T20")
        self.assertEqual(result["extra_rows"], ["T47"])
        _, result, _, _ = measure(1, "stray_part")
        self.assertEqual(result["extra_rows"], ["T30"])  # unchanged without MAP
        _, result, _, _ = measure(1, "stray_part", "PLACE T 15")
        self.assertEqual(result["extra_rows"], ["T45"])


class TestLessonTwoAndThree(unittest.TestCase):
    def test_lesson_two_with_other_spacing(self):
        session, result, _, _ = measure(2, command="MAP T5 T12 T16")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.placement, (("T5", "T5"), ("T10", "T12"), ("T15", "T16")))
        self.assertIn("spaced differently", result["moved"])

    def test_lesson_three_mixed_copies(self):
        session, result, cut, _ = measure(3, command="COPIES T0 T20/T27/T29 B10")
        self.assertEqual((result["status"], result["count"]), ("ok", 3))
        # Exact-spacing copies first, then any spacing.
        self.assertEqual([c["rows"] for c in result["copies"]],
                         ["rows 5 to 12 (top half)", "rows 15 to 22 (bottom half)",
                          "rows 20 to 29 (top half)"])
        self.assertFalse(cut)

    def test_lesson_three_two_spaced_copies_in_one_group(self):
        _, result, _, _ = measure(3, command="COPIES T20/T27/T29 T33/T38/T40")
        self.assertEqual((result["status"], result["count"]), ("ok", 2))

    def test_a_mistake_in_a_spaced_copy(self):
        _, result, _, _ = measure(3, "led_backwards", "COPIES T0 T20/T24/T26")
        self.assertEqual((result["mistake_id"], result["rows"]), ("led_backwards", ["T24", "T26"]))


class TestFakeBoardAndServer(unittest.TestCase):
    def test_map_command(self):
        board = FakeBoard(1, "correct", seed=1)
        self.assertEqual(board.send("MAP T20 T27 T29")["type"], "ack")
        self.assertEqual(board.place, ("ROWS", ("T20", "T27", "T29")))
        r = board.read()
        self.assertNotIn("T27", r["floating"])
        self.assertIn("T10", r["floating"])
        for bad in ("MAP T20 T27", "MAP T20 B27 T29", "MAP T20 T20 T29", "MAP X1 T2 T3"):
            self.assertEqual(board.send(bad)["type"], "error", bad)
        self.assertEqual(board.send("COPIES T0 T20/T27/T29")["cmd"], "COPIES T0 T20/T27/T29")

    def test_build_rows_through_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = FakeBoard(1, "correct", seed=1)
            app = App(board, lesson=1, history_path=Path(tmp) / "h.json",
                      model=ScriptedTutorModel())
            self.assertEqual(app.build("correct", rows=["T20", "T27", "T29"])["type"], "ack")
            json.dumps(app.state())  # the state (with this place) is still JSON
            out = app.ask("Check my circuit")
            self.assertTrue(out["reply"].startswith(SPACED_WORDS), out["reply"])
            self.assertEqual(app.build("correct", rows="T20")["type"], "error")


if __name__ == "__main__":
    unittest.main()
