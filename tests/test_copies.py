"""Lesson 3 "More LEDs": the board finds 1, 2 or 3 copies of the LED
circuit anywhere and checks each one. Every lesson but lesson 0 also
spots a part plugged into a row that is not in the plan ("extra_part").
Lessons without "copies" behave exactly as before."""

import json
import tempfile
import unittest
import urllib.request
import threading
from pathlib import Path

from agent.build import ask, build_agent
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.placement import HOME, find_all_placements
from agent.session import Session
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from web.server import App, make_server

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def measure(lesson, scenario="correct", copies=None, **board_opts):
    board = FakeBoard(lesson, scenario, seed=1, **board_opts)
    if copies:
        assert board.send("COPIES " + " ".join(copies))["type"] == "ack"
    session = Session(board, lesson=lesson)
    summary, result, cut = session.measure()
    return session, summary, result, cut, board


class TestLessonThree(unittest.TestCase):
    def test_the_lesson_file(self):
        l3 = LESSONS[3]
        self.assertEqual(l3["title"], "More LEDs")
        self.assertEqual(l3["copies"], {"min": 1, "max": 3})
        self.assertEqual(l3["current_from"][0]["ohms"], 330)
        self.assertEqual(l3["expected"]["current_ma"], 3.9)  # (3.3 - 2) / 330
        # (3.3 - 2.4) / 330 = 2.7 mA and (3.3 - 1.5) / 330 = 5.5 mA: the whole
        # row window fits (a board with no sensor works the current out from it).
        self.assertEqual(l3["accept"]["current_ma"], {"min": 2.7, "max": 5.5})
        self.assertIn("330 ohm resistor", [p["part"] for p in l3["layout"]])
        self.assertEqual([m["id"] for m in l3["mistakes"]], [m["id"] for m in LESSONS[1]["mistakes"]])
        self.assertNotIn("220", json.dumps(l3))
        self.assertLess(3 * 3.9, 30)  # three copies stay under the safety cut

    def test_one_two_or_three_correct_copies(self):
        for copies in (["T0"], ["T0", "T15"], ["T0", "T15", "B0"], ["B20"], ["T30", "B3"]):
            with self.subTest(copies=copies):
                session, summary, result, cut, board = measure(3, copies=copies)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["count"], len(copies))
                self.assertEqual([c["status"] for c in result["copies"]], ["ok"] * len(copies))
                self.assertIn(f"I found {len(copies)} LED circuit", result["found"])
                self.assertFalse(cut)
                self.assertTrue(board.power_on)
                self.assertNotIn("not_checked", result)  # the total current was checked

    def test_a_mistake_in_one_copy_lights_only_that_copy(self):
        session, _, result, cut, board = measure(3, "led_backwards", ["T0", "T15", "B0"])
        self.assertEqual((result["status"], result["mistake_id"]), ("mistake", "led_backwards"))
        self.assertEqual(result["rows"], ["B10", "B12"])  # the last copy only
        self.assertEqual(result["count"], 3)
        self.assertEqual([c["status"] for c in result["copies"]], ["ok", "ok", "mistake"])
        # The tutor lights those rows only, and says how many it found.
        agent = build_agent(session, ScriptedTutorModel())
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertEqual(board.lights, {"B10": "red", "B12": "red"})
        self.assertIn("I found 3 LED circuits", reply)

    def test_other_mistakes_in_one_copy(self):
        for scenario, rows in (("open_circuit", ["T25"]), ("loose_led_leg", ["T25"]),
                               ("led_bypassed", ["T25", "T27"])):
            with self.subTest(scenario=scenario):
                _, _, result, _, _ = measure(3, scenario, ["T0", "T15"])
                self.assertEqual(result["mistake_id"], scenario)
                self.assertEqual(result["rows"], rows)

    def test_total_current_too_high_is_not_ok(self):
        board = FakeBoard(3, "correct", seed=1)
        board.send("COPIES T0 T15")
        plain = board.read

        def more_current():  # something else draws 8 mA: the rows look fine (16 mA in all)
            r = plain()
            r["current_ma"] += 8.0
            return r

        board.read = more_current
        _, result, cut = Session(board, lesson=3).measure()
        self.assertNotEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        self.assertFalse(cut)  # 16 mA is under the 30 mA cut

    def test_a_too_big_resistor_shows_in_the_total_current(self):
        _, _, result, _, _ = measure(3, "resistor_too_big", ["T0", "T15"])
        self.assertEqual(result["mistake_id"], "resistor_too_big")
        # Every copy looks the same by its voltages: all of them light up.
        self.assertEqual(result["rows"], ["T5", "T10", "T20", "T25"])

    def test_dangerous_copy_still_cuts_power(self):
        _, _, result, cut, board = measure(3, "no_resistor", ["T0", "T15"])
        self.assertEqual(result["mistake_id"], "no_resistor")
        self.assertTrue(cut)
        self.assertFalse(board.power_on)

    def test_one_copy_without_a_current_sensor(self):
        _, _, result, _, _ = measure(3, copies=["T0", "T15"], has_current=False)
        self.assertEqual((result["status"], result["count"]), ("ok", 2))


