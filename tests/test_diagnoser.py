"""The AI diagnoser: plain Python works out physics facts, the diagnoser
reasons from them, plain Python checks every cause is grounded. Safety,
asking before power on, and PASS (the lesson's accept rules) do not change.

"Claude" here is FakeDiagnoser, a stand-in that answers from the facts it
is given (like a careful teacher would), or misbehaves on purpose."""

import json
import unittest

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.build import build_agent
from agent.diagnoser import (DIAGNOSER_PROMPT, Diagnosis, compare_with_rules, diagnoser_spec,
                             lights_from, make_diagnoser, validate_diagnosis)
from agent.lessons import load_lessons, summarize
from agent.mock_model import ScriptedTutorModel
from agent.physics import observations, ohms_of
from agent.prompts import SYSTEM_PROMPT
from agent.session import Session
from agent.trace import describe_steps
from sim.fake_board import FakeBoard

LESSONS = load_lessons()


class FakeDiagnoser(BaseChatModel):
    """Answers like a teacher who read the facts. mode: "rule" (find the
    known mistake's facts), "ok", "mistake_on" (blame rows given in rows),
    "made_up" (cite facts and rows that do not exist), "raise"."""
    mode: str = "rule"
    rows: list = Field(default_factory=list)
    payloads: list = Field(default_factory=list)
    power_seen: list = Field(default_factory=list)
    board: object = None

    @property
    def _llm_type(self):
        return "fake-diagnoser"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = json.loads([m for m in messages if isinstance(m, HumanMessage)][-1].content)
        self.payloads.append(payload)
        if self.board is not None:
            self.power_seen.append(self.board.power_on)
        if self.mode == "raise":
            raise ConnectionError("no internet")
        answer = self.answer(payload)
        msg = AIMessage(content="", tool_calls=[{"name": "Diagnosis", "args": answer, "id": "d1"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def answer(self, payload):
        facts = payload["facts"]
        if self.mode == "ok" or (self.mode == "rule" and payload["rule_status"] in ("ok", "step_ok")):
            return {"status": "ok", "causes": []}
        if self.mode == "made_up":
            return {"status": "mistake", "causes": [
                {"cause": "Something in row 99", "rows": ["T99"], "evidence": ["F1"]},
                {"cause": "A made-up reading", "rows": ["T10"], "evidence": ["F999"]},
                {"cause": "No evidence at all", "rows": ["T10"], "evidence": []}],
                "question": "Is it the LED"}
        if self.mode == "mistake_on":
            rows = self.rows
        else:  # "rule": the rows of the known mistake, the facts about them
            by_kind = {f["kind"]: f for f in facts}
            lesson = LESSONS[payload["lesson"]["id"]]
            spec = next((m for m in lesson["mistakes"] if m["id"] == payload["rule_match"]), {})
            rows = (by_kind.get("extra_rows", {}).get("rows") if payload["rule_match"] == "extra_part"
                    else by_kind.get("flicker", {}).get("rows") if "loose" in (payload["rule_match"] or "")
                    else spec.get("rows")) or []
        evidence = [f["id"] for f in facts if set(f["rows"]) & set(rows)] or [facts[-1]["id"]]
        return {"status": "mistake",
                "causes": [{"cause": f"Something is wrong around {', '.join(rows)}", "rows": rows,
                            "evidence": evidence, "how_sure": "likely"}]}


def checked(lesson, scenario="correct", mode="rule", holds=None, rows=None, **opts):
    board = FakeBoard(lesson, scenario, seed=1, **opts)
    session = Session(board, lesson=lesson)
    model = FakeDiagnoser(mode=mode, rows=rows or [], board=board)
    session.diagnoser = make_diagnoser(session, model)
    result = cut = None
    for hold in holds or [None]:
        if hold is not None:
            board.send("HOLD " + ("on" if hold else "off"))
        _, result, cut = session.measure()
    return session, result, cut, board, model


def facts_for(lesson, scenario="correct", hold=None, **opts):
    board = FakeBoard(lesson, scenario, seed=1, noise=False, **opts)
    if hold is not None:
        board.send("HOLD " + ("on" if hold else "off"))
    lesson_data = LESSONS[lesson]
    if lesson_data.get("states"):  # check with the step's rules, like the session does
        step = "pressed" if hold else "released"
        st = lesson_data["states"][step]
        lesson_data = dict(lesson_data, accept=st["accept"], expected=st["expected"])
    s = summarize([board.read() for _ in range(10)], lesson_data)
    return observations(s, lesson_data)


def kinds(facts, kind):
    return [f for f in facts if f["kind"] == kind]


class TestPhysicsNumbers(unittest.TestCase):
    def test_ohms_law(self):
        lesson = {"layout": [{"part": "220 ohm resistor", "legs": [{"leg": "a", "row": "T5"},
                                                                   {"leg": "b", "row": "T10"}]}],
                  "rows": ["T5", "T10"], "expected": {"rows": {"T5": 3.3, "T10": 2.0}}}
        summary = {"power_on": True, "supply_v": 3.3, "rows": {"T5": 3.3, "T10": 2.0},
                   "floating": [], "flickering": [], "current_ma": 5.9, "current_source": "measured"}
        facts = observations(summary, lesson)
        rc = kinds(facts, "resistor_current")[0]
        self.assertAlmostEqual(rc["values"]["ma"], 5.909, places=2)  # 1.3 V / 220 ohm
        self.assertIn("1.3 volts across the 220 ohm resistor means about 5.9 mA", rc["text"])
        self.assertEqual([f["id"] for f in facts], [f"F{i}" for i in range(1, len(facts) + 1)])
        self.assertEqual(ohms_of("Top 10k resistor", {}), 10000)
        self.assertEqual(ohms_of("330 ohm resistor", {}), 330)

    def test_lesson_one(self):
        f = facts_for(1)
        self.assertEqual(kinds(f, "led_state")[0]["values"]["state"], "on")
        self.assertAlmostEqual(kinds(f, "resistor_current")[0]["values"]["ma"], 5.91, places=1)
        self.assertEqual(kinds(f, "current_vs_expected")[0]["values"]["percent"], 0)
        self.assertEqual(kinds(f, "plan_row_floating") + kinds(f, "same_voltage"), [])
        self.assertEqual(kinds(facts_for(1, "led_backwards"), "led_state")[0]["values"]["state"],
                         "blocked")
        bypassed = facts_for(1, "led_bypassed")
        self.assertEqual(kinds(bypassed, "led_state")[0]["values"]["state"], "shorted")
        self.assertEqual(kinds(bypassed, "same_voltage")[0]["rows"], ["T10", "T12"])
        self.assertEqual(kinds(facts_for(1, "open_circuit"), "plan_row_floating")[0]["rows"], ["T10"])
        self.assertEqual(kinds(facts_for(1, "resistor_too_big"), "current_vs_expected")[0]
                         ["values"]["percent"], -78)  # 1.3 mA instead of 5.9
        self.assertIn("only 2.2 volts", kinds(facts_for(1, "no_resistor", has_current=False),
                                             "power")[0]["text"])
        board = FakeBoard(1, "loose_led_leg", seed=1)
        s = summarize([board.read() for _ in range(10)], LESSONS[1])
        self.assertEqual(kinds(observations(s, LESSONS[1]), "flicker")[0]["rows"], ["T10"])

    def test_lesson_two(self):
        f = facts_for(2)
        self.assertEqual([round(x["values"]["ma"], 2) for x in kinds(f, "resistor_current")],
                         [0.17, 0.17])  # 1.65 V / 10k each = 0.165 mA
        f = facts_for(2, "bottom_1k")  # middle 3.3 x 1 / 11 = 0.3 V
        top, bottom = kinds(f, "resistor_current")
        self.assertAlmostEqual(top["values"]["ma"], 0.3, places=2)
        self.assertAlmostEqual(bottom["values"]["ma"], 0.03, places=2)  # they should be equal
        self.assertEqual(kinds(facts_for(2, "middle_floating"), "plan_row_floating")[0]["rows"],
                         ["T10"])

    def test_lesson_three_and_four(self):
        self.assertAlmostEqual(kinds(facts_for(3), "resistor_current")[0]["values"]["ma"], 3.94,
                               places=1)  # 1.3 V / 330 ohm
        pressed = facts_for(4, hold=True)
        drops = {f["text"].split(" has")[0]: f["values"]["drop"] for f in kinds(pressed, "part_drop")}
        self.assertAlmostEqual(drops["The push button"], 0.0, places=1)
        self.assertEqual(kinds(pressed, "led_state")[0]["values"]["state"], "on")
        released = facts_for(4, hold=False)
        self.assertIn("cannot be measured", kinds(released, "part_drop")[1]["text"])
        always = facts_for(4, "button_always_on", hold=False)
        self.assertIn("expects no current", kinds(always, "current_vs_expected")[0]["text"])
        self.assertEqual(kinds(facts_for(4, "led_backwards", hold=True), "led_state")[0]
                         ["values"]["state"], "blocked")

    def test_power_off_and_extra_rows(self):
        off = {"power_on": False, "supply_v": 0.0, "rows": {}, "floating": [], "flickering": []}
        facts = observations(off, LESSONS[1])
        self.assertEqual([f["kind"] for f in facts], ["power"])
        board = FakeBoard(1, "correct", seed=1, noise=False)
        s = summarize([board.read() for _ in range(3)], LESSONS[1])
        extra = observations(s, LESSONS[1], extra_rows=["T30"])[-1]
        self.assertEqual((extra["kind"], extra["rows"]), ("extra_rows", ["T30"]))
        cut = observations(s, LESSONS[1], safety_cut=True)[0]
        self.assertIn("switched off to keep the parts safe", cut["text"])

    def test_texts_are_plain(self):
        for lesson, scenario in ((1, "correct"), (1, "led_backwards"), (2, "bottom_1k"),
                                 (1, "open_circuit")):
            for f in facts_for(lesson, scenario):
                self.assertNotIn("-0.0", f["text"])
                self.assertNotIn("T1", f["text"])  # "row 10", never "T10"
                for hard in ("node", "anode", "cathode", "polarity", "rail"):
                    self.assertNotIn(f" {hard} ", f" {f['text'].lower()} ")


class TestValidator(unittest.TestCase):
    FACTS = [{"id": "F1", "kind": "power", "rows": [], "values": {}, "text": "on"},
             {"id": "F2", "kind": "led_state", "rows": ["T10", "T12"], "values": {}, "text": "x"}]
    ROWS = {"T5", "T10", "T12", "PT+"}

    def validate(self, raw, passed=False):
        return validate_diagnosis(raw, self.FACTS, self.ROWS, passed)

    def test_drops_ungrounded_causes(self):
        raw = {"status": "mistake", "causes": [
            {"cause": "Good one", "rows": ["T10"], "evidence": ["F2"], "how_sure": "likely"},
            {"cause": "No evidence", "rows": ["T10"], "evidence": []},
            {"cause": "Made-up fact", "rows": ["T10"], "evidence": ["F9"]},
            {"cause": "Bad row", "rows": ["T99"], "evidence": ["F2"]},
            {"cause": "Not a row name", "rows": ["row 10"], "evidence": ["F2"]}]}
        diag, dropped = self.validate(raw)
        self.assertEqual([c["cause"] for c in diag["causes"]], ["Good one"])
        self.assertEqual(dropped, 4)

    def test_at_most_three_short_causes_and_a_real_question(self):
        many = [{"cause": f"Cause {i} " + "x" * 200, "rows": ["T10"], "evidence": ["F2"],
                 "how_sure": "very"} for i in range(5)]
        diag, dropped = self.validate({"status": "mistake", "causes": many,
                                       "question": "Is the LED backwards"})
        self.assertEqual(len(diag["causes"]), 3)
        self.assertEqual(dropped, 2)
        self.assertTrue(all(len(c["cause"]) <= 123 for c in diag["causes"]))
        self.assertEqual({c["how_sure"] for c in diag["causes"]}, {"possible"})
        self.assertNotIn("question", diag)  # no "?": dropped
        diag, _ = self.validate({"status": "unsure", "question": "Is the LED backwards?"})
        self.assertEqual(diag["question"], "Is the LED backwards?")

    def test_never_raises(self):
        for raw in (None, "text", 42, {"status": "maybe"}, {"causes": "x"},
                    {"status": "mistake", "causes": [None, 3, {"cause": None}]}):
            diag, _ = self.validate(raw)
            self.assertEqual(diag["status"], "unsure")
        diag, _ = self.validate(Diagnosis(status="mistake", causes=[
            {"cause": "LED blocked", "rows": ["T10"], "evidence": ["F2"], "how_sure": "likely"}]))
        self.assertEqual(diag["status"], "mistake")

    def test_veto_both_ways(self):
        ok = {"status": "ok", "causes": []}
        self.assertEqual(self.validate(ok, passed=False)[0]["status"], "unsure")  # cannot pass
        fail = {"status": "mistake", "causes": [{"cause": "LED", "rows": ["T10"],
                                                 "evidence": ["F2"], "how_sure": "likely"}]}
        diag, _ = self.validate(fail, passed=True)  # cannot fail a passing circuit
        self.assertEqual(diag["status"], "ok")
        self.assertEqual(diag["causes"][0]["how_sure"], "possible")  # only a note

    def test_lights_and_rule_comparison(self):
        diag = {"status": "mistake", "causes": [
            {"cause": "a", "rows": ["T10", "PT+"], "evidence": ["F2"], "how_sure": "possible"},
            {"cause": "b", "rows": ["T10", "T12"], "evidence": ["F2"], "how_sure": "likely"},
            {"cause": "c", "rows": ["T5"], "evidence": ["F2"], "how_sure": "possible"}]}
        self.assertEqual(lights_from(diag), {"T10": "red", "T12": "red", "T5": "yellow"})
        self.assertTrue(compare_with_rules({"status": "mistake", "rows": ["T10"]}, diag)["agrees"])
        self.assertFalse(compare_with_rules({"status": "mistake", "rows": ["T12"]}, diag)["agrees"])
        self.assertTrue(compare_with_rules({"status": "ok"}, {"status": "ok", "causes": []})["agrees"])
        self.assertFalse(compare_with_rules({"status": "mistake", "rows": ["T5"]}, diag)["agrees"])
        self.assertFalse(compare_with_rules({"status": "ok"}, diag)["agrees"])


class TestInTheCheck(unittest.TestCase):
    def test_every_known_mistake_is_still_diagnosed(self):
        cases = [(1, s, None) for s in ("led_backwards", "no_resistor", "open_circuit",
                                         "resistor_too_big", "led_bypassed", "loose_led_leg",
                                         "stray_part")]
        cases += [(2, s, None) for s in ("bottom_1k", "middle_floating", "top_1k",
                                         "bottom_missing", "top_missing")]
        cases += [(3, "led_backwards", None), (4, "led_backwards", [True]),
                  (4, "button_always_on", [True, False])]
        for lesson, scenario, holds in cases:
            with self.subTest(lesson=lesson, scenario=scenario):
                _, result, _, _, _ = checked(lesson, scenario, holds=holds)
                self.assertEqual(result["status"], "mistake")
                diag = result["diagnosis"]
                self.assertEqual(diag["status"], "mistake")
                self.assertEqual(set(diag["causes"][0]["rows"]), set(result["rows"]))
                self.assertTrue(result["rule_match"]["agrees"])
                self.assertEqual(result["lights"], {r: "red" for r in result["rows"]})

    def test_correct_circuits_pass_as_before(self):
        for lesson, holds in ((1, None), (2, None), (3, None), (4, [True, False])):
            with self.subTest(lesson=lesson):
                session, result, _, _, _ = checked(lesson, holds=holds)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["diagnosis"]["status"], "ok")
                self.assertTrue(session.passed_current)

    def test_the_ai_cannot_fail_a_passing_circuit(self):
        session, result, _, _, _ = checked(1, mode="mistake_on", rows=["T10"])
        self.assertEqual(result["status"], "ok")  # PASS: the lesson's rules
        self.assertTrue(session.passed_current)
        self.assertEqual(result["diagnosis"]["status"], "ok")
        self.assertEqual(result["lights"], {"T10": "yellow"})  # a "possible" note
        self.assertFalse(result["rule_match"]["agrees"])

    def test_the_ai_cannot_pass_a_failing_circuit(self):
        session, result, _, _, _ = checked(1, "led_backwards", mode="ok")
        self.assertEqual(result["status"], "mistake")
        self.assertEqual(result["diagnosis"]["status"], "unsure")
        self.assertFalse(session.passed_current)

    def test_disagreement_is_kept_for_later(self):
        session, result, _, _, _ = checked(1, "led_backwards", mode="mistake_on", rows=["T5"])
        self.assertEqual(result["rule_match"], {"status": "mistake", "mistake_id": "led_backwards",
                                                "rows": ["T10", "T12"], "agrees": False})
        self.assertIn("Kept both answers: the diagnoser and the lesson's own rules "
                      "(led backwards) disagree", [s["text"] for s in describe_steps(session.trace)])

    def test_made_up_causes_never_reach_the_student(self):
        session, result, _, _, _ = checked(1, "led_backwards", mode="made_up")
        self.assertEqual(result["diagnosis"], {"status": "unsure", "causes": []})
        self.assertEqual(result["lights"], {})
        steps = [s["text"] for s in describe_steps(session.trace)]
        self.assertIn("Checked every cause against the facts (left out 3 that the facts do not "
                      "show)", steps)

    def test_how_zip_worked(self):
        session, result, _, _, _ = checked(1, "led_backwards")
        steps = describe_steps(session.trace)
        self.assertEqual([s["text"] for s in steps],
                         [f"Worked out {len(result['facts'])} facts from the measurements",
                          "Asked the diagnoser",
                          "The diagnoser answered: a mistake, 1 possible cause",
                          "Checked every cause against the facts"])
        self.assertEqual(steps[1]["who"], "diagnoser")
        self.assertEqual(steps[2]["who"], "diagnoser")

    def test_safety_comes_first_and_does_not_wait(self):
        session, result, cut, board, model = checked(1, "no_resistor")
        self.assertTrue(cut)
        self.assertFalse(board.power_on)
        self.assertEqual(model.power_seen, [False])  # already off when the AI was asked
        self.assertEqual(result["diagnosis"]["status"], "mistake")
        self.assertTrue(result["facts"][0]["values"]["safety_cut"] or
                        "switched off" in result["facts"][0]["text"] or
                        result.get("board_cut"))

    def test_a_failing_diagnoser_changes_nothing(self):
        session, result, cut, board, _ = checked(1, "led_backwards", mode="raise")
        self.assertEqual(result["mistake_id"], "led_backwards")
        self.assertNotIn("diagnosis", result)
        self.assertIn("ConnectionError", result["diagnosis_error"])
        self.assertTrue(board.power_on)

    def test_power_off_is_not_sent_to_the_ai(self):
        board = FakeBoard(1, "correct", seed=1)
        board.send("CUT")
        session = Session(board, lesson=1)
        model = FakeDiagnoser()
        session.diagnoser = make_diagnoser(session, model)
        _, result, _ = session.measure()
        self.assertEqual(result["status"], "power_off")
        self.assertEqual([f["kind"] for f in result["facts"]], ["power"])
        self.assertEqual(model.payloads, [])

    def test_the_payload(self):
        _, result, _, _, model = checked(3, "led_backwards")
        payload = model.payloads[0]
        self.assertEqual(payload["rule_match"], "led_backwards")
        self.assertEqual(payload["facts"], result["facts"])
        self.assertEqual(payload["lesson"]["rows"], ["T5", "T10", "T12"])
        self.assertEqual(set(payload), {"lesson", "copies", "step", "facts", "rule_match",
                                        "rule_status", "recent_mistakes"})

    def test_lesson_three_copies_get_facts_each(self):
        board = FakeBoard(3, "led_backwards", seed=1)
        board.send("COPIES T0 T15")
        session = Session(board, lesson=3)
        session.diagnoser = make_diagnoser(session, FakeDiagnoser())
        _, result, _ = session.measure()
        texts = [f["text"] for f in result["facts"]]
        self.assertTrue(any(t.startswith("LED circuit 1:") for t in texts))
        self.assertTrue(any(t.startswith("LED circuit 2:") for t in texts))
        self.assertEqual(result["rows"], ["T25", "T27"])


class TestOfflineUnchanged(unittest.TestCase):
    def test_no_diagnoser_offline(self):
        board = FakeBoard(1, "led_backwards", seed=1)
        session = Session(board, lesson=1)
        agent = build_agent(session, ScriptedTutorModel())
        self.assertIsNone(agent.smart_board_diagnoser)
        _, result, _ = session.measure()
        for key in ("facts", "diagnosis", "lights", "rule_match"):
            self.assertNotIn(key, result)
        self.assertEqual(session.trace, [])

    def test_each_reply_uses_its_own_agents_diagnoser(self):
        from agent.build import ask
        board = FakeBoard(1, "led_backwards", seed=1)
        session = Session(board, lesson=1)
        online = build_agent(session, ScriptedTutorModel(), diagnoser_model=FakeDiagnoser())
        offline = build_agent(session, ScriptedTutorModel())
        ask(online, [], "Check my circuit")
        self.assertIn("diagnosis", session.last_check[1])
        ask(offline, [], "Check my circuit")  # the offline fallback: no AI
        self.assertNotIn("diagnosis", session.last_check[1])


class TestSpecAndPrompts(unittest.TestCase):
    def test_spec_is_a_plain_schema(self):
        spec = diagnoser_spec(session=None)
        self.assertIs(spec["response_format"], Diagnosis)
        self.assertEqual(spec["name"], "diagnoser")
        self.assertEqual(spec["tools"], [])

    def test_prompts(self):
        for needed in ("cite the fact ids", "Never invent", '"unsure"', "Never say the fix"):
            self.assertIn(needed, DIAGNOSER_PROMPT)
        for needed in ('lesson_check has "diagnosis"', "Hint first", "lesson_check.lights",
                       "diagnosis.question", "never decides a pass"):
            self.assertIn(needed, SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
