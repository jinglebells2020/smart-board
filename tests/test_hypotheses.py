"""Free build: several hypotheses, the experiment planner, and keeping the
hypothesis that came true. The circuits from a real test on a board with
usable rows T3-T8, T20-T24, B40-B45, where NO current flows."""

import json
import tempfile
import unittest
from pathlib import Path

import protocol as P
from agent.boardlink import BoardLink
from agent.build import answer_experiment, ask, build_agent
from agent.detective import ScriptedDetectiveModel
from agent.lessons import FREE_ID
from agent.mock_model import ScriptedTutorModel
from agent.planner import plan_experiment, which_hypothesis
from agent.reader import (HeuristicReaderModel, clean_guess, make_reader, measured_of,
                          physics_check, rank)
from agent.session import Session
from agent.tray import clean_tray
from sim import solver
from sim.fake_board import FakeBoard
from sim.random_circuit import random_circuit
from tests.test_reader import FakeReader
from tests.test_zip_acts import ScriptModel
from web.server import App

USABLE = "T3-T8,T20-T24,B40-B45"
# Seed 1: wire + -> B43, 330 ohm B43-B45, blue LED in backwards (long leg in the - line).
SEED1 = [{"kind": "wire", "rows": ["PB+", "B43"]},
         {"kind": "resistor", "rows": ["B43", "B45"], "ohms": 330},
         {"kind": "led", "rows": ["PB-", "B45"], "color": "blue"}]
SEED1_FIXED = SEED1[:2] + [{"kind": "led", "rows": ["B45", "PB-"], "color": "blue"}]
# Seed 4: push button + -> B40, 330 ohm B40-B41, green LED in backwards.
SEED4 = [{"kind": "button", "rows": ["PB+", "B40"]},
         {"kind": "resistor", "rows": ["B40", "B41"], "ohms": 330},
         {"kind": "led", "rows": ["PB-", "B41"], "color": "green"}]
TRAY4 = ["push button", "330 ohm resistor", "green LED"]
TWO_WIRES = {"parts": [{"kind": "wire", "rows": ["PB+", "B43"]},
                       {"kind": "wire", "rows": ["PB+", "B45"]}]}


def session(netlist, reader=None, tray=None, hold=False):
    board = FakeBoard(FREE_ID, "correct", seed=1)
    board.set_netlist(netlist)
    if hold:
        board.send("HOLD on")
    s = Session(BoardLink(board, USABLE), lesson=FREE_ID, free_build=True)
    s.reader = make_reader(s, reader or HeuristicReaderModel())
    s.tray = clean_tray(tray, "the LED lights up") if tray is not None else None
    return board, s


def problems(result):
    return [p for h in result["hypotheses"] for p in h["problems"]]


