"""Genesis on the station (jinglebells2020/smartboard, docs/HARDWARE.md):
a 3.3 V supply, a meter that tops out at about 2.9 to 3.1 V (anything
above is sent as the supply) and is good to about 0.1 V, no current
sensor, no power switch, and buttons with colours instead of numbers.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

import protocol as P
from agent.boardlink import BoardLink
from agent.build import ask, build_agent
from agent.lessons import check, free_check, load_lessons, summarize
from agent.mock_model import ScriptedTutorModel
from agent.physics import observations, power_fact
from agent.reader import physics_check
from agent.serial_board import STATION_VID, BoardError, choose_port
from agent.session import NO_SWITCH_SAY, UNMAPPED_SCREEN, Session
from sim.fake_board import FakeBoard
from sim.random_circuit import BRIGHT_AT_3V3, random_circuit
from tests.test_instant import Clock, InstantApp
from tests.test_rows_checking import CIRCUITS, FLOAT, UNLISTED, reading
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
V = P.SUPPLY_V

# Lesson 4, the button: true row voltages per step (no current sensor).
BUTTON = {
    ("pressed", "correct"): {"T2": V, "T5": V, "T10": 2.0, "T12": 0},
    ("pressed", "led_backwards"): {"T2": V, "T5": V, "T10": V, "T12": 0},
    ("pressed", "open_circuit"): {"T2": V, "T5": V, "T10": FLOAT, "T12": 0},
    ("released", "correct"): {"T2": V, "T5": FLOAT, "T10": FLOAT, "T12": 0},
    ("released", "button_always_on"): {"T2": V, "T5": V, "T10": 2.0, "T12": 0},
}
# The station's meter top (its clip point: the rail tap's saturated reading
# minus 100 mV, 2850 mV with no calibration): about 2.8 to 3.1 V.
TOPS = (2.8, 2.85, 2.9, 3.0, 3.1)


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
                    for top in TOPS:
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
                for top in TOPS:
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
        for text in (P.MANUAL_OFF_SCREEN, P.NO_SWITCH_SCREEN, P.HAND_OFF_SCREEN):
            with self.subTest(text=text):
                self.assertEqual(P.fit_screen(text), text.split("|"))  # nothing cut off
        # Every wire: lesson 3 has one red wire per copy, free build any wire.
        self.assertEqual(P.MANUAL_OFF_SCREEN, "DANGER!|Pull every wire|out of the + line|right now!")
        self.assertEqual(P.MANUAL_OFF_WORDS, "pull every wire out of the + line")

    def test_red_button_says_how_on_the_screen_and_the_laptop(self):
        board = FakeBoard(1, has_power_switch=False, has_current=False)
        session = Session(board, lesson=1)
        out = session.toggle_power()
        self.assertEqual(out["say"], NO_SWITCH_SAY)
        self.assertIn("pull every wire out of the + line", out["say"])
        self.assertIn("push the wires back into the + line", out["say"])
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


STATION_OPTS = {"rows": "T1-T63,B1-B63,PT+,PB+", "has_current": False,
                "has_power_switch": False}
SAFETY = "First look at the LED. If it is very bright or feels warm, pull every wire out of " \
         "the + line right now"
NO_SENSOR_CLAIMS = re.compile(r"\b(almost )?no current\b", re.IGNORECASE)


class StiffStation(FakeBoard):
    """The station with a supply that holds 3.3 V (no sag, no current
    sensor, no power switch): it reads exactly the rows it is given, every
    other row empty. An LED with no resistor reads like this."""

    def __init__(self, lesson, rows, **kw):
        super().__init__(lesson, seed=1, **dict(STATION_OPTS, **kw))
        self.now = dict(rows)

    def read(self):
        r = super().read()
        rows = dict(self.now, **{"PT+": V})
        r["rows"] = {n: (0.0 if rows.get(n, FLOAT) is FLOAT else rows[n]) for n in self.rows}
        r["floating"] = [n for n in self.rows if rows.get(n, FLOAT) is FLOAT]
        r["supply_v"] = V
        return r


# An LED with no resistor on a supply that holds 3.3 V (the station), and the
# mistake whose rows it fits: a wire in the resistor's place reads like a
# blocked LED; the LED's long leg pushed into the row before the resistor
# leaves the LED's row empty, like an open circuit.
LOOK_ALIKES = {
    1: {"led_backwards": {"T5": V, "T10": V, "T12": 0.0},
        "open_circuit": {"T5": V, "T10": FLOAT, "T12": 0.0}},
    3: {"led_backwards": {"T5": V, "T10": V, "T12": 0.0},
        "open_circuit": {"T5": V, "T10": FLOAT, "T12": 0.0}},
    4: {"led_backwards": {"T2": V, "T5": V, "T10": V, "T12": 0.0},  # the button held
        "open_circuit": {"T2": V, "T5": V, "T10": FLOAT, "T12": 0.0}},
}


class TestAStiffSupplyWithNoCurrentSensor(unittest.TestCase):
    """Round 2 (G3): on the station an LED with no resistor does not sag the
    supply, so it reads like a blocked LED or an open circuit. Without a
    current sensor every such hint starts with a check the student can see,
    names a missing resistor, and never claims that no current flows."""

    def test_the_safety_sentence_comes_first_for_both_look_alikes(self):
        for lid, cases in LOOK_ALIKES.items():
            for mistake_id, rows in cases.items():
                with self.subTest(lesson=lid, mistake=mistake_id):
                    board = StiffStation(lid, rows)
                    with tempfile.TemporaryDirectory() as tmp:
                        session = Session(board, lesson=lid, history_path=Path(tmp) / "h.json")
                        agent = build_agent(session, ScriptedTutorModel())
                        reply, _ = ask(agent, [], "Check my circuit")
                    self.assertEqual(session.last_check[1]["mistake_id"], mistake_id)
                    self.assertIn(SAFETY, reply)
                    self.assertTrue(re.search(r"resistor may be missing|skip the resistor", reply))
                    self.assertIsNone(NO_SENSOR_CLAIMS.search(reply), reply)
                    # The short version on the board screen (4 x 20, nothing cut).
                    self.assertEqual(board.screen, ["LED bright or warm?", "Pull every wire",
                                                    "out of the + line!"])
                    # The next level of help (the hint button) says it too.
                    explain = session.hint()["say"]
                    self.assertIn("pull every wire out of the + line first", explain)
                    self.assertIsNone(NO_SENSOR_CLAIMS.search(explain), explain)

    def test_the_led_fact_and_instant_help_say_it_too(self):
        board = StiffStation(1, LOOK_ALIKES[1]["led_backwards"])
        s = summarize([board.read() for _ in range(3)], LESSONS[1])
        led = next(f for f in observations(s, LESSONS[1]) if f["kind"] == "led_state")
        self.assertEqual(led["values"]["state"], "blocked")
        self.assertTrue(led["text"].startswith(SAFETY), led["text"])
        self.assertIsNone(NO_SENSOR_CLAIMS.search(led["text"]))
        # Instant help, the moment the circuit appears: the same words first.
        with tempfile.TemporaryDirectory() as tmp:
            board = StiffStation(1, {})
            app = App(board, lesson=1, history_path=Path(tmp) / "h.json",
                      model=ScriptedTutorModel(), instant=True)
            app.instant_inline = app.auto_inline = True
            clock = Clock()
            app.live.clock = clock

            def settle():
                for _ in range(10):
                    clock.t += 0.25
                    app.instant_tick()
            settle()
            board.now = dict(LOOK_ALIKES[1]["led_backwards"])
            settle()
        said = [m["text"] for m in app.log if m["who"] == "tutor"]
        self.assertTrue(said and said[0].startswith(SAFETY), said)
        self.assertTrue(all(NO_SENSOR_CLAIMS.search(t) is None for t in said), said)

    def test_with_a_current_sensor_the_hints_stay_as_they_were(self):
        board = FakeBoard(1, "led_backwards", seed=1)  # current sensor: 0 mA measured
        session = Session(board, lesson=1)
        session.measure()
        out = session.log_mistake("led_backwards")
        self.assertEqual(out["say"], next(m for m in LESSONS[1]["mistakes"]
                                          if m["id"] == "led_backwards")["hint"])
        self.assertNotIn("screen", out)
        s = summarize([board.read() for _ in range(3)], LESSONS[1])
        led = next(f for f in observations(s, LESSONS[1]) if f["kind"] == "led_state")
        self.assertIn("almost no current flows", led["text"])  # measured, so it is a fact

    def test_every_look_alike_mistake_has_its_no_sensor_texts(self):
        for lid in (1, 3, 4):
            for m in LESSONS[lid]["mistakes"]:
                if m["id"] not in ("led_backwards", "open_circuit"):
                    continue
                with self.subTest(lesson=lid, mistake=m["id"]):
                    ns = m["no_sensor"]
                    self.assertTrue(ns["hint"].startswith(("First look at the LED.",
                                                           "You are holding the button. First")))
                    self.assertIn("pull every wire out of the + line right now", ns["hint"])
                    self.assertIn("resistor", ns["description"])
                    for text in (ns["hint"], ns["explain"], ns["description"]):
                        self.assertIsNone(NO_SENSOR_CLAIMS.search(text), text)
                    self.assertEqual(P.fit_screen(ns["screen"]), ns["screen"].split("|"))


class TestPowerOffByHand(InstantApp):
    """Round 2 (G4): no power switch. After Genesis asks for the power to be
    switched off by hand, the + line going empty is the power off: Genesis
    says so, keeps the mistake open, and the same mistake coming back is not
    a new one."""

    def check(self, session, agent, history):
        reply, _ = ask(agent, history, "Check my circuit")
        return reply

    def test_instant_help_off(self):
        board = FakeBoard(1, "no_resistor", seed=1, **STATION_OPTS)
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(board, lesson=1, history_path=Path(tmp) / "h.json")
            agent = build_agent(session, ScriptedTutorModel())
            history = []
            self.assertTrue(self.check(session, agent, history).startswith(
                "Switch the power off by hand right now: pull every wire out of the + line"))
            for how in ("ALL", "WIRES"):  # the feed too, or only the circuit's wires
                with self.subTest(unplug=how):
                    board.send(f"UNPLUG {how}")
                    self.assertEqual(self.check(session, agent, history), P.HAND_OFF_SAY)
                    self.assertEqual(session.open_mistake["mistake"], "no_resistor")  # kept
                    self.assertEqual(board.screen, P.HAND_OFF_SCREEN.split("|"))
                    self.assertTrue(board.power_on)
                    board.send("UNPLUG NONE")  # pushed back in, but not fixed
                    self.assertTrue(self.check(session, agent, history).startswith(
                        "Switch the power off by hand right now"))
            self.assertEqual([m["mistake"] for m in session.history["mistakes"]],
                             ["no_resistor"])  # one mistake, not three
            board.send("SCENARIO 1 correct")  # fixed, and pushed back in
            self.assertIn("Your circuit is correct!", self.check(session, agent, history))
            self.assertIsNone(session.manual_off)

    def test_instant_help_on(self):
        self.make("no_resistor", **STATION_OPTS)
        self.step("BUILD 4")
        texts = [m["text"] for m in self.said()]
        self.assertTrue(any(t.startswith("Switch the power off by hand") for t in texts), texts)
        self.step("UNPLUG ALL")
        self.assertEqual(self.said()[-1]["text"], P.HAND_OFF_SAY)
        self.step("BUILD 3")  # the student works on the circuit with the power off
        self.assertEqual(self.said()[-1]["text"], P.HAND_OFF_SAY)  # said once
        self.assertNotIn("That fixed it.", [m["text"] for m in self.said()])
        self.step("BUILD 4")
        self.step("UNPLUG NONE")  # pushed back in, not fixed: the danger again
        self.assertTrue(self.said()[-1]["text"].startswith("Switch the power off by hand"))
        self.assertNotIn("That fixed it.", [m["text"] for m in self.said()])
        s = self.app.session
        self.assertEqual([m["mistake"] for m in s.history["mistakes"]], ["no_resistor"])
        self.assertEqual(s.open_mistake["mistake"], "no_resistor")
        self.step("SCENARIO 1 correct")  # now it is fixed
        self.assertIn("Your circuit is correct!", self.said()[-1]["text"])

    def test_the_unplugged_line_is_never_a_sagging_supply(self):
        # supply_v 0 and the + taps floating: power off, whatever the rows say.
        rows = {"T5": 0.0, "T10": 2.0, "T12": 0.0, "PT+": FLOAT, "PB+": FLOAT}
        r = reading(rows, 0.0, 0.0, with_sensor=False)
        for lid in (1, 3, 4):
            with self.subTest(lesson=lid):
                result = check(summarize([r], LESSONS[lid]), LESSONS[lid])
                self.assertEqual(result, {"status": "power_off", "by_hand": True})
        free = free_check(summarize([r], {"rows": ["T5", "T10", "T12"]}))
        self.assertEqual(free, {"status": "power_off", "by_hand": True})  # no cut_power
        fact = power_fact(summarize([r], LESSONS[1]))
        self.assertIn("off by hand", fact["text"])
        # A + tap that still reads 3.3 V is not unplugged: a sag is still a sag.
        sag = reading({"T5": 2.2, "T10": 2.2, "T12": 0.0, "PT+": 2.2}, 0.0, 2.2,
                      with_sensor=False)
        self.assertEqual(check(summarize([sag], LESSONS[1]), LESSONS[1])["mistake_id"],
                         "no_resistor")

    def test_unplugged_with_nobody_asking_is_just_power_off(self):
        board = FakeBoard(1, "correct", seed=1, **STATION_OPTS)
        board.send("UNPLUG ALL")
        _, result, _ = Session(board, lesson=1).measure()
        self.assertEqual((result["status"], result["by_hand"]), ("power_off", True))
        self.assertNotIn("asked", result)
        self.assertIn("push the wires back into the + line", result["message"])


class TestMeterTopAndWindows(unittest.TestCase):
    def test_lesson_2_top_1k_on_a_clipping_meter_names_both_causes(self):
        for top, want in ((2.8, "bottom_missing"), (2.85, "bottom_missing"),
                          (2.9, "bottom_missing"), (3.1, "top_1k")):
            with self.subTest(top=top):
                board = FakeBoard(2, "top_1k", seed=1, meter_top=top, **STATION_OPTS)
                _, result, _ = Session(board, lesson=2).measure()
                self.assertEqual(outcome(result), want)
        m = next(m for m in LESSONS[2]["mistakes"] if m["id"] == "bottom_missing")
        for text in (m["description"], m["hint"], m["explain"]):
            self.assertIn("top resistor may be much smaller", text)
            self.assertIsNone(NO_SENSOR_CLAIMS.search(text), text)
        self.assertIn("color bands", m["hint"])

    def test_the_led_row_can_be_anywhere_in_its_window(self):
        """No current sensor: the current is worked out from the rows, and the
        whole LED row window must pass (rows at the station's 3.3 V)."""
        cases = {1: ("T10", {"T5": V, "T12": 0.0}, LESSONS[1]),
                 3: ("T10", {"T5": V, "T12": 0.0}, LESSONS[3]),
                 4: ("T10", {"T2": V, "T5": V, "T12": 0.0}, None)}
        for lid, (row, rest, lesson) in cases.items():
            if lesson is None:  # lesson 4: the pressed step's rules
                st = LESSONS[4]["states"]["pressed"]
                lesson = dict(LESSONS[4], accept=st["accept"], expected=st["expected"],
                              check_step="pressed",
                              mistakes=[m for m in LESSONS[4]["mistakes"]
                                        if m.get("step") in (None, "pressed")])
            window = lesson["accept"]["rows"][row]
            self.assertEqual((window["min"], window["max"]), (1.5, 2.4))
            v = window["min"]
            while v <= window["max"] + 1e-9:
                with self.subTest(lesson=lid, led_row=round(v, 2)):
                    r = reading(dict(rest, **{row: round(v, 2)}), 0.0, V, with_sensor=False)
                    self.assertEqual(outcome(check(summarize([r], lesson), lesson)), "ok")
                v += 0.01


class TestUnmappedStation(unittest.TestCase):
    """Round 2 (G9): the station before its row map is flashed (hello rows [])."""

    def test_genesis_says_what_to_do_not_row_names(self):
        board = FakeBoard(1, seed=1, rows=[], has_current=False, has_power_switch=False)
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(board, lesson=1, history_path=Path(tmp) / "h.json")
            self.assertTrue(session.board.unmapped)
            agent = build_agent(session, ScriptedTutorModel())
            reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("row map is not flashed yet", reply)
        self.assertIn("mux test, then make_fw_maps.py, then flash", reply)
        self.assertIsNone(re.search(r"\b[TB]\d+\b", reply), reply)
        self.assertEqual(board.screen, P.fit_screen(UNMAPPED_SCREEN))
        self.assertEqual(session.history["mistakes"], [])
        self.assertFalse(Session(FakeBoard(1, seed=1), lesson=1).board.unmapped)

    def test_the_page_says_it_too(self):
        board = FakeBoard(1, seed=1, rows=[], has_current=False, has_power_switch=False)
        with tempfile.TemporaryDirectory() as tmp:
            app = App(board, lesson=1, history_path=Path(tmp) / "h.json",
                      model=ScriptedTutorModel())
            self.assertTrue(app.state()["board"]["unmapped"])
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("The station's row map is not flashed yet: run the mux test, then "
                      "make_fw_maps.py, then flash the firmware again.", html)
        for lid in (1, 4):  # a lesson with steps adds no "press the button" to it
            with self.subTest(lesson=lid):
                _, result, _ = Session(FakeBoard(lid, seed=1, rows=[]), lesson=lid).measure()
                self.assertEqual((result["status"], result["unmapped"]), ("missing_rows", True))
                self.assertNotIn("next_step", result)


class TestFreeBuildMeterTop(unittest.TestCase):
    def test_a_row_at_the_supply_fits_any_prediction_near_the_top(self):
        parts = [{"kind": "wire", "rows": ["PT+", "T5"]},
                 {"kind": "resistor", "rows": ["T5", "T10"], "ohms": 1500},
                 {"kind": "resistor", "rows": ["T10", "T15"], "ohms": 10000},
                 {"kind": "wire", "rows": ["T15", "PT-"]}]

        def measured(t10):
            return {"rows": {"T5": V, "T10": t10, "T15": 0.0}, "current_ma": None,
                    "current_source": "calculated", "supply_v": V, "nominal_v": V}
        self.assertEqual(physics_check(parts, measured(2.87))["score"], 1.0)
        self.assertEqual(physics_check(parts, measured(V))["score"], 1.0)  # what the station sends
        self.assertLess(physics_check(parts, measured(2.0))["score"], 1.0)
        # Below the meter's lowest top (2.8 V) a prediction must match the number.
        low = [dict(p, ohms=4700) if p.get("ohms") == 1500 else p for p in parts]
        self.assertLess(physics_check(low, measured(V))["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
