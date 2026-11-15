"""Genesis on the station (jinglebells2020/smartboard, docs/HARDWARE.md):
a 3.3 V supply, a meter that tops out at about 2.9 to 3.1 V (anything
above is sent as the supply) and is good to about 0.1 V, no current
sensor, no power switch, and buttons with colours instead of numbers.
"""

import json
import re
import unittest
from pathlib import Path

import protocol as P
from agent.boardlink import BoardLink
from agent.lessons import check, free_check, load_lessons, summarize
from agent.serial_board import STATION_VID, BoardError, choose_port
from agent.session import NO_SWITCH_SAY, Session
from sim.fake_board import FakeBoard
from sim.random_circuit import BRIGHT_AT_3V3, random_circuit
from tests.test_rows_checking import CIRCUITS, FLOAT, UNLISTED, reading

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
V = P.SUPPLY_V

# Lesson 4, the button: true row voltages per step (no current sensor).
BUTTON = {
    ("pressed", "correct"): {"T5": V, "T8": V, "T13": 2.0, "T15": 0},
    ("pressed", "led_backwards"): {"T5": V, "T8": V, "T13": V, "T15": 0},
    ("released", "correct"): {"T5": V, "T8": FLOAT, "T13": FLOAT, "T15": 0},
    ("released", "button_always_on"): {"T5": V, "T8": V, "T13": 2.0, "T15": 0},
}


def station(rows, offset, top):
    """What the station sends for these true row voltages: its meter reads
    each one `offset` volts off (never below 0), and a reading at or above
    its top (`top`, about 2.9 to 3.1 V) is sent as the supply, 3.3 V."""
    out = {}
    for name, v in rows.items():
        if v is FLOAT:
            out[name] = FLOAT
        else:
            seen = max(0.0, v + offset)
            out[name] = V if seen >= top else round(seen, 3)
    return out


def station_summary(lesson, rows, supply, offset, top):
    r = reading(station(rows, offset, top), 0.0, supply, with_sensor=False)
    return summarize([r], lesson)


def outcome(result):
    return result.get("mistake_id") or result["status"]


class TestSupplySetting(unittest.TestCase):
    def test_one_setting(self):
        self.assertEqual(P.SUPPLY_V, 3.3)
        self.assertEqual(P.low_supply_v(), 2.805)  # 0.85 x 3.3: below the meter's top
        self.assertEqual(P.low_supply_v(5.0), 4.25)
        self.assertLess(P.low_supply_v(), P.ADC_TOP_V - P.ADC_ACCURACY_V + 0.01)
        from sim import fake_board, solver
        self.assertEqual((fake_board.SUPPLY_V, solver.SUPPLY_V), (3.3, 3.3))

    def test_a_hello_with_supply_v_is_used(self):
        self.assertEqual(P.supply_of({"supply_v": 3.3}), 3.3)
        self.assertEqual(P.supply_of({"supply_v": 5}), 5.0)
        for hello in ({}, None, {"supply_v": None}, {"supply_v": "3.3"}, {"supply_v": 0},
                      {"supply_v": True}):
            self.assertEqual(P.supply_of(hello), V, hello)

    def test_the_board_supply_reaches_every_check(self):
        link = BoardLink(FakeBoard(1, "correct", seed=1, supply_v=5.0))
        self.assertEqual(link.supply_v, 5.0)
        r = link.read()
        self.assertEqual(r["nominal_v"], 5.0)  # laptop only
        self.assertEqual(summarize([r], LESSONS[1])["nominal_v"], 5.0)
        # A hello line with no "supply_v": the station's 3.3 V.
        class OldHello(FakeBoard):
            def hello(self):
                h = super().hello()
                h.pop("supply_v")
                return h
        self.assertEqual(BoardLink(OldHello()).supply_v, V)

    def test_low_supply_is_relative_to_the_board(self):
        def free(supply, nominal):
            r = reading({"T5": supply}, 0.0, supply, with_sensor=False)
            r["nominal_v"] = nominal
            return free_check(summarize([r], {"rows": ["T5"]}))
        self.assertNotIn("action", free(3.0, 3.3))   # a healthy + line near the meter's top
        self.assertEqual(free(2.6, 3.3)["action"], "cut_power")
        self.assertEqual(free(4.0, 5.0)["action"], "cut_power")  # a 5 V board
        self.assertNotIn("action", free(4.6, 5.0))

    def test_where_the_red_wire_goes_names_the_supply(self):
        self.assertEqual(Session._where("PT+"), "the top + line (3.3 V)")
        session = Session(FakeBoard(1, supply_v=5.0), lesson=1)
        self.assertIn("the top + line (5 V)", session.hint()["say"])
        self.assertIn("the top + line (3.3 V)", Session(FakeBoard(1), lesson=1).hint()["say"])


