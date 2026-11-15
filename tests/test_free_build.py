"""Free build, phase 1: usable rows, the fake board's circuit solver,
random judge circuits, the parts tray and the plan-free free build check.
Lessons 0-4, safety and PASS rules do not change."""

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import protocol as P
from agent.boardlink import BoardLink
from agent.lessons import FREE_ID, load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from agent.tray import TRAY_PARTS, clean_tray
from sim import solver
from sim.fake_board import SCENARIOS, FakeBoard
from sim.random_circuit import MISTAKES, random_circuit
from web.server import App, usable_rows_setting

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
USABLE = "T3-T8,T20-T24,B40-B45"
LED_CIRCUIT = [{"kind": "wire", "rows": ["PT+", "T5"]},
               {"kind": "resistor", "rows": ["T5", "T10"], "ohms": 220},
               {"kind": "led", "rows": ["T10", "T12"], "color": "red"},
               {"kind": "wire", "rows": ["T12", "PT-"]}]


def app(lesson=FREE_ID, usable=None, model=None, **board):
    tmp = tempfile.TemporaryDirectory()
    b = FakeBoard(lesson, "correct", seed=1, **board)
    a = App(b, lesson=lesson, history_path=Path(tmp.name) / "h.json",
            model=model or ScriptedTutorModel(), usable_rows=usable, free_build=True)
    a._tmp = tmp  # kept alive with the app
    return a, b


