"""Agent activity: the judges' live panel of everything Zip's agents do.

Built only from what is already recorded (agent/trace.py) plus instant help
and plain-Python safety cuts. No logic changes: these tests only read."""

import re
import tempfile
import unittest
from pathlib import Path

from agent.activity import AGENTS, KEEP, KINDS, Activity, classify
from agent.build import build_agent
from agent.detective import ScriptedDetectiveModel
from sim.fake_board import FakeBoard
from tests.test_instant import InstantApp, TinyDiagnoser
from tests.test_mini_lessons import HARD_WORDS, has_word
from tests.test_zip_acts import TURN_LED, ScriptModel, experiment
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def items(app, agent=None, kind=None):
    return [i for i in app.state()["activity"]
            if (agent is None or i["agent"] == agent) and (kind is None or i["kind"] == kind)]


def actions(app, agent=None, kind=None):
    return [i["action"] for i in items(app, agent, kind)]


class TestScriptedRun(unittest.TestCase):
    """Tutor + diagnoser + experiment + a safety cut, on the page's App."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = FakeBoard(1, "led_backwards", seed=1)
        self.model = ScriptModel(script=[{"tool": "read_rows"}, experiment(),
                                         {"reply": "It was backwards!"}])
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=self.model, confirm_power=True)
        # With Claude the diagnoser runs on every check: a tiny fake one here.
        self.app.agent = build_agent(self.app.session, self.model,
                                     detective_model=ScriptedDetectiveModel(),
                                     ask_before_power=True, diagnoser_model=TinyDiagnoser())

    def test_agents_kinds_and_counts(self):
        self.assertEqual(self.app.state()["activity"], [])
        self.assertEqual(self.app.state()["activity_count"]["total"], 0)

        self.app.ask("Check my circuit")  # pauses at the experiment card
        live = actions(self.app)
        self.assertIn(f"Asked you to {TURN_LED[0].lower()}{TURN_LED[1:-1]}", live)  # live
        self.board.set_scenario(1, "correct")
        self.app.answer_experiment(True)

        self.assertIn("Measured the rows", actions(self.app, "Zip (tutor)", "measure"))
        # One diagnosis: the facts are plain Python, then 3 steps of the diagnoser.
        python = actions(self.app, "Python safety", "check")
        self.assertTrue(any(a.startswith("Worked out") for a in python), python)
        diagnoser = actions(self.app, "Diagnoser")
        self.assertIn("Asked the diagnoser", actions(self.app, "Diagnoser", "think"))
        self.assertIn("The diagnoser answered: not sure yet, 0 possible causes",
                      actions(self.app, "Diagnoser", "think"))
        self.assertTrue(any(a.startswith("Checked every cause")
                            for a in actions(self.app, "Diagnoser", "check")), diagnoser)
        asked = diagnoser.count("Asked the diagnoser")
        self.assertEqual(asked, 2)  # the check, and measuring again after the experiment
        self.assertEqual(len(diagnoser), 3 * asked)
        self.assertFalse(any(a.startswith("Worked out") for a in diagnoser))
        self.assertIn("Asked you to turn around the LED in rows 10 and 12",
                      actions(self.app, "Zip (tutor)", "ask student"))
        self.assertTrue(any(a.startswith("Measured again: row 10 changed")
                            for a in actions(self.app, "Zip (tutor)", "measure")))
        # The experiment step is listed once, though the tool runs again on "done".
        self.assertEqual(sum(a.startswith("Asked you to turn around") for a in actions(self.app)), 1)

        # Now a dangerous circuit: plain Python cuts the power at once.
        self.board.set_scenario(1, "no_resistor")
        self.model.script = [{"tool": "read_rows"},
                             {"tool": "cut_power", "args": {"reason": "too much current"}},
                             {"reply": "I switched the power off."}]
        self.app.ask("Check my circuit")
        self.assertFalse(self.board.power_on)
        cut = actions(self.app, "Python safety", "act on board")
        self.assertTrue(any(a.startswith("Switched the power off at once") for a in cut), cut)
        self.assertIn("Cut the power: too much current",
                      actions(self.app, "Zip (tutor)", "act on board"))

        state = self.app.state()
        count = state["activity_count"]
        self.assertEqual(count["total"], sum(count["by_agent"].values()))
        self.assertEqual(count["total"], len(state["activity"]))  # under 50: all listed
        self.assertEqual(count["replies"], 2)  # the paused one counts once
        self.assertEqual(count["experiments"], 1)
        self.assertGreaterEqual(count["power_cuts"], 2)  # Python's cut and Zip's
        self.assertEqual(set(count["by_agent"]), {"Zip (tutor)", "Diagnoser", "Python safety"})
        for item in state["activity"]:
            self.assertEqual(set(item), {"time", "agent", "action", "kind", "tag"})
            self.assertIsNone(item["tag"])  # every step here was asked for
            self.assertIn(item["agent"], AGENTS)
            self.assertIn(item["kind"], KINDS)
        times = [i["time"] for i in state["activity"]]
        self.assertEqual(times, sorted(times, reverse=True))  # newest first
        self.assertEqual(state["activity"][0]["action"], "Cut the power: too much current")

    def test_reset_starts_a_new_list(self):
        self.board.set_scenario(1, "correct")
        self.model.script = [{"tool": "read_rows"}, {"reply": "Fine."}]
        self.app.ask("Check my circuit")
        self.assertGreater(self.app.state()["activity_count"]["total"], 0)
        self.app.reset()
        self.assertEqual(self.app.state()["activity_count"]["total"], 0)


class TestInstant(InstantApp):
    def test_build_guide_and_instant_help(self):
        self.make()
        self.step("BUILD 1")  # a part goes in: a silent build-guide turn
        self.assertIn("Saw the board change and checked it: 1 of 4 parts in",
                      actions(self.app, "Instant help", "check"))
        self.assertIn("Looked at which parts are in", actions(self.app, "Build guide", "measure"))
        self.assertIn("Lit rows 5 and 10 blue", actions(self.app, "Build guide", "act on board"))
        count = self.app.state()["activity_count"]
        self.assertEqual(count["replies"], 1)
        self.assertGreaterEqual(count["lights_set"], 1)
        self.assertNotIn("Zip (tutor)", count["by_agent"])  # only the guide turn ran

    def test_mistake_said_at_once(self):
        self.make("stray_part")
        self.step("BUILD 1")
        self.step("BUILD 2")
        said = actions(self.app, "Instant help", "check")
        self.assertIn("Said at once: Something is connected in row 30, which is not in the plan.",
                      said)
        self.assertIn("Lit row 30 yellow", actions(self.app, "Instant help", "act on board"))
        # Then the automatic check: Zip's own steps, with a small "instant" tag.
        measured = [i for i in items(self.app, "Zip (tutor)", "measure")
                    if i["action"] == "Measured the rows"]
        self.assertTrue(measured)
        self.assertTrue(all(i["tag"] == "instant" for i in measured))
        # Instant help is only the change detector's own lines.
        for i in items(self.app, "Instant help"):
            self.assertTrue(i["action"].startswith(("Saw the board change", "Said at once",
                                                    "Lit row")), i["action"])
            self.assertIsNone(i["tag"])

    def test_instant_safety_cut(self):
        self.make("no_resistor", built=None)
        self.app.instant_tick()
        self.board.send("BUILD ALL")
        self.settle()
        self.assertFalse(self.board.power_on)
        self.assertTrue(items(self.app, "Python safety", "act on board"))
        self.assertGreaterEqual(self.app.state()["activity_count"]["power_cuts"], 1)


class TestActivity(unittest.TestCase):
    def test_capped_at_50_newest_first(self):
        a = Activity()
        for n in range(60):
            a.add("Zip (tutor)", f"step {n}", "think", when=n)
        view = a.view()
        self.assertEqual(KEEP, 50)
        self.assertEqual(len(view["activity"]), 50)
        self.assertEqual(view["activity"][0]["action"], "step 59")
        self.assertEqual(view["activity"][-1]["action"], "step 10")
        self.assertEqual(view["activity_count"]["total"], 60)  # the count is the whole session
        self.assertEqual(view["activity_count"]["by_agent"], {"Zip (tutor)": 60})

    def test_trace_read_once_and_again_for_a_new_reply(self):
        a = Activity()
        trace = [{"who": "tutor", "tool": "read_rows", "args": {}}]
        a.read_trace(trace)
        trace.append({"who": "tutor", "tool": "light_row", "args": {"rows": ["T5"], "color": "red"}})
        a.read_trace(trace)
        a.read_trace(trace)  # nothing new
        self.assertEqual([i["action"] for i in a.view()["activity"]],
                         ["Lit row 5 red", "Measured the rows"])
        a.read_trace([{"who": "tutor", "tool": "read_rows", "args": {}}])  # a new reply
        self.assertEqual(a.view()["activity_count"]["total"], 3)
        self.assertEqual(a.view()["activity_count"]["lights_set"], 1)

    def test_every_helper_has_its_agent_and_kind(self):
        cases = [
            ({"who": "tutor", "tool": "read_rows"}, "reply", ("Zip (tutor)", "measure")),
            ({"who": "tutor", "tool": "read_rows"}, "check", ("Zip (tutor)", "measure")),
            ({"who": "diagnoser", "tool": "answer"}, "reply", ("Diagnoser", "think")),
            ({"who": "diagnoser", "tool": "validate"}, "check", ("Diagnoser", "check")),
            ({"who": "diagnoser", "tool": "compare"}, "reply", ("Python safety", "check")),
            ({"who": "tutor", "tool": "light_row"}, "guide", ("Build guide", "act on board")),
            ({"who": "tutor", "tool": "run_experiment"}, "reply", ("Zip (tutor)", "ask student")),
            ({"who": "tutor", "tool": "log_mistake"}, "reply", ("Zip (tutor)", "memory")),
            ({"who": "tutor", "tool": "task"}, "reply", ("Zip (tutor)", "think")),
            ({"who": "memory", "tool": "memory"}, "reply", ("Zip (tutor)", "memory")),
            ({"who": "you", "tool": "approval"}, "reply", ("Zip (tutor)", "ask student")),
            ({"who": "detective", "tool": "look_at_the_case"}, "reply", ("Detective", "measure")),
            ({"who": "detective", "tool": "DetectiveAnswer"}, "reply", ("Detective", "think")),
            ({"who": "lesson writer", "tool": "get_lesson_facts"}, "reply",
             ("Lesson writer", "memory")),
            ({"who": "lesson writer", "tool": "write_file"}, "reply", ("Lesson writer", "memory")),
            ({"who": "grader", "tool": "grade"}, "reply", ("Grader", "check")),
            ({"who": "diagnoser", "tool": "diagnose"}, "reply", ("Diagnoser", "think")),
            ({"who": "physics", "tool": "facts"}, "reply", ("Python safety", "check")),
            ({"who": "tutor", "tool": "skip_detective"}, "reply", ("Python safety", "check")),
        ]
        for step, mode, want in cases:
            with self.subTest(step=step, mode=mode):
                self.assertEqual(classify(step, mode), want)


class TestPage(unittest.TestCase):
    def test_panel_and_counter(self):
        for marker in ('id="activity"', 'id="activity-toggle"', 'aria-expanded="false"',
                       'id="activity-body" hidden', "Zip's agents: <span id=\"activity-total\">",
                       "Agent activity", 'id="activity-chips"', 'id="activity-list"',
                       "state.activity, state.activity_count", "function renderActivity",
                       'li.className = "new"'):
            self.assertIn(marker, HTML)
        # Closed by default; the Teacher view opens it.
        teacher = re.search(r"function setTeacher\(open\) \{(.*?)\n  \}", HTML, re.S).group(1)
        self.assertIn("setActivity(true)", teacher)
        # One colour per agent, from the palette.
        for agent in AGENTS:
            self.assertIn(f'"{agent}"' if " " in agent or "(" in agent else agent, HTML)
        rules = dict(re.findall(r"\.ag-(\d) \{ background: ([^;]+); \}", HTML))
        self.assertEqual(sorted(rules), [str(n) for n in range(len(AGENTS))])
        colours = {n: tuple(re.findall(r"var\(--([\w-]+)\)", v)) for n, v in rules.items()}
        self.assertEqual(len(set(colours.values())), len(AGENTS))  # all different
        for n, used in colours.items():  # real colours, never a grey
            self.assertTrue(used, n)
            self.assertEqual(set(used) - {"blue", "sky", "ink", "green", "yellow", "coral"},
                             set(), n)
        # Zip's steps in an automatic check carry a small "instant" tag.
        self.assertIn('tag.className = "ag-tag"', HTML)
        # New items slide in, but not with reduced motion.
        self.assertIn("@keyframes slide-in", HTML)
        calm = HTML[HTML.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn(".activity-list li.new { animation: none !important; }",
                      re.sub(r"\s+", " ", calm[:calm.index("\n  }")]))

    def test_only_the_judges_block_names_the_agents(self):
        block = re.search(r"<!-- judges: start.*?<!-- judges: end -->", HTML, re.S).group(0)
        self.assertIn('id="activity"', block)
        rest = HTML.replace(block, " ")
        rest = re.sub(r"<style>.*?</style>|<script>.*?</script>|<!--.*?-->", " ", rest, flags=re.S)
        for word in HARD_WORDS:
            self.assertFalse(has_word(re.sub(r"<[^>]+>", " ", rest), word), word)


if __name__ == "__main__":
    unittest.main()