class TestHypotheses(unittest.TestCase):
    def test_seed1_led_backwards_is_found(self):
        _, s = session(SEED1)
        _, result, cut = s.measure()
        self.assertFalse(cut)
        self.assertEqual(result["current_ma"] < 0.5, True)
        self.assertGreaterEqual(len(result["hypotheses"]), 2)
        self.assertTrue(any("is blocking the current" in p for p in problems(result)))
        main = result["main_problem"]
        self.assertEqual(main["kind"], "led_backwards")
        # (no tray, no current: the board cannot know its colour)
        self.assertRegex(main["text"], r"^The \w+ LED between row 45 and the - line is blocking "
                                       r"the current: which way does its long leg go\?$")
        self.assertEqual(main["light"], "red")
        self.assertEqual(main["rows"], ["B45"])  # only usable rows are lit
        kinds = [i["kind"] for i in result["feedback"]]
        self.assertIn("could_be", kinds)
        self.assertIn("experiment", kinds)

    def test_seed4_with_tray_strings_and_button_held(self):
        _, s = session(SEED4, tray=TRAY4, hold=True)
        _, result, _ = s.measure()
        self.assertTrue(any("green LED" in p and "is blocking the current" in p
                            for p in problems(result)), result["hypotheses"])
        self.assertEqual(result["main_problem"]["kind"], "led_backwards")
        # Every hypothesis that fits uses every tray part.
        for h in result["hypotheses"]:
            words = " ".join(h["circuit"])
            for part in ("push button", "330 ohm resistor", "green LED"):
                self.assertIn(part, words)

    def test_tray_and_mistakes_beat_invisible_wires(self):
        """The real answer was "two wires from + to the rows": it fits, but
        uses no tray part and has no usual mistake while no current flows."""
        truth = {"parts": [dict(p, sure=False) for p in SEED1]}
        for tray in (None, ["330 ohm resistor", "blue LED"]):
            with self.subTest(tray=tray):
                reader = FakeReader(script=[{"hypotheses": [TWO_WIRES, truth]}])
                _, s = session(SEED1, reader=reader, tray=tray)
                _, result, _ = s.measure()
                self.assertEqual(len(result["hypotheses"]), 2)  # both fit the physics
                self.assertIn("blue LED", " ".join(result["hypotheses"][0]["circuit"]))
                self.assertEqual(result["main_problem"]["kind"], "led_backwards")
                self.assertEqual(result["reader_tries"], 1)

    def test_rank_penalties(self):
        measured = {"rows": {"B43": 5.0, "B45": 5.0}, "current_ma": 0.0,
                    "current_source": "measured", "supply_v": 5.0}
        usable = P.parse_usable(USABLE)
        hyps = []
        for parts in (TWO_WIRES["parts"], SEED1):
            g = clean_guess({"parts": parts}, usable)
            hyps.append(dict(physics_check(g["parts"], measured), guess=g))
        ranked = rank(hyps, measured, clean_tray(["330 ohm resistor", "blue LED"])["parts"])
        self.assertEqual(ranked[0]["guess"]["parts"][-1]["kind"], "led")
        self.assertEqual(ranked[0]["problems"][0]["kind"], "led_backwards")
        self.assertGreater(ranked[1]["penalty"], ranked[0]["penalty"])

    def test_unmeasured_rows(self):
        g = clean_guess({"parts": [{"kind": "led", "rows": ["B45", "U1"], "color": "red"}]},
                        P.parse_usable(USABLE))
        self.assertEqual(g["parts"][0]["rows"], ["B45", "U1"])
        self.assertIn("a row I cannot measure", g["parts"][0]["words"])
        self.assertIsNone(solver.solve([{"kind": "led", "rows": ["PB+", "U1"]}])["rows"]["U1"])


class TestPlanner(unittest.TestCase):
    def test_turn_around_the_led_separates_seed1(self):
        _, s = session(SEED1)
        _, result, _ = s.measure()
        plan = result["experiment_plan"]
        self.assertEqual(plan["action"], "turn around")
        self.assertEqual(plan["instruction"], "Turn around the LED in row 45 and the bottom - line.")
        self.assertEqual(len(plan["predictions"]), len(result["hypotheses"]))
        backwards = next(p for p in plan["predictions"] if "blocking" in (p.get("problem") or ""))
        self.assertGreater(backwards["current_ma"], 0.5)  # turned around, it would light
        others = [p for p in plan["predictions"] if p is not backwards]
        self.assertTrue(any(p["current_ma"] < 0.5 for p in others))

    def test_press_the_button_when_that_separates(self):
        """Button not pressed, LED one way or the other: only pressing tells."""
        measured = {"rows": {"B40": 5.0, "B41": None}, "current_ma": 0.0,
                    "current_source": "measured", "supply_v": 5.0}
        base = [{"kind": "resistor", "rows": ["PB+", "B40"], "ohms": 330},
                {"kind": "button", "rows": ["B40", "B41"], "closed": False}]
        forward = base + [{"kind": "led", "rows": ["B41", "PB-"], "color": "red"}]
        back = base + [{"kind": "led", "rows": ["PB-", "B41"], "color": "red"}]
        for parts in (forward, back):
            self.assertTrue(physics_check(parts, measured)["passed"])
        plan = plan_experiment([forward, back], measured, P.parse_usable(USABLE))
        self.assertEqual(plan["action"], "press")
        self.assertIn("Press the push button in rows 40 and 41", plan["instruction"])
        after = {"rows": {"B40": 2.2, "B41": 2.2}, "current_ma": 8.4,  # it lit
                 "current_source": "measured"}
        kept = which_hypothesis(plan, after)
        self.assertEqual((kept["index"], kept["all_matched"]), (0, True))

    def test_never_an_unsafe_action(self):
        measured = {"rows": {"B40": None}, "current_ma": 0.0, "current_source": "measured"}
        a = [{"kind": "button", "rows": ["PB+", "B40"], "closed": False},
             {"kind": "led", "rows": ["B40", "PB-"], "color": "red"}]   # pressed: no resistor
        b = [{"kind": "button", "rows": ["PB+", "B40"], "closed": False},
             {"kind": "led", "rows": ["PB-", "B40"], "color": "red"}]
        self.assertIsNone(plan_experiment([a, b], measured, P.parse_usable(USABLE)))
        self.assertIsNone(plan_experiment([a], measured, P.parse_usable(USABLE)))


