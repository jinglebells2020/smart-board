"""The two deepagents features: the detective subagent and the student
notebook (memory). All offline: the scripted models stand in for Claude."""

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.build import ask, build_agent
from agent.detective import CERTAIN, DetectiveAnswer, case_file, leads
from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.notebook import MARKER, Notebook, safe_name
from agent.session import Session
from sim.fake_board import SCENARIOS, FakeBoard
from web.server import App, default_history

LESSONS = load_lessons()
UNKNOWN = [(1, "resistor_150"), (2, "bottom_4k7")]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def tutor(self, lesson=1, scenario="correct", student="Maya", history="h.json",
              model=None, detective_model=None, notebook=True, **board):
        self.board = FakeBoard(lesson, scenario, seed=1, **board)
        nb = Notebook(self.dir / "notebooks", student) if notebook else None
        self.session = Session(self.board, lesson=lesson, history_path=self.dir / history,
                               notebook=nb)
        self.agent = build_agent(self.session, model or ScriptedTutorModel(),
                                 detective_model=detective_model)
        self.chat = []
        return self.session

    def say(self, text):
        reply, new = ask(self.agent, self.chat, text)
        tools = [m.name for m in new if isinstance(m, ToolMessage)]
        return reply, tools, new


def questions_in(new):
    """The detective's answer, from the task tool's message."""
    msg = next(m for m in new if isinstance(m, ToolMessage) and m.name == "task")
    return json.loads(msg.content)["questions"]


# --- Detective ------------------------------------------------------------------

class TestDetective(Case):
    def test_called_for_unknown_mistakes_and_said_after_the_clue(self):
        for lesson, scenario in UNKNOWN:
            with self.subTest(scenario=scenario):
                self.tutor(lesson, scenario)
                reply, tools, new = self.say("Check my circuit")
                self.assertIn("task", tools)
                qs = questions_in(new)
                self.assertTrue(1 <= len(qs) <= 2, qs)
                clue = self.session.last_check[1]["clue"]["text"]
                self.assertIn(clue, reply)
                self.assertIn(qs[0], reply)
                self.assertLess(reply.index(clue), reply.index(qs[0]))  # after the clue
                self.assertIn("detective", reply.lower())

    def test_answer_is_careful_questions(self):
        for lesson, scenario in UNKNOWN:
            self.tutor(lesson, scenario)
            _, _, new = self.say("Check my circuit")
            for q in questions_in(new):
                self.assertTrue(q.endswith("?"), q)
                self.assertNotIn("T1", q)  # "row 10", not "T10"
                for word in CERTAIN:
                    self.assertNotIn(word, q.lower())

    def test_not_called_for_other_results(self):
        for scenario in [s for s in SCENARIOS[1] if s != "resistor_150"]:
            with self.subTest(scenario=scenario):
                self.tutor(1, scenario)
                _, tools, _ = self.say("Check my circuit")
                self.assertNotIn("task", tools)

    def test_case_file_refuses_when_the_mistake_is_known(self):
        s = self.tutor(1, "led_backwards")
        s.measure()
        self.assertIn("error", case_file(s))
        self.tutor(1, "resistor_150").measure()
        case = case_file(self.session)
        self.assertNotIn("error", case)
        self.assertEqual(case["rows"]["row 10"]["parts_here"],
                         ["220 ohm resistor (other leg)", "Red LED (long leg (+))"])

    def test_answer_rules_are_enforced_in_python(self):
        good = "Is the LED's long leg in row 10?"
        self.assertEqual(DetectiveAnswer(questions=[good]).questions, [good])
        self.assertEqual(DetectiveAnswer(questions=[]).questions, [])
        # More than two: only the first two are kept.
        self.assertEqual(DetectiveAnswer(questions=["a?", "b?", "c?"]).questions, ["a?", "b?"])
        # Never raises: an item that breaks the rules is dropped.
        for bad in ("The resistor is wrong.",                    # not a question
                    "Is it definitely the resistor?",            # sounds certain
                    "The problem is the LED, right?",
                    "Is " + "the resistor " * 20 + "in row 10?"):  # longer than 200
            with self.subTest(bad=bad):
                self.assertEqual(DetectiveAnswer(questions=[bad]).questions, [])

    def test_every_lead_passes_the_rules(self):
        directions = [("empty", "voltage in row 10", "T10"), ("almost all", "voltage", "T10"),
                      ("almost none", "voltage", "T10"), ("lower", "voltage", "T10"),
                      ("higher", "voltage", "T12"), ("almost none", "current", None),
                      ("much higher", "current", None), ("lower", "power voltage", None)]
        for lesson in LESSONS.values():
            for direction, number, row in directions:
                clue = {"details": [{"number": number, "row": row, "direction": direction}]}
                for q in leads(lesson, clue):
                    DetectiveAnswer(questions=[q])
            DetectiveAnswer(questions=leads(lesson, {"details": []})[:2])

    def test_questions_that_break_the_rules_never_reach_the_student(self):
        class Sloppy(BaseChatModel):
            @property
            def _llm_type(self):
                return "sloppy"

            def bind_tools(self, tools, **kw):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kw):
                qs = ["The resistor is definitely wrong.", "Is the resistor the 220 ohm one?",
                      "Is the LED's long leg in row 10?", "c?"]
                msg = AIMessage(content="", tool_calls=[
                    {"name": "DetectiveAnswer", "args": {"questions": qs}, "id": "s1"}])
                return ChatResult(generations=[ChatGeneration(message=msg)])

        self.tutor(1, "resistor_150", detective_model=Sloppy())
        reply, _, new = self.say("Check my circuit")
        self.assertEqual(questions_in(new), ["Is the resistor the 220 ohm one?",
                                             "Is the LED's long leg in row 10?"])
        self.assertNotIn("definitely", reply)

    def test_works_without_a_current_sensor_or_notebook(self):
        self.tutor(2, "bottom_4k7", has_current=False, notebook=False)
        reply, tools, new = self.say("Check my circuit")
        self.assertIn("task", tools)
        self.assertTrue(questions_in(new))