class TestLessonsOnTheStationMeter(unittest.TestCase):
    """Every lesson circuit read the way the station reads it: up to 0.1 V
    off, and the top of its range (2.9 to 3.1 V) sent as 3.3 V."""

    def expected(self, lid, circuit, seen_top):
        if circuit in ("correct", "resistor_150", "resistor_too_big"):
            return "ok"  # no current sensor (docs/LESSONS.md)
        if circuit in UNLISTED:
            return "unknown"
        if (lid, circuit) == (2, "top_1k") and seen_top:
            return "bottom_missing"  # 3.0 V read as the full supply: the station cannot tell
        return circuit

    def test_every_circuit_with_offsets_and_any_meter_top(self):
        for lid, circuits in CIRCUITS.items():
            for circuit, (rows, _current, supply) in circuits.items():
                for offset in (-0.1, 0.0, 0.1):
                    for top in (2.9, 3.0, 3.1):
                        with self.subTest(lesson=lid, circuit=circuit, offset=offset, top=top):
                            s = station_summary(LESSONS[lid], rows, supply, offset, top)
                            middle = rows.get("T10")
                            seen_top = middle is not FLOAT and middle + offset >= top
                            self.assertEqual(outcome(check(s, LESSONS[lid])),
                                             self.expected(lid, circuit, seen_top))

    def test_the_divider_middle_only_reads_as_the_supply_near_the_top(self):
        s = station_summary(LESSONS[2], CIRCUITS[2]["top_1k"][0], V, 0.0, 3.1)
        self.assertEqual(outcome(check(s, LESSONS[2])), "top_1k")  # 3.0 V, under the top

    def test_lesson_4_steps(self):
        lesson = LESSONS[4]
        for (step, circuit), rows in BUTTON.items():
            st = lesson["states"][step]
            rules = dict(lesson, accept=st["accept"], expected=st["expected"], check_step=step,
                         mistakes=[m for m in lesson["mistakes"] if m.get("step") in (None, step)])
            for offset in (-0.1, 0.0, 0.1):
                for top in (2.9, 3.1):
                    with self.subTest(step=step, circuit=circuit, offset=offset, top=top):
                        s = station_summary(rules, rows, V, offset, top)
                        self.assertEqual(outcome(check(s, rules)),
                                         "ok" if circuit == "correct" else circuit)

    def test_numbers_and_math_are_for_3_3_volts(self):
        self.assertEqual(LESSONS[1]["expected"]["current_ma"], 5.9)
        self.assertIn("(3.3 - 2) / 220", LESSONS[1]["expected"]["math"])
        self.assertEqual(LESSONS[2]["expected"]["rows"]["T10"], 1.65)
        self.assertEqual(LESSONS[3]["expected"]["current_ma"], 3.9)
        self.assertIn("(3.3 - 2) / 330", LESSONS[3]["expected"]["math"])
        for lid in (1, 2, 3, 4):
            self.assertTrue(LESSONS[lid]["circuit"].startswith("3.3 V ->"))
            self.assertEqual(LESSONS[lid]["expected"]["rows"]["T5"], V)

    def test_no_text_says_5_volts(self):
        five = re.compile(r"(?<![\d.])5(\.0)? ?(V\b|volt)")
        for path in sorted((ROOT / "lessons").glob("lesson*.json")):
            with self.subTest(file=path.name):
                self.assertIsNone(five.search(path.read_text(encoding="utf-8")))

    def test_lessons_use_leds_that_light_at_3_3_volts(self):
        for lid, lesson in LESSONS.items():
            for part in lesson["layout"]:
                name = part["part"].lower()
                if "led" in name:
                    self.assertTrue(any(c in name for c in BRIGHT_AT_3V3), (lid, name))
                    self.assertFalse("blue" in name or "white" in name, (lid, name))


class TestAStationLikeFakeBoard(unittest.TestCase):
    """The fake board set up like the station: every row and the two +
    taps measured (not the - lines), no current sensor, no power switch."""
    STATION = {"rows": "T1-T63,B1-B63,PT+,PB+", "has_current": False,
               "has_power_switch": False}

    def test_every_lesson_circuit(self):
        from sim.fake_board import SCENARIOS
        for lid in (1, 2, 3):
            for scenario in SCENARIOS[lid]:
                with self.subTest(lesson=lid, scenario=scenario):
                    board = FakeBoard(lid, scenario, seed=2, **self.STATION)
                    _, result, cut = Session(board, lesson=lid).measure()
                    want = ("ok" if scenario in ("correct", "resistor_150", "resistor_too_big")
                            else "unknown" if scenario == "bottom_4k7" else scenario)
                    self.assertEqual(outcome(result), want)
                    self.assertFalse(cut)  # nothing can switch it off
                    self.assertEqual(bool(result.get("manual_power_off")),
                                     scenario == "no_resistor")