class TestKeepTheHypothesis(unittest.TestCase):
    def run_experiment(self, after):
        board, s = session(SEED1)
        _, first, _ = s.measure()
        instruction = first["experiment_plan"]["instruction"]
        model = ScriptModel(script=[{"tool": "read_rows"}, {"tool": "plan_experiment"},
                                    {"tool": "run_experiment",
                                     "args": {"instruction": instruction}},
                                    {"reply": "Done."}])
        agent = build_agent(s, model, detective_model=ScriptedDetectiveModel(),
                            ask_before_power=True, reader_model=HeuristicReaderModel())
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertEqual(reply, instruction)  # paused at the experiment card
        plan = next(x for x in model.seen if isinstance(x, dict) and "plan" in x)["plan"]
        self.assertEqual(plan["instruction"], instruction)
        board.set_netlist(after)  # what the student really did
        answer_experiment(agent, [], True)
        out = model.seen[-1]
        return out, s

    def test_turning_it_around_keeps_led_backwards(self):
        out, s = self.run_experiment(SEED1_FIXED)
        kept = out["hypothesis_kept"]
        self.assertTrue(kept["came_true"])
        self.assertIn("is blocking the current", kept["problem"])
        result = s.last_check[1]
        self.assertEqual(result["hypothesis_kept"], kept)
        self.assertEqual(result["main_problem"]["kind"], "found")
        self.assertRegex(result["main_problem"]["text"],
                         r"^The experiment showed it: the \w+ LED between row 45 and the - line "
                         r"is blocking the current")
        steps = [x for x in s.trace if x.get("tool") == "compare"]
        self.assertEqual(len(steps), 1)

    def test_nothing_changed_keeps_the_missing_link(self):
        out, s = self.run_experiment(SEED1)  # turned around, still dark: not backwards
        kept = out["hypothesis_kept"]
        self.assertTrue(kept["came_true"])
        self.assertNotIn("is blocking the current", kept["problem"] or "")