class TestFindAllPlacements(unittest.TestCase):
    def readings(self, copies, scenario="correct"):
        board = FakeBoard(3, scenario, seed=1)
        board.send("COPIES " + " ".join(copies))
        return [board.read() for _ in range(10)]

    def test_order_no_shared_rows_and_at_most_max(self):
        lesson = LESSONS[3]
        found = find_all_placements(self.readings(["B0", "T15", "T0"]), lesson, 3)
        self.assertEqual(found, [("T", 0), ("T", 15), ("B", 0)])  # top first, lowest row first
        self.assertEqual(find_all_placements(self.readings(["T0", "T15", "B0"]), lesson, 2),
                         [("T", 0), ("T", 15)])
        rows = [{f"T{n + o}" for n in (5, 10, 12)} for _, o in found if _ == "T"]
        self.assertFalse(rows[0] & rows[1])

    def test_a_copy_with_an_empty_row_is_still_found(self):
        found = find_all_placements(self.readings(["T0", "T15"], "open_circuit"), LESSONS[3], 3)
        self.assertEqual(found, [("T", 0), ("T", 15)])

    def test_nothing_built_or_power_off(self):
        off = {"power_on": False, "rows": {"T5": 0.0}, "floating": [], "supply_v": 0.0}
        self.assertEqual(find_all_placements([off], LESSONS[3], 3), [])
        self.assertEqual(find_all_placements([], LESSONS[3], 3), [])