# --- Notebook -------------------------------------------------------------------

class TestNotebook(Case):
    def test_agent_writes_one_entry_per_check_with_its_file_tool(self):
        s = self.tutor(1, "led_backwards")
        _, tools, _ = self.say("Check my circuit")
        self.assertIn("edit_file", tools)
        self.board.set_scenario(1, "resistor_150")
        self.say("I changed it. Check my circuit")
        self.board.set_scenario(1, "correct")
        self.say("I fixed it. Check my circuit")
        entries = s.notebook.entries()
        self.assertEqual(len(entries), 3, entries)
        self.assertIn('mistake "LEDs are one-way doors". Quiz: not answered yet.', entries[0])
        self.assertIn("not on the list", entries[1])
        self.assertIn("circuit correct, lesson passed", entries[2])
        for e in entries:
            self.assertIn("Next time: ", e)
        self.assertTrue(s.notebook.text().rstrip().endswith(MARKER))

    def test_quiz_result_goes_in_the_notebook(self):
        s = self.tutor(1, "led_backwards")
        self.say("Check my circuit")
        ml = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")["mini_lesson"]
        s.record_quiz(1, "led_backwards", ml["answer"], s.last_logged["occurrence"])
        self.assertIn("Quiz: right.", s.notebook.entries()[-1])
        self.board.set_scenario(1, "led_backwards")
        self.say("Check my circuit")
        s.record_quiz(1, "led_backwards", (ml["answer"] + 1) % len(ml["options"]),
                      s.last_logged["occurrence"])
        self.assertIn("Quiz: wrong.", s.notebook.entries()[-1])
        self.assertIn("Quiz: right.", s.notebook.entries()[0])  # the old one is unchanged

    def test_kept_on_disk_and_loaded_as_memory_next_time(self):
        self.tutor(1, "led_backwards", history="day1.json")
        first, _, _ = self.say("Check my circuit")
        self.assertNotIn("notebook", first.lower())
        # The next day: a new program, a new history file, the same student.
        self.tutor(1, "led_backwards", history="day2.json")
        second, _, _ = self.say("Check my circuit")
        self.assertIn("Your notebook says this happened before", second)
        # A different student has their own, empty notebook.
        self.tutor(1, "led_backwards", student="Sam", history="sam.json")
        other, _, _ = self.say("Check my circuit")
        self.assertNotIn("notebook", other.lower())
        self.assertTrue((self.dir / "notebooks" / "maya.md").exists())
        self.assertTrue((self.dir / "notebooks" / "sam.md").exists())

    def test_memory_reaches_the_model(self):
        seen = []

        class Spy(ScriptedTutorModel):
            def _next(self, messages):
                seen.extend(m.text for m in messages if isinstance(m, SystemMessage))
                return super()._next(messages)

        nb = Notebook(self.dir / "notebooks", "Maya")
        nb.ensure()
        nb.add("- Oct 2, lesson 1: a note from last week. Next time: go slowly.")
        self.tutor(1, "correct", model=Spy())
        self.say("Check my circuit")
        self.assertTrue(any("a note from last week" in t and "<agent_memory>" in t for t in seen))

    def test_hint_and_power_off_add_nothing(self):
        s = self.tutor(1, "led_backwards")
        self.say("I need a hint")
        self.assertEqual(s.notebook.entries(), [])
        self.board.send("CUT")
        self.say("Check my circuit")  # power is off and the student did not ask for it
        self.assertEqual(s.notebook.entries(), [])

    def test_python_adds_the_entry_if_the_agent_forgot(self):
        s = self.tutor(1, "led_backwards")
        s.measure()                # a check the agent never wrote down
        s.notebook_after_turn()
        entries = s.notebook.entries()
        self.assertEqual(len(entries), 1)
        self.assertIn("Next time: look at the rows that light red first.", entries[0])
        s.notebook_after_turn()   # nothing new: nothing added
        self.assertEqual(len(s.notebook.entries()), 1)

    def test_agent_may_write_only_its_notebook(self):
        attempts = [
            ("write_file", {"file_path": "/notes.md", "content": "x"}),
            ("write_file", {"file_path": "/notebook/sam.md", "content": "x"}),
            ("read_file", {"file_path": "/notebook/sam.md"}),
            ("write_file", {"file_path": "/notebook/../escape.md", "content": "x"}),
            ("edit_file", {"file_path": "/notebook/maya.md", "old_string": MARKER,
                           "new_string": "- my note.\n" + MARKER}),
        ]

        class Tries(BaseChatModel):
            @property
            def _llm_type(self):
                return "tries"

            def bind_tools(self, tools, **kw):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kw):
                start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
                done = sum(isinstance(m, ToolMessage) for m in messages[start:])
                if done < len(attempts):
                    name, args = attempts[done]
                    msg = AIMessage(content="", tool_calls=[
                        {"name": name, "args": args, "id": f"t{done}"}])
                else:
                    msg = AIMessage(content="done")
                return ChatResult(generations=[ChatGeneration(message=msg)])

        sam = Notebook(self.dir / "notebooks", "Sam")
        sam.ensure()
        sam_before = sam.text()
        s = self.tutor(1, "correct", model=Tries())
        _, _, new = self.say("go")
        answers = [m.content for m in new if isinstance(m, ToolMessage)]
        for answer in answers[:4]:
            self.assertRegex(answer.lower(), r"denied|not allowed|error|invalid")
        self.assertIn("- my note.", s.notebook.text())             # its own file: allowed
        self.assertEqual(sam.text(), sam_before)                    # another student's: no
        on_disk = sorted(p.relative_to(self.dir).as_posix() for p in self.dir.rglob("*")
                         if p.is_file())
        self.assertEqual(on_disk, ["notebooks/maya.md", "notebooks/sam.md"])

    def test_start_over_keeps_the_old_one(self):
        nb = Notebook(self.dir / "notebooks", "Maya")
        nb.add("- old entry.")
        nb.start_over()
        self.assertEqual(nb.entries(), [])
        old = [p.name for p in (self.dir / "notebooks").glob("maya.*.md")]
        self.assertEqual(len(old), 1)

    def test_safe_names(self):
        self.assertEqual(safe_name("Maya Lee"), "maya-lee")
        self.assertEqual(safe_name("../../etc/passwd"), "etc-passwd")
        self.assertEqual(safe_name(""), "student")
        self.assertEqual(default_history("student").name, "student_history.json")
        self.assertEqual(default_history("Maya").name, "history_maya.json")