class TestNoPowerSwitch(unittest.TestCase):
    """The station: pull the red wire out of the + line."""

    def test_screens_fit_without_cutting(self):
        for text in (P.MANUAL_OFF_SCREEN, P.NO_SWITCH_SCREEN):
            with self.subTest(text=text):
                self.assertEqual(P.fit_screen(text), text.split("|"))  # nothing cut off
        self.assertIn("red wire", P.MANUAL_OFF_SCREEN)

    def test_red_button_says_how_on_the_screen_and_the_laptop(self):
        board = FakeBoard(1, has_power_switch=False, has_current=False)
        session = Session(board, lesson=1)
        out = session.toggle_power()
        self.assertEqual(out["say"], NO_SWITCH_SAY)
        self.assertIn("pull the red wire out of the + line", out["say"])
        self.assertIn("push the red wire back into the + line", out["say"])
        self.assertEqual(board.screen, P.NO_SWITCH_SCREEN.split("|"))
        self.assertTrue(board.power_on)

    def test_danger_on_the_station(self):
        board = FakeBoard(1, "no_resistor", seed=1, has_power_switch=False, has_current=False)
        _, result, cut = Session(board, lesson=1).measure()
        self.assertEqual((result["mistake_id"], result["manual_power_off"], cut),
                         ("no_resistor", True, False))
        self.assertEqual(board.screen, P.MANUAL_OFF_SCREEN.split("|"))


class TestButtonColours(unittest.TestCase):
    def test_the_station_colours(self):
        self.assertEqual(P.BUTTON_COLORS, {1: "yellow", 2: "white", 3: "blue", 4: "red"})
        self.assertEqual(P.button_words(1), "the yellow button")

    def test_texts_name_buttons_by_colour(self):
        session = Session(FakeBoard(1), lesson=1)
        self.assertIn("Press the blue button to start building", session.hint()["say"])
        for _ in range(5):
            session.next_step()
        self.assertEqual(session.board.screen, ["All parts placed!", "Press the yellow",
                                                "button to check."])
        self.assertIn("press the red button", P.power_cut_words({"reason": "short"}))

    def test_the_page_draws_the_station_buttons(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        block = re.search(r"const KEY_COLORS = (\{[^}]*\});", html).group(1)
        js = json.loads(re.sub(r"(\d+):", r'"\1":', block))
        self.assertEqual(js, {str(k): v for k, v in P.BUTTON_COLORS.items()})
        for colour, token in (("yellow", "yellow"), ("white", "white"), ("blue", "blue"),
                              ("red", "coral")):
            self.assertIn(f".board-buttons .key.{colour} {{ background: var(--{token}); }}", html)
        self.assertIn("This board runs on 3.3 volts.", html)  # the tray says which LEDs light


class TestSerialAuto(unittest.TestCase):
    def test_prefers_the_s3_usb_port(self):
        ports = [("COM3", "Bluetooth", None), ("COM7", "USB Serial Device", STATION_VID),
                 ("COM9", "USB-SERIAL CH340", 0x1A86)]
        self.assertEqual(STATION_VID, 0x303A)
        self.assertEqual(choose_port("auto", ports=ports), "COM7")
        self.assertEqual(choose_port("auto", ports=[("COM7", "x", STATION_VID)]), "COM7")
        self.assertEqual(choose_port("COM2", ports=ports), "COM2")

    def test_two_s3_boards_or_none_say_so(self):
        with self.assertRaises(BoardError) as e:
            choose_port("auto", ports=[("COM4", "a", STATION_VID), ("COM5", "b", STATION_VID)])
        self.assertIn("More than one ESP32-S3", str(e.exception))
        with self.assertRaises(BoardError) as e:
            choose_port("auto", ports=[("COM3", "Bluetooth", None), ("COM9", "CH340", 0x1A86)])
        self.assertIn("More than one serial port", str(e.exception))


class TestRandomCircuitsAt3V3(unittest.TestCase):
    def test_no_two_leds_in_a_row_and_bright_leds_without_a_resistor(self):
        for seed in range(120):
            for n in (3, 5):
                made = random_circuit(seed, n)
                leds = [p for p in made["parts"] if p["kind"] == "led"]
                if made["mistake"] == "no_resistor":
                    self.assertTrue(all(p["color"] in BRIGHT_AT_3V3 for p in leds), made)
                if made["mistake"] is None:  # never LED to LED in one row (in series)
                    rows = [r for p in leds for r in p["rows"] if P.is_row(r)]
                    self.assertEqual(len(rows), len(set(rows)), made)


if __name__ == "__main__":
    unittest.main()