class TestOffline(unittest.TestCase):
    def test_offline_tutor_names_the_problem_and_the_experiment(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        board = FakeBoard(FREE_ID, "correct", seed=1)
        board.set_netlist(SEED1)
        app = App(board, lesson=FREE_ID, history_path=Path(tmp.name) / "h.json",
                  model=ScriptedTutorModel(), usable_rows=USABLE, free_build=True)
        out = app.ask("Check my circuit")
        self.assertIn("is blocking the current: which way does its long leg go?", out["reply"])
        self.assertIn("To find out which, try this: Turn around the LED in row 45 and the bottom "
                      "- line.", out["reply"])
        self.assertEqual(board.lights.get("B45"), "red")
        self.assertTrue(set(board.lights) <= set(P.parse_usable(USABLE)))


class TestSmallFixes(unittest.TestCase):
    def test_tray_takes_names_and_objects(self):
        tray = clean_tray(["push button", "330 ohm resistor", "green LED", "green LED",
                           {"name": "wire", "count": 2}, "a buzzer"], "it lights")
        self.assertEqual(tray["parts"], [{"name": "wire", "count": 2},
                                         {"name": "330 ohm resistor", "count": 1},
                                         {"name": "green LED", "count": 2},
                                         {"name": "push button", "count": 1}])
        self.assertEqual(tray["other"], "a buzzer")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(FakeBoard(FREE_ID, "correct", seed=1), lesson=FREE_ID,
                  history_path=Path(tmp.name) / "h.json", model=ScriptedTutorModel(),
                  free_build=True)
        self.assertTrue(app.set_tray(TRAY4)["ok"])
        self.assertEqual(len(app.state()["tray"]["parts"]), 3)
        self.assertIn("error", app.set_tray([5]))

    def test_random_buttons_follow_hold(self):
        made = next(m for m in (random_circuit(s, 5, P.parse_usable(USABLE), mistake=None)
                                for s in range(200))
                    if any(p["kind"] == "button" for p in m["parts"]))
        for p in made["parts"]:
            if p["kind"] == "button":
                self.assertIsNone(p.get("closed"))  # not fixed: HOLD decides
        board = FakeBoard(FREE_ID, "correct", seed=1, noise=False)
        board.set_netlist(made["parts"])
        self.assertLess(board.read()["current_ma"], 0.5)  # nobody presses it
        board.send("HOLD on")
        self.assertGreater(board.read()["current_ma"], 0.5)
        board.send("HOLD off")
        self.assertLess(board.read()["current_ma"], 0.5)

    def test_push_button_is_a_part_not_the_action_push_in(self):
        from agent.experiments import validate_instruction
        from agent.lessons import FREE_LESSON
        ok, error, parsed = validate_instruction(
            "Press the push button in row 40 and the bottom + line and keep it pressed.",
            FREE_LESSON)
        self.assertTrue(ok, error)
        self.assertEqual(parsed["action"], "press")
        ok, _, parsed = validate_instruction("Push the LED in firmly in row 12.", FREE_LESSON)
        self.assertEqual(parsed["action"], "push in")  # the real "push in" still counts

    def test_no_resistor_circuits_have_no_button(self):
        for s in range(150):
            made = random_circuit(s, 5, P.parse_usable(USABLE), mistake="no_resistor")
            self.assertFalse(any(p["kind"] == "button" for p in made["parts"]))
            self.assertGreater(solver.solve(made["parts"])["current_ma"], 30)


if __name__ == "__main__":
    unittest.main()


# The bug from a real test of PR #28: button + -> B40, 220 ohm B40-B41, red
# LED in backwards (- line -> B41). The board: B40 = 5 V, B41 = 5 V, 0 mA.
# The tray (as the student filled it in): push button, 330 ohm, green LED.
REPORTED = [{"kind": "button", "rows": ["PB+", "B40"]},
            {"kind": "resistor", "rows": ["B40", "B41"], "ohms": 220},
            {"kind": "led", "rows": ["PB-", "B41"], "color": "red"}]
REPORTED_FIXED = REPORTED[:2] + [{"kind": "led", "rows": ["B41", "PB-"], "color": "red"}]
REPORTED_TRAY = ["push button", "330 ohm resistor", "green LED"]
# The reader's wrong answer: parts in rows that read EMPTY, nothing in B40/B41.
EMPTY_ROWS_GUESS = {"parts": [{"kind": "button", "rows": ["PT+", "T5"], "closed": False},
                              {"kind": "resistor", "rows": ["T5", "T20"], "ohms": 330},
                              {"kind": "led", "rows": ["T20", "PT-"], "color": "green"}]}
GOOD_GUESS = {"parts": [{"kind": "button", "rows": ["PB+", "B40"], "closed": True},
                        {"kind": "resistor", "rows": ["B40", "B41"], "ohms": 330},
                        {"kind": "led", "rows": ["PB-", "B41"], "color": "green"}]}


class TestRowsThatReadEmpty(unittest.TestCase):
    def measured(self):
        _, s = session(REPORTED, tray=REPORTED_TRAY, hold=True)
        summary, _, _ = s.measure()
        return measured_of(summary)

    def test_guess_in_empty_rows_fails_the_check(self):
        m = self.measured()
        self.assertEqual({r for r, v in m["rows"].items() if v is not None}, {"B40", "B41"})
        g = clean_guess(EMPTY_ROWS_GUESS, P.parse_usable(USABLE))
        out = physics_check(g["parts"], m)
        self.assertFalse(out["passed"])
        self.assertFalse(out["explains_all"])
        self.assertEqual(out["unexplained"], ["B40", "B41"])
        self.assertIn("B40: has 5.0 V, your guess leaves it empty", out["mismatches"])
        self.assertIn("B41: has 5.0 V, your guess leaves it empty", out["mismatches"])
        self.assertEqual(out["empty_joints"], ["T20", "T5"])  # joints of parts in empty rows

    def test_a_guess_that_drives_an_empty_row_fails(self):
        m = self.measured()
        g = clean_guess({"parts": GOOD_GUESS["parts"] + [{"kind": "wire", "rows": ["B40", "B42"]}]},
                        P.parse_usable(USABLE))
        out = physics_check(g["parts"], m)
        self.assertFalse(out["passed"])
        self.assertTrue(any(x.startswith("B42: your guess drives it") for x in out["mismatches"]))

    def test_the_kept_hypothesis_uses_b40_b41_and_the_backwards_led(self):
        reader = FakeReader(script=[{"hypotheses": [EMPTY_ROWS_GUESS, GOOD_GUESS]}])
        _, s = session(REPORTED, reader=reader, tray=REPORTED_TRAY, hold=True)
        _, result, _ = s.measure()
        self.assertEqual(result["reader_tries"], 1)
        self.assertEqual(len(result["hypotheses"]), 1)  # the empty-rows guess is not kept
        circuit = " ".join(result["hypotheses"][0]["circuit"])
        self.assertIn("row 40", circuit)
        self.assertIn("row 41", circuit)
        self.assertNotIn("row 5", circuit)
        main = result["main_problem"]
        self.assertEqual(main["kind"], "led_backwards")
        self.assertEqual(main["text"], "The green LED between row 41 and the - line is blocking "
                                       "the current: which way does its long leg go?")
        self.assertEqual(main["rows"], ["B41"])

    def test_only_the_empty_rows_guess_is_honestly_not_sure(self):
        reader = FakeReader(script=[EMPTY_ROWS_GUESS])  # twice: it does not learn
        _, s = session(REPORTED, reader=reader, tray=REPORTED_TRAY, hold=True)
        _, result, _ = s.measure()
        self.assertEqual(len(reader.seen), 2)  # the mismatches went back once
        again = json.loads(reader.seen[1])
        self.assertIn("B40: has 5.0 V, your guess leaves it empty", again["did_not_match"])
        self.assertEqual(result["main_problem"]["kind"], "not_sure")
        self.assertNotIn("experiment_plan", result)

    def test_rows_that_explain_every_driven_row_rank_first(self):
        m = self.measured()
        usable = P.parse_usable(USABLE)
        hyps = []
        for raw in (EMPTY_ROWS_GUESS, GOOD_GUESS):
            g = clean_guess(raw, usable)
            hyps.append(dict(physics_check(g["parts"], m), guess=g))
        ranked = rank(hyps, m, clean_tray(REPORTED_TRAY)["parts"])
        self.assertTrue(ranked[0]["explains_all"])
        self.assertEqual(ranked[0]["guess"]["parts"][0]["rows"], ["PB+", "B40"])
        self.assertFalse(ranked[1]["explains_all"])

    def test_planner_turns_the_led_around_never_parts_in_empty_rows(self):
        from agent.planner import candidate_actions
        _, s = session(REPORTED, tray=REPORTED_TRAY, hold=True)  # the offline reader
        summary, result, _ = s.measure()
        plan = result["experiment_plan"]
        self.assertEqual(plan["action"], "turn around")
        self.assertEqual(plan["instruction"], "Turn around the LED in row 41 and the bottom - line.")
        self.assertTrue(plan["fixes"])  # it would fix the most likely problem
        m = measured_of(summary)
        usable = P.parse_usable(USABLE)
        bad = clean_guess(EMPTY_ROWS_GUESS, usable)["parts"]
        good = clean_guess(GOOD_GUESS, usable)["parts"]
        for action in candidate_actions([bad, good], usable, m):
            self.assertFalse({"T5", "T20"} & set(action["part"]["rows"]), action)

    def test_turning_it_around_keeps_it_and_feedback_names_it(self):
        board, s = session(REPORTED, tray=REPORTED_TRAY, hold=True)
        _, first, _ = s.measure()
        instruction = first["experiment_plan"]["instruction"]
        model = ScriptModel(script=[{"tool": "read_rows"}, {"tool": "plan_experiment"},
                                    {"tool": "run_experiment",
                                     "args": {"instruction": instruction}},
                                    {"reply": "Done."}])
        agent = build_agent(s, model, detective_model=ScriptedDetectiveModel(),
                            ask_before_power=True, reader_model=HeuristicReaderModel())
        self.assertEqual(ask(agent, [], "Check my circuit")[0], instruction)
        board.set_netlist(REPORTED_FIXED)  # the student turned the LED around
        answer_experiment(agent, [], True)
        out = model.seen[-1]
        self.assertGreater(out["lesson_check"]["current_ma"], 10)  # current flows now
        kept = out["hypothesis_kept"]
        self.assertTrue(kept["came_true"])
        self.assertIn("is blocking the current", kept["problem"])
        self.assertIn("row 41", kept["problem"])
        main = s.last_check[1]["main_problem"]
        self.assertEqual(main["kind"], "found")
        self.assertEqual(main["text"], "The experiment showed it: the green LED between row 41 "
                                       "and the - line is blocking the current: which way does "
                                       "its long leg go?")