class TestUsableRows(unittest.TestCase):
    def test_parsing(self):
        rows = P.parse_usable(USABLE)
        self.assertEqual(len(rows), 6 + 5 + 6)
        self.assertEqual(rows[:2], ["T3", "T4"])
        self.assertIn("B45", rows)
        self.assertEqual(P.parse_usable(None), P.ALL_ROWS)
        self.assertEqual(P.parse_usable("all"), P.ALL_ROWS)
        self.assertEqual(P.parse_usable(""), P.ALL_ROWS)
        self.assertEqual(P.parse_usable("PT+,T3"), ["T3"])  # the + and - lines always work
        for bad in ("X9", "T70", "T8-T3", "PT+"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                P.parse_usable(bad)

    def test_flag_then_environment(self):
        env = {P.USABLE_ROWS_ENV: "T1-T4"}
        self.assertEqual(usable_rows_setting("T9", env), "T9")
        self.assertEqual(usable_rows_setting(None, env), "T1-T4")
        self.assertIsNone(usable_rows_setting(None, {}))

    def test_rows_outside_are_never_measured_or_lit(self):
        board = FakeBoard(1, "correct", seed=1)
        link = BoardLink(board, "T3-T8")
        reading = link.read()
        rows = {r for r in reading["rows"] if P.is_row(r)}
        self.assertEqual(rows, {f"T{n}" for n in range(3, 9)})
        self.assertIn("PT+", reading["rows"])  # the + and - lines still read
        self.assertTrue(set(reading["floating"]) <= set(reading["rows"]))
        self.assertNotIn("T10", reading["floating"])  # not "empty": not there at all
        self.assertEqual(link.set_light("T10", "red")["type"], "error")
        self.assertEqual(link.set_light("T5", "green")["type"], "ack")
        self.assertEqual(board.lights, {"T5": "green"})
        self.assertEqual(link.usable_rows, [f"T{n}" for n in range(3, 9)])
        self.assertEqual(BoardLink(board).usable, None)  # default: every row

    def test_only_rows_the_board_measures(self):
        board = FakeBoard(FREE_ID, "correct", seed=1, rows="T1-T16")
        self.assertEqual(BoardLink(board).usable_rows, [f"T{n}" for n in range(1, 17)])
        self.assertEqual(BoardLink(board, "T10-T30").usable_rows, [f"T{n}" for n in range(10, 17)])
        a, _ = app(rows="T1-T16")
        made = a.random_circuit(4, 5)
        rows = {r for p in made["netlist"] for r in p["rows"] if P.is_row(r)}
        self.assertTrue(rows <= {f"T{n}" for n in range(1, 17)}, rows)

    def test_a_lesson_on_rows_that_do_not_work_cannot_be_checked(self):
        session = Session(BoardLink(FakeBoard(1, "correct", seed=1), "T3-T8"), lesson=1)
        _, result, _ = session.measure()
        self.assertEqual(result["status"], "missing_rows")  # the existing honest answer

    def test_state(self):
        a, _ = app(usable=USABLE)
        self.assertEqual(a.state()["usable_rows"], P.parse_usable(USABLE))
        a2, _ = app()
        self.assertEqual(len(a2.state()["usable_rows"]), 126)

    def test_page_greys_unusable_rows(self):
        for marker in ("state.usable_rows", 'class: "bb-off"', ".bb-off { fill: var(--line); }",
                       "if (works(\"T\" + n))", "breaks.forEach", ".drawing.wide"):
            self.assertIn(marker, HTML)


class TestSolver(unittest.TestCase):
    def test_led_with_resistor(self):
        out = solver.solve(LED_CIRCUIT)
        want = (5 - 1.9) / (220 + 15) * 1000  # 13.2 mA
        self.assertAlmostEqual(out["current_ma"], want, delta=0.05)
        self.assertAlmostEqual(out["rows"]["T5"], 5.0, places=2)
        self.assertAlmostEqual(out["rows"]["T10"], 1.9 + want / 1000 * 15, delta=0.01)
        self.assertAlmostEqual(out["rows"]["T12"], 0.0, places=2)
        self.assertEqual(out["leds_on"], {2: True})

    def test_voltage_divider(self):
        out = solver.solve([{"kind": "resistor", "rows": ["PT+", "T10"], "ohms": 10000},
                            {"kind": "resistor", "rows": ["T10", "PT-"], "ohms": 10000}])
        self.assertAlmostEqual(out["rows"]["T10"], 2.5, places=3)
        self.assertAlmostEqual(out["current_ma"], 0.25, places=3)

    def test_led_backwards(self):
        parts = [dict(p) for p in LED_CIRCUIT]
        parts[2] = dict(parts[2], rows=["T12", "T10"])
        out = solver.solve(parts)
        self.assertAlmostEqual(out["current_ma"], 0.0, places=6)
        self.assertAlmostEqual(out["rows"]["T10"], 5.0, places=2)  # no current: no drop
        self.assertEqual(out["leds_on"], {2: False})

    def test_two_leds_in_parallel_each_with_its_own_330(self):
        out = solver.solve([{"kind": "resistor", "rows": ["PT+", "T10"], "ohms": 330},
                            {"kind": "led", "rows": ["T10", "PT-"], "color": "red"},
                            {"kind": "resistor", "rows": ["PT+", "T20"], "ohms": 330},
                            {"kind": "led", "rows": ["T20", "PT-"], "color": "red"}])
        each = (5 - 1.9) / (330 + 15) * 1000  # 8.99 mA
        self.assertAlmostEqual(out["current_ma"], 2 * each, delta=0.01)
        self.assertAlmostEqual(out["rows"]["T10"], out["rows"]["T20"], places=6)

    def test_colours_buttons_and_empty_rows(self):
        blue = [dict(p) for p in LED_CIRCUIT]
        blue[2] = dict(blue[2], color="blue")
        self.assertAlmostEqual(solver.solve(blue)["current_ma"], 2.0 / 235 * 1000, delta=0.05)
        button = LED_CIRCUIT[:1] + [{"kind": "button", "rows": ["T5", "T6"], "closed": False},
                                    dict(LED_CIRCUIT[1], rows=["T6", "T10"])] + LED_CIRCUIT[2:]
        out = solver.solve(button)
        self.assertEqual(out["current_ma"], 0.0)
        self.assertIsNone(out["rows"]["T6"])  # joined to nothing that conducts: floating
        button[1]["closed"] = True
        self.assertGreater(solver.solve(button)["current_ma"], 13)
        out = solver.solve([{"kind": "resistor", "rows": ["PT+", "T5"], "ohms": 220},
                            {"kind": "led", "rows": ["T7", "T9"]}])
        self.assertEqual((out["rows"]["T7"], out["rows"]["T9"]), (None, None))

    def test_current_limit_and_short(self):
        out = solver.solve([{"kind": "led", "rows": ["PT+", "PT-"]}])
        self.assertAlmostEqual(out["current_ma"], solver.CURRENT_LIMIT_MA, delta=0.1)
        self.assertLess(out["supply_v"], 5.0)  # the supply is pulled down
        out = solver.solve([{"kind": "wire", "rows": ["PT+", "PT-"]}])
        self.assertAlmostEqual(out["current_ma"], solver.CURRENT_LIMIT_MA, delta=0.1)
        self.assertLess(out["supply_v"], 0.1)

    def test_bad_netlists(self):
        for bad in ([{"kind": "rock", "rows": ["T1", "T2"]}],
                    [{"kind": "wire", "rows": ["T1"]}],
                    [{"kind": "wire", "rows": ["T1", "Q9"]}],
                    [{"kind": "resistor", "rows": ["T1", "T2"]}],
                    [{"kind": "led", "rows": ["T1", "T2"], "color": "purple"}], "nope"):
            with self.subTest(bad=bad), self.assertRaises(solver.NetlistError):
                solver.solve(bad)


class TestFakeBoardNetlist(unittest.TestCase):
    def test_netlist_command(self):
        board = FakeBoard(FREE_ID, "correct", seed=1, noise=False)
        self.assertEqual(board.send("NETLIST " + json.dumps({"parts": LED_CIRCUIT}))["type"], "ack")
        r = board.read()
        self.assertAlmostEqual(r["current_ma"], 13.2, delta=0.1)
        self.assertNotIn("T10", r["floating"])
        self.assertIn("T30", r["floating"])
        self.assertEqual(board.send("NETLIST {bad")["type"], "error")
        self.assertEqual(board.send("NETLIST CLEAR")["type"], "ack")
        self.assertIsNone(board.netlist)
        board.set_netlist(LED_CIRCUIT)
        board.send(f"SCENARIO {FREE_ID} correct")
        self.assertIsNone(board.netlist)  # back to the lesson's circuit

    def test_hold_presses_a_netlist_button(self):
        board = FakeBoard(FREE_ID, "correct", seed=1, noise=False)
        board.set_netlist([{"kind": "wire", "rows": ["PT+", "T5"]},
                           {"kind": "button", "rows": ["T5", "T6"]},
                           dict(LED_CIRCUIT[1], rows=["T6", "T10"])] + LED_CIRCUIT[2:])
        self.assertEqual(board.read()["current_ma"], 0.0)
        board.send("HOLD on")
        self.assertGreater(board.read()["current_ma"], 13)


class TestRandomCircuits(unittest.TestCase):
    def test_only_usable_rows_and_plausible(self):
        usable = P.parse_usable(USABLE)
        seen = set()
        for seed in range(150):
            for n in range(2, 6):
                made = random_circuit(seed, n, usable)
                rows = {r for p in made["parts"] for r in p["rows"] if P.is_row(r)}
                self.assertTrue(rows <= set(usable), (seed, n, rows))
                seen.add(made["mistake"])
                current = solver.solve(made["parts"])["current_ma"]
                if made["mistake"] is None:
                    self.assertLess(current, 25, made)  # a right circuit never trips the cut
                if made["mistake"] == "no_resistor":
                    self.assertGreater(current, 30, made)  # the safety cut must catch it
        self.assertEqual(seen, set(MISTAKES) | {None})

    def test_same_seed_same_circuit(self):
        self.assertEqual(random_circuit(5, 4), random_circuit(5, 4))

    def test_api(self):
        a, board = app(usable=USABLE)
        out = a.random_circuit(3, 4)
        self.assertTrue(out["ok"])
        self.assertEqual(out["netlist"], board.netlist)
        self.assertIn("never sent to the tutor", out["note"])
        self.assertIn("error", a.random_circuit(3, 9))
        self.assertIn("error", a.random_circuit(3, "x"))
        self.assertNotIn("netlist", json.dumps(a.state()))  # the page state does not carry it


class Recorder(BaseChatModel):
    """A fake tutor that measures once and keeps everything it was given."""
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append("\n".join(str(m.content) for m in messages))
        last = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        if not any(isinstance(m, AIMessage) for m in messages[last:]):
            msg = AIMessage(content="", tool_calls=[{"name": "read_rows", "args": {}, "id": "r"}])
        else:
            msg = AIMessage(content="I measured it.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


class TestFreeBuild(unittest.TestCase):
    def test_true_netlist_never_reaches_the_tutor(self):
        model = Recorder()
        a, board = app(usable=USABLE, model=model)
        made = a.random_circuit(11, 4)
        a.ask("Check my circuit")
        everything = "\n".join(model.seen)
        self.assertIn("free", everything)  # it did see the check
        self.assertNotIn("netlist", everything)
        self.assertNotIn('"ohms"', everything)
        self.assertNotIn('"kind": "resistor"', everything)
        self.assertIsNone(a.session.tray)
        self.assertFalse(hasattr(a.session, "netlist"))
        self.assertTrue(made["netlist"])

    def test_check_with_no_plan(self):
        a, board = app(usable=USABLE)
        board.set_netlist([{"kind": "wire", "rows": ["PT+", "T5"]},
                           {"kind": "resistor", "rows": ["T5", "T21"], "ohms": 220},
                           {"kind": "led", "rows": ["T21", "T22"], "color": "red"},
                           {"kind": "wire", "rows": ["T22", "PT-"]}])
        out = a.ask("Check my circuit")
        summary, result = a.session.last_check
        self.assertEqual(result["status"], "free")
        self.assertEqual(set(result["measured_rows"]), {"T5", "T21", "T22"})
        self.assertAlmostEqual(result["current_ma"], 13.2, delta=0.3)
        kinds = [f["kind"] for f in result["facts"]]
        self.assertEqual(kinds[0], "power")
        self.assertEqual(kinds.count("row_voltage"), 3)
        self.assertEqual(kinds[-1], "current")
        self.assertFalse({"part_drop", "led_state", "extra_rows", "same_voltage"} & set(kinds))
        self.assertTrue(board.power_on)
        self.assertIn("I measured 3 rows", out["reply"])
        self.assertFalse(a.session.passed_current)  # free build has no PASS
        self.assertEqual(a.session.next_lesson()["lesson"], FREE_ID)  # stays: nothing to pass
        self.assertIn("Free build", a.session.hint()["say"])

    def test_led_without_resistor_is_cut(self):
        for sensor in (True, False):
            with self.subTest(current_sensor=sensor):
                a, board = app(usable=USABLE, has_current=sensor)
                board.set_netlist([{"kind": "wire", "rows": ["PT+", "T4"]},
                                   {"kind": "led", "rows": ["T4", "T6"], "color": "red"},
                                   {"kind": "wire", "rows": ["T6", "PT-"]}])
                out = a.ask("Check my circuit")
                self.assertFalse(board.power_on)
                self.assertIn("switched the power off", out["reply"])
                _, result = a.session.last_check
                self.assertEqual(result["action"], "cut_power")
                safety = [i for i in a.state()["activity"] if i["agent"] == "Python safety"
                          and i["kind"] == "act on board"]
                self.assertTrue(safety)

    def test_random_mistake_no_resistor_is_cut(self):
        a, board = app(usable=USABLE)
        made = next(m for m in (random_circuit(s, 3, a.board.usable_rows) for s in range(200))
                    if m["mistake"] == "no_resistor")
        board.set_netlist(made["parts"])
        a.ask("Check my circuit")
        self.assertFalse(board.power_on)

    def test_path_title_and_instant_help(self):
        a, board = app()
        path = a.state()["path"]
        self.assertEqual([p["id"] for p in path], [0, 1, 2, 3, 4, FREE_ID])
        self.assertEqual((path[-1]["kind"], path[-1]["title"]), ("free", "Free build"))
        self.assertEqual(board.screen, ["Genesis", "Free build", "Build anything!"])
        a.instant = True
        self.assertIsNone(a.instant_tick())  # instant help waits for phase 2

    def test_tray(self):
        a, _ = app()
        out = a.set_tray([{"name": "red LED", "count": 2}, {"name": "wire", "count": 3},
                          {"name": "red LED"}, {"name": "other", "text": "  a  buzzer "}],
                         "  the LED lights up  ")
        self.assertEqual(out["tray"], {"parts": [{"name": "wire", "count": 3},
                                                 {"name": "red LED", "count": 3}],
                                       "other": "a buzzer", "goal": "the LED lights up"})
        self.assertEqual(a.state()["tray"], out["tray"])
        self.assertEqual(a.session.tray, out["tray"])
        self.assertEqual(a.state()["tray_parts"], TRAY_PARTS)
        for bad in ([{"name": "rocket"}], [{"name": "wire", "count": 99}], "x", [5]):
            with self.subTest(bad=bad):
                self.assertIn("error", a.set_tray(bad))
        self.assertEqual(len(clean_tray([], "x" * 500)["goal"]), 200)

    def test_page_has_the_tray_and_the_free_stop(self):
        for marker in ('id="tray"', "What parts did you use?", "What should it do?",
                       'id="tray-chips"', 'id="tray-other"', 'id="tray-goal"', 'id="tray-save"',
                       '"/api/tray"', "state.tray_parts", 'p.kind === "free"',
                       "Your board: every row that works"):
            self.assertIn(marker, HTML)


class TestLessonsUnchanged(unittest.TestCase):
    def test_lessons_0_to_4(self):
        self.assertEqual(sorted(load_lessons()), [0, 1, 2, 3, 4])
        self.assertEqual(sorted(SCENARIOS), [0, 1, 2, 3, 4])
        self.assertNotIn(FREE_ID, Session(FakeBoard(1, "correct", seed=1), lesson=1).lessons)
        session = Session(FakeBoard(1, "correct", seed=1), lesson=1)
        self.assertEqual(session.measure()[1]["status"], "ok")
        session = Session(FakeBoard(1, "no_resistor", seed=1), lesson=1)
        _, result, cut = session.measure()
        self.assertEqual((result["status"], result["mistake_id"], cut), ("mistake", "no_resistor", True))

    def test_free_build_is_never_picked_next(self):
        session = Session(FakeBoard(4, "correct", seed=1), lesson=4, free_build=True)
        session.history["passes"] = {str(i): 1 for i in range(5)}
        session.passed_current = True
        self.assertNotEqual(session.next_lesson()["lesson"], FREE_ID)
        self.assertNotEqual(Session(FakeBoard(1, "correct", seed=1), lesson="start",
                                    free_build=True).lesson_id, FREE_ID)


if __name__ == "__main__":
    unittest.main()