class TestExtraParts(unittest.TestCase):
    def test_stray_part_in_lessons_one_and_two(self):
        for lesson in (1, 2):
            with self.subTest(lesson=lesson):
                session, _, result, cut, board = measure(lesson, "stray_part")
                self.assertEqual((result["status"], result["mistake_id"]), ("mistake", "extra_part"))
                self.assertEqual(result["extra_rows"], ["T30"])
                self.assertEqual((result["rows"], result["light"]), (["T30"], "yellow"))
                self.assertFalse(cut)
                agent = build_agent(session, ScriptedTutorModel())
                reply, _ = ask(agent, [], "Check my circuit")
                self.assertTrue(reply.startswith(
                    "Something is plugged into row 30, which is not in the plan. "
                    "Take out any part that is not in the plan."), reply)
                self.assertEqual(board.lights, {"T30": "yellow"})
                self.assertTrue(board.power_on)

    def test_stray_part_in_lesson_three(self):
        _, _, result, _, _ = measure(3, "stray_part", ["T0", "T15"])
        self.assertEqual(result["mistake_id"], "extra_part")
        self.assertEqual(result["extra_rows"], ["T45"])  # last copy's middle row + 20
        self.assertEqual(result["count"], 2)

    def test_a_real_mistake_is_kept_and_the_extra_part_mentioned_first(self):
        board = FakeBoard(1, "no_resistor", seed=1)
        plain = board.read

        def with_extra():
            r = plain()
            if r["power_on"] and "T40" in r["floating"]:
                r["floating"].remove("T40")
                r["rows"]["T40"] = 1.0
            return r

        board.read = with_extra
        session = Session(board, lesson=1)
        _, result, cut = session.measure()
        self.assertEqual(result["mistake_id"], "no_resistor")  # the power cut is not hidden
        self.assertTrue(cut)
        self.assertEqual(result["extra_rows"], ["T40"])
        self.assertTrue(result["extra_say"].startswith("Something is plugged into row 40"))

    def test_extra_part_next_to_a_mistake_is_said_first_and_lit_yellow(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        plain = board.read

        def with_extra():
            r = plain()
            if "T40" in r["floating"]:
                r["floating"].remove("T40")
                r["rows"]["T40"] = 1.0
            return r

        board.read = with_extra
        session = Session(board, lesson=1)
        reply, _ = ask(build_agent(session, ScriptedTutorModel()), [], "Check my circuit")
        self.assertTrue(reply.startswith("Something is plugged into row 40"), reply)
        self.assertEqual(session.last_check[1]["mistake_id"], "led_backwards")
        self.assertEqual(board.lights, {"T10": "red", "T12": "red", "T40": "yellow"})

    def test_extra_part_has_a_card_and_hint(self):
        session, _, result, _, _ = measure(1, "stray_part")
        logged = session.log_mistake("extra_part")
        self.assertIn("row 30", logged["say"])
        self.assertEqual(logged["mini_lesson"]["title"], "Every part needs a job")
        self.assertIn("error", Session(FakeBoard(0, "correct"), lesson=0).log_mistake("extra_part"))

    def test_lesson_zero_has_no_extra_parts(self):
        board = FakeBoard(0, "correct", seed=1)
        session = Session(board, lesson=0)
        _, result, _ = session.measure()
        self.assertNotIn("extra_rows", result)


class TestLessonsWithoutCopiesAreUnchanged(unittest.TestCase):
    def test_every_circuit_in_lessons_one_and_two(self):
        for lesson in (1, 2):
            for scenario in SCENARIOS[lesson]:
                with self.subTest(lesson=lesson, scenario=scenario):
                    session, _, result, _, _ = measure(lesson, scenario)
                    for key in ("count", "copies", "found", "extra_rows"):
                        self.assertNotIn(key, result)
                    if scenario == "correct":
                        self.assertEqual(result["status"], "ok")
                    elif scenario in UNLISTED[lesson]:
                        self.assertEqual(result["status"], "unknown")
                    else:
                        self.assertEqual(result["mistake_id"], scenario)
                    self.assertEqual(session.placement, HOME)

    def test_two_copies_in_lesson_one_are_not_counted(self):
        _, _, result, _, _ = measure(1, copies=["T0", "T15"])
        self.assertNotIn("count", result)
        self.assertEqual(result["mistake_id"], "extra_part")  # the second circuit is extra
        self.assertEqual(result["extra_rows"], ["T20", "T25", "T27"])

    def test_led_backwards_says_backwards_wrong_row_or_missing(self):
        for lesson in (1, 3):
            m = next(m for m in LESSONS[lesson]["mistakes"] if m["id"] == "led_backwards")
            for text in (m["description"], m["explain"]):
                self.assertIn("backwards", text)
                self.assertIn("wrong row", text)
                self.assertIn("missing", text)
                self.assertIn("cannot tell which", text)


class TestPageAndServer(unittest.TestCase):
    def test_build_copies_and_found_on_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App(FakeBoard(3, "correct", seed=1), lesson=3,
                      history_path=Path(tmp) / "h.json", model=ScriptedTutorModel())
            self.assertIsNone(app.state()["copies_found"])
            self.assertEqual(app.state()["lesson"]["copies"], {"min": 1, "max": 3})
            server = make_server(app, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                req = urllib.request.Request(
                    url + "/api/build", headers={"Content-Type": "application/json"},
                    data=json.dumps({"scenario": "correct", "copies": ["T 0", "T 15"]}).encode())
                self.assertEqual(json.loads(urllib.request.urlopen(req).read())["type"], "ack")
                out = app.ask("Check my circuit")
                self.assertIn("I found 2 LED circuits", out["reply"])
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(app.session.last_check[1]["count"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            app = App(FakeBoard(1, "correct", seed=1), lesson=1,
                      history_path=Path(tmp) / "h.json", model=ScriptedTutorModel())
            self.assertIsNone(app.state()["copies_found"])
            self.assertIn("stray_part", app.state()["scenarios"])
            self.assertEqual(app.build("correct", copies="T 0")["type"], "error")

    def test_page_shows_the_count(self):
        self.assertIn('id="copies-note"', HTML)
        self.assertIn('"LED circuits found: "', HTML)
        self.assertIn('stray_part: "Extra part plugged in"', HTML)
        # The current pill aims for N times one circuit's range.
        self.assertIn("const n = L.copies && copies ? copies : 1;", HTML)

    def test_copies_command(self):
        board = FakeBoard(3, "correct", seed=1)
        self.assertEqual(board.send("COPIES T0 T15 B0")["type"], "ack")
        self.assertEqual(board.copies, [("T", 0), ("T", 15), ("B", 0)])
        self.assertEqual(board.send("COPIES X1")["type"], "error")
        self.assertEqual(board.send("COPIES Tx")["type"], "error")
        r = board.read()
        self.assertAlmostEqual(r["current_ma"], 3 * 1.3 / 330 * 1000, delta=0.5)
        board.set_scenario(1, "correct")  # a new lesson: one circuit again
        self.assertIsNone(board.copies)


if __name__ == "__main__":
    unittest.main()
