"""Free build, phase 2: the circuit reader, the physics check and feedback.
Fake reader models stand in for Claude. Lessons 0-4 do not change."""

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import protocol as P
from agent.activity import classify
from agent.boardlink import BoardLink
from agent.experiments import validate_instruction
from agent.feedback import feedback_items, main_problem
from agent.lessons import FREE_ID, FREE_LESSON
from agent.mock_model import ScriptedTutorModel
from agent.reader import (MAX_TRIES, HeuristicReaderModel, clean_guess, make_reader,
                          measured_of, physics_check, read_and_check)
from agent.session import Session
from agent.trace import describe_steps
from sim.fake_board import FakeBoard
from tools.eval_free_build import evaluate
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
USABLE = "T3-T12,T20-T24,B40-B45"

W1 = {"kind": "wire", "rows": ["PT+", "T5"]}
R220 = {"kind": "resistor", "rows": ["T5", "T10"], "ohms": 220}
LED = {"kind": "led", "rows": ["T10", "T12"], "color": "red"}
W2 = {"kind": "wire", "rows": ["T12", "PT-"]}
CIRCUIT = [W1, R220, LED, W2]


def guess(*parts, sure=True):
    return {"parts": [dict(p, sure=sure) for p in parts], "notes": []}


class FakeReader(BaseChatModel):
    """Answers CircuitReading from a script (the last answer repeats), and
    keeps every input it was given."""
    script: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)
    fail: bool = False

    @property
    def _llm_type(self):
        return "fake-reader"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        self.seen.append(text)
        if self.fail:
            raise RuntimeError("no answer")
        answer = self.script[min(len(self.seen) - 1, len(self.script) - 1)]
        if "parts" in answer:  # one guess: the reader's answer holds a list of them
            answer = {"hypotheses": [answer]}
        msg = AIMessage(content="", tool_calls=[{"name": "CircuitReading", "args": answer,
                                                 "id": "g"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def free_session(netlist, script=(), usable=USABLE, tray=None, fail=False, **board):
    b = FakeBoard(FREE_ID, "correct", seed=1, **board)
    b.set_netlist(netlist)
    s = Session(BoardLink(b, usable), lesson=FREE_ID, free_build=True)
    model = FakeReader(script=list(script), fail=fail)
    s.reader = make_reader(s, model)
    s.tray = tray
    return b, s, model


def check(netlist, script=(), **kw):
    b, s, model = free_session(netlist, script, **kw)
    _, result, cut = s.measure()
    return result, s, model, b


def kinds(result):
    return [i["kind"] for i in result["feedback"]]


class TestPhysicsCheck(unittest.TestCase):
    def test_correct_guess_passes(self):
        result, s, model, _ = check(CIRCUIT, [guess(W1, R220, LED, W2)])
        self.assertEqual(result["guess_score"], 1.0)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["reader_tries"], 1)  # no second try needed
        self.assertTrue(all(p["ok"] for p in result["circuit_guess"]["parts"]))
        self.assertEqual(result["guess_matched"], "4 of 4")  # 3 rows + the current
        steps = [x["text"] for x in describe_steps(s.trace)]
        self.assertIn("Asked the circuit reader what you built", steps)
        self.assertIn("The circuit reader guessed 4 parts (4 sure)", steps)
        self.assertIn("Checked the guess with a circuit solver: everything matches", steps)

    def test_wrong_guess_gets_mismatches_and_a_second_try(self):
        wrong = dict(LED, color="blue")  # a blue LED would hold row 10 near 3 V
        result, s, model, _ = check(CIRCUIT, [guess(W1, R220, wrong, W2), guess(W1, R220, LED, W2)])
        self.assertEqual(len(model.seen), 2)
        second = json.loads(model.seen[1])
        self.assertTrue(any(m.startswith("T10: you predict") for m in second["did_not_match"]),
                        second["did_not_match"])
        self.assertTrue(any(m.startswith("current: you predict") for m in second["did_not_match"]))
        self.assertEqual(second["your_last_guess"][2]["color"], "blue")
        self.assertEqual((result["reader_tries"], result["guess_score"]), (2, 1.0))
        steps = [x["text"] for x in describe_steps(s.trace)]
        self.assertIn("Asked the circuit reader again, with what did not match", steps)

    def test_best_guess_is_kept_and_at_most_two_tries(self):
        close = guess(W1, dict(R220, ohms=330), LED, W2)       # rows fine, current off
        worse = guess(W1, dict(LED, rows=["T5", "T12"]), W2)   # T10 not even in it
        result, s, model, _ = check(CIRCUIT, [close, worse, worse])
        self.assertEqual(MAX_TRIES, 2)
        self.assertEqual(len(model.seen), 2)
        self.assertEqual(result["circuit_guess"]["parts"][1]["ohms"], 330)  # the better one
        self.assertLess(result["guess_score"], 1.0)
        self.assertTrue(result["mismatches"])

    def test_never_raises(self):
        result, s, model, _ = check(CIRCUIT, fail=True)
        self.assertIn("RuntimeError", result["reader_error"])
        self.assertIsNone(result["circuit_guess"])
        self.assertEqual(main_problem(result["feedback"])["kind"], "not_sure")
        bad = {"parts": [{"kind": "rocket", "rows": ["T5", "T10"]},
                         {"kind": "wire", "rows": ["T5"]},
                         {"kind": "wire", "rows": ["T5", "T30"]},       # not a usable row
                         {"kind": "resistor", "rows": ["T5", "T10"]},   # no value
                         "junk"]}
        cleaned = clean_guess(bad, P.parse_usable(USABLE))
        self.assertEqual(cleaned["dropped"], 4)
        self.assertEqual([p["kind"] for p in cleaned["parts"]], ["unknown"])
        out = physics_check([{"kind": "resistor", "rows": ["T5", "T10"], "ohms": -1, "words": ""}],
                            {"rows": {"T5": 5.0}})
        self.assertEqual(out["score"], 0.0)  # the solver refused it: no exception
        self.assertEqual(read_and_check(lambda p: 1 / 0, {}, {"rows": {}}, [], [])["guess"], None)

    def test_low_score_is_honest(self):
        nonsense = guess({"kind": "resistor", "rows": ["T20", "T21"], "ohms": 100})
        b = FakeBoard(FREE_ID, "correct", seed=1)
        b.set_netlist(CIRCUIT)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(b, lesson=FREE_ID, history_path=Path(tmp.name) / "h.json",
                  model=ScriptedTutorModel(), usable_rows=USABLE, free_build=True)
        app.agent.smart_board_reader = make_reader(app.session, FakeReader(script=[nonsense]))
        out = app.ask("Check my circuit")
        result = app.session.last_check[1]
        self.assertLess(result["guess_score"], 0.6)
        self.assertEqual(result["main_problem"]["kind"], "not_sure")
        self.assertIn("I am not sure what you built", out["reply"])
        self.assertIn("What parts did you use?", out["reply"])  # asks about the tray
        self.assertTrue(app.state()["circuit_guess"]["not_sure"])


class TestFeedback(unittest.TestCase):
    def test_led_on(self):
        result, *_ = check(CIRCUIT, [guess(W1, R220, LED, W2)])
        led = next(i for i in result["feedback"] if i["kind"] == "led_on")
        self.assertEqual((led["light"], led["rows"]), ("green", ["T10", "T12"]))
        self.assertIn("red LED in row 10 and row 12 is on", led["text"])
        self.assertIn("working", kinds(result))  # the resistor carries current
        self.assertIsNone(result["main_problem"])

    def test_led_backwards(self):
        back = dict(LED, rows=["T12", "T10"])
        result, *_ = check([W1, R220, back, W2], [guess(W1, R220, back, W2)])
        self.assertEqual(result["guess_score"], 1.0)
        main = result["main_problem"]
        self.assertEqual((main["kind"], main["light"]), ("led_backwards", "red"))
        self.assertIn("is blocking the current: which way does its long leg go?", main["text"])
        self.assertIn("row 10", main["text"])  # names the rows, hint first

    def test_short(self):
        across = {"kind": "wire", "rows": ["T10", "T12"]}
        result, *_ = check(CIRCUIT + [across], [guess(W1, R220, LED, W2, across)])
        self.assertEqual(result["guess_score"], 1.0)
        main = result["main_problem"]
        self.assertEqual((main["kind"], main["light"]), ("short", "red"))
        self.assertIn("goes around it", main["text"])

    def test_short_across_the_supply(self):
        items = feedback_items(None, {"rows": {"T5": 0.4}, "current_ma": None,
                                      "current_source": "unknown", "supply_v": 0.4},
                               P.parse_usable(USABLE))
        self.assertEqual(items[0]["kind"], "short_supply")

    def test_open_path(self):
        moved = dict(LED, rows=["T11", "T12"])  # the long leg went in row 11: nothing else there
        result, *_ = check([W1, R220, moved, W2], [guess(W1, R220, moved, W2)])
        main = result["main_problem"]
        self.assertEqual(main["kind"], "open_path")
        self.assertIn("row 11", main["text"])
        self.assertEqual(main["light"], "yellow")

    def test_part_doing_nothing(self):
        spare = {"kind": "resistor", "rows": ["T5", "T20"], "ohms": 1000}  # one leg in the air
        result, *_ = check(CIRCUIT + [spare], [guess(W1, R220, LED, W2, spare)])
        self.assertEqual(result["guess_score"], 1.0)
        nothing = next(i for i in result["feedback"] if i["kind"] == "does_nothing")
        self.assertIn("1k ohm resistor", nothing["text"])
        self.assertEqual(nothing["rows"], ["T5", "T20"])

    def test_goal_met_and_not_met(self):
        tray = {"parts": [{"name": "red LED", "count": 1}], "other": "",
                "goal": "the red LED lights up"}
        result, *_ = check(CIRCUIT, [guess(W1, R220, LED, W2)], tray=tray)
        self.assertIn("goal_met", kinds(result))
        back = dict(LED, rows=["T12", "T10"])
        result, *_ = check([W1, R220, back, W2], [guess(W1, R220, back, W2)], tray=tray)
        self.assertIn("goal_not_met", kinds(result))
        tray["goal"] = "make a buzzer beep"
        result, *_ = check(CIRCUIT, [guess(W1, R220, LED, W2)], tray=tray)
        self.assertIn("goal_unknown", kinds(result))

    def test_only_usable_rows_are_lit(self):
        usable = P.parse_usable("T3-T8")
        reading = {"guess": {"parts": [dict(LED, rows=["T5", "T30"], sure=True, words="")]},
                   "score": 1.0, "predicted": {"rows": {}, "leds_on": {0: True}}}
        items = feedback_items(reading, {"rows": {"T5": 4.0}, "current_ma": 5.0,
                                         "current_source": "measured", "supply_v": 5.0}, usable)
        for item in items:
            self.assertTrue(set(item["rows"]) <= set(usable), item)
        b = FakeBoard(FREE_ID, "correct", seed=1)
        b.set_netlist(CIRCUIT)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(b, lesson=FREE_ID, history_path=Path(tmp.name) / "h.json",
                  model=ScriptedTutorModel(), usable_rows="T3-T10", free_build=True)
        app.ask("Check my circuit")
        self.assertTrue(b.lights)
        self.assertTrue(set(b.lights) <= set(P.parse_usable("T3-T10")), b.lights)

    def test_too_much_current_is_cut_first(self):
        result, s, model, b = check([W1, dict(LED, rows=["T5", "T12"]), W2],
                                    [guess(W1, dict(LED, rows=["T5", "T12"]), W2)])
        self.assertFalse(b.power_on)  # the safety cut, before and without the AI
        self.assertEqual(result["main_problem"]["kind"], "too_much_current")


class TestPrivacyAndLessons(unittest.TestCase):
    def test_true_netlist_never_in_the_reader_input(self):
        result, s, model, b = check(CIRCUIT, [guess(W1, dict(R220, ohms=1000), LED, W2),
                                              guess(W1, R220, LED, W2)])
        first = json.loads(model.seen[0])
        self.assertEqual(set(first), {"usable_rows", "driven_rows", "floating", "current_ma",
                                      "current_source", "supply_v", "tray", "tray_other", "goal",
                                      "facts"})
        everything = "\n".join(model.seen)
        self.assertNotIn("netlist", everything)
        self.assertNotIn('"ohms": 220', everything)  # the true value: never given
        self.assertNotIn("220", model.seen[0])

    def test_lessons_unchanged(self):
        b = FakeBoard(1, "led_backwards", seed=1)
        s = Session(b, lesson=1)
        model = FakeReader(script=[guess(W1)])
        s.reader = make_reader(s, model)
        _, result, _ = s.measure()
        self.assertEqual((result["status"], result["mistake_id"]), ("mistake", "led_backwards"))
        self.assertEqual(model.seen, [])  # the reader is only for free build
        self.assertNotIn("circuit_guess", result)
        self.assertNotIn("feedback", result)


class TestWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = FakeBoard(FREE_ID, "correct", seed=1)
        self.app = App(self.board, lesson=FREE_ID, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel(), usable_rows=USABLE, free_build=True)

    def test_offline_check_end_to_end(self):
        self.board.set_netlist(CIRCUIT)
        out = self.app.ask("Check my circuit")
        self.assertIn("Your circuit works!", out["reply"])
        self.assertEqual(self.board.lights, {"T5": "green", "T10": "green", "T12": "green"})
        view = self.app.state()["circuit_guess"]
        self.assertEqual(view["matched"], "4 of 4")
        self.assertTrue(all(p["ok"] for p in view["parts"]))
        self.assertIn("red LED: row 10 and row 12 (long leg in row 10)",
                      [p["words"] for p in view["parts"]])
        agents = {i["agent"] for i in self.app.state()["activity"]}
        self.assertTrue({"Circuit reader", "Physics check (Python)"} <= agents)

    def test_offline_reader_is_on_and_a_lesson_has_no_guess(self):
        self.assertIsInstance(HeuristicReaderModel(), BaseChatModel)
        self.assertIsNotNone(self.app.agent.smart_board_reader)
        self.assertIsNone(App(FakeBoard(1, "correct", seed=1), lesson=1,
                              history_path=Path(self.tmp.name) / "h2.json",
                              model=ScriptedTutorModel()).state()["circuit_guess"])

    def test_activity_agents(self):
        self.assertEqual(classify({"who": "reader", "tool": "read"}), ("Circuit reader", "think"))
        self.assertEqual(classify({"who": "physics_check", "tool": "check"}),
                         ("Physics check (Python)", "check"))

    def test_page(self):
        for marker in ('id="guess"', "Zip thinks you built:", 'id="guess-list"',
                       "state.circuit_guess", "Checked with a circuit solver",
                       '"Circuit reader": 8', '"Physics check (Python)": 9'):
            self.assertIn(marker, HTML)

    def test_experiments_in_free_build(self):
        ok, error, parsed = validate_instruction("Turn around the LED in rows 10 and 12.",
                                                 FREE_LESSON)
        self.assertTrue(ok, error)
        self.assertEqual(parsed["part"], "LED")


class TestEval(unittest.TestCase):
    def test_eval_script_offline(self):
        report = evaluate(n=8, seed=0, quiet=True)
        self.assertEqual(report["n"], 8)
        for key in ("read_pct", "found_pct", "parts_pct"):
            self.assertTrue(0 <= report[key] <= 100, report)
        self.assertGreaterEqual(report["read_pct"], 50)  # the offline baseline reads most


if __name__ == "__main__":
    unittest.main()