class TestOnThePage(Case):
    def setUp(self):
        super().setUp()
        self.nb = Notebook(self.dir / "notebooks", "Maya")
        self.board = FakeBoard(1, "resistor_150", seed=1)
        self.app = App(self.board, lesson=1, history_path=self.dir / "h.json",
                       model=ScriptedTutorModel(), notebook=self.nb)

    def test_detective_and_notebook_in_the_tutor_log(self):
        out = self.app.ask("Check my circuit")
        self.assertIn("task", out["tools"])
        self.assertIn("edit_file", out["tools"])
        self.assertIn("detective", out["reply"].lower())
        self.assertEqual(len(self.nb.entries()), 1)
        page = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
        self.assertIn('task: "asked the detective"', page)
        self.assertIn('edit_file: "wrote in the notebook"', page)

    def test_quiz_on_the_page_fills_the_notebook(self):
        self.board.set_scenario(1, "led_backwards")
        self.app.ask("Check my circuit")
        card = self.app.card
        ml = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")["mini_lesson"]
        self.app.quiz(card["occurrence"], ml["answer"])
        self.assertIn("Quiz: right.", self.nb.entries()[-1])

    def test_new_student_starts_a_new_notebook(self):
        self.app.ask("Check my circuit")
        self.app.reset()
        self.assertEqual(self.nb.entries(), [])
        self.assertEqual(len(list((self.dir / "notebooks").glob("maya.*.md"))), 1)

    def test_notebook_address(self):
        import threading
        import urllib.request
        from web.server import make_server
        self.app.ask("Check my circuit")
        server = make_server(self.app, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/notebook"
            with urllib.request.urlopen(url, timeout=10) as r:
                text = r.read().decode()
                self.assertIn("text/plain", r.headers["Content-Type"])
        finally:
            server.shutdown()
            server.server_close()
        self.assertIn("# Notebook: Maya", text)
        self.assertIn("not on the list", text)


if __name__ == "__main__":
    unittest.main()
