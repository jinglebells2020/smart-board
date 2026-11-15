"""What a kids' STEM center asked for, and the "Genesis" name.

Their words: kids struggle with "polarity of components (e.g. LED)",
"keeping the idea of what lines of holes are connected in the breadboard",
and "loose connections". "If many kids are stuck we show slides for
breadboards or draw on the whiteboard the concept."
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from agent.build import ask, build_agent
from agent.lesson_writer import check_lesson
from agent.lessons import check, load_lessons, summarize
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from protocol import SCREEN_CHARS, SCREEN_LINES
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def window(lesson, scenario, seed=1, n=10, **opts):
    board = FakeBoard(lesson, scenario, seed=seed, **opts)
    return [board.read() for _ in range(n)]


class TestTheName(unittest.TestCase):
    def test_users_see_genesis(self):
        self.assertIn("<title>Genesis: Smart Breadboard</title>", HTML)
        brand = re.search(r'<div class="brand">.*?</div>', HTML, re.S).group(0)
        self.assertIn("Genesis", brand)
        self.assertIn("your Genesis guide", HTML)  # Zip names the app

    def test_board_screen_welcome_names_the_app_and_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            for lesson in (0, 1, 2):
                app = App(FakeBoard(1, seed=1), lesson=lesson, model=ScriptedTutorModel(),
                          history_path=Path(tmp) / f"h{lesson}.json")
                screen = app.state()["board"]["screen"]
                self.assertEqual(screen[0], "Genesis")
                self.assertTrue(screen[-1] in ("Press blue to start.", "Tap the holes!"), screen)
                self.assertLessEqual(len(screen), SCREEN_LINES)
                self.assertTrue(all(len(line) <= SCREEN_CHARS for line in screen))

    def test_docs_use_the_full_name(self):
        for doc in ["README.md", "RUN.md", "CLAUDE.md"] + [str(p.relative_to(ROOT))
                                                          for p in (ROOT / "docs").glob("*.md")]:
            first = (ROOT / doc).read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("Genesis: Smart Breadboard", first, doc)


class TestLessonZero(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "h.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_lesson_zero_comes_first_and_has_a_good_mini_lesson(self):
        self.assertEqual(min(LESSONS), 0)
        l0 = LESSONS[0]
        self.assertEqual(l0["kind"], "explore")
        self.assertEqual(l0["layout"], [])  # nothing to build
        ml = l0["intro_lesson"]
        self.assertTrue(all(ok for _, ok, _ in check_lesson(ml)), check_lesson(ml))
        self.assertEqual(SCENARIOS[0], ["correct"])

    def test_a_new_student_starts_at_lesson_zero(self):
        self.assertEqual(Session(FakeBoard(), lesson="start", history_path=self.path).lesson_id, 0)
        self.path.write_text(json.dumps({"mistakes": [], "passes": {"0": 1, "1": 1}, "quiz": []}))
        self.assertEqual(Session(FakeBoard(), lesson="start", history_path=self.path).lesson_id, 2)

    def test_it_passes_once_the_question_is_answered(self):
        board = FakeBoard(0, "correct", seed=1)
        s = Session(board, lesson="start", history_path=self.path)
        agent = build_agent(s, ScriptedTutorModel())
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("answer the question", reply)
        self.assertEqual(s.last_check[1]["status"], "quiz_first")
        s.record_quiz(0, "intro", 2, "intro")  # even a wrong answer counts as answered
        reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("You know how a breadboard connects", reply)
        self.assertEqual(s.lesson_id, 1)
        nxt = s.next_lesson()  # never back to lesson 0
        self.assertNotEqual(nxt["lesson"], 0)

    def test_page_shows_the_question_card_and_no_writer_on_wrong(self):
        app = App(FakeBoard(0, "correct", seed=1), lesson="start", history_path=self.path,
                  model=ScriptedTutorModel())
        card = app.state()["mini_lesson"]
        self.assertTrue(card["intro"])
        self.assertEqual(card["title"], LESSONS[0]["intro_lesson"]["title"])
        out = app.quiz("intro", (LESSONS[0]["intro_lesson"]["answer"] + 1) % 3)
        self.assertFalse(out["correct"])
        app.wait_for_buttons()
        self.assertEqual(app.log, [])  # a wrong answer here needs no new lesson
        self.assertTrue(app.session.intro_answered())

    def test_terminal_tutor_can_answer_the_question(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(
                [sys.executable, "-m", "agent", "--fresh", "--student", "cli-test",
                 "--history", str(Path(tmp) / "h.json"), "--notebooks", tmp],
                input="/answer 1\ncheck my circuit\n/quit\n", capture_output=True,
                text=True, cwd=ROOT, env=env, timeout=120).stdout
        self.assertIn("Lesson 0: How a breadboard connects", out)
        self.assertIn("Which hole is joined to it?", out)
        self.assertIn("Right!", out)
        self.assertIn("You know how a breadboard connects", out)

    def test_drawing_lights_joined_holes(self):
        for needed in ('"data-group": group', "function showJoined", "function groupWords",
                       '"T" + n', '"B" + n', '"PT+"', '"PB-"', "L.explore",
                       "these ${count} holes are joined", "all of its holes are joined"):
            self.assertIn(needed, HTML)
        self.assertIn('tabindex: "0", role: "button"', HTML)  # works with the keyboard too


class TestLooseConnection(unittest.TestCase):
    def test_flickering_row_is_found(self):
        for lesson, scenario in ((1, "loose_led_leg"), (2, "loose_resistor_leg")):
            for has_current in (True, False):
                for seed in range(5):
                    s = summarize(window(lesson, scenario, seed, has_current=has_current),
                                  LESSONS[lesson])
                    self.assertEqual(s["flickering"], ["T10"])
                    r = check(s, LESSONS[lesson])
                    self.assertEqual((r["mistake_id"], r["rows"], r["light"]),
                                     (scenario, ["T10"], "yellow"))

    def test_it_matches_only_itself(self):
        for lid, scenarios in SCENARIOS.items():
            for scenario in scenarios:
                if scenario.startswith("loose_"):
                    continue
                s = summarize(window(lid, scenario), LESSONS[lid])
                self.assertEqual(s["flickering"], [], scenario)
                self.assertFalse(str(check(s, LESSONS[lid]).get("mistake_id")).startswith("loose"))

    def test_one_glitch_is_not_a_loose_leg(self):
        readings = window(1, "correct")
        readings[3] = dict(readings[3], floating=readings[3]["floating"] + ["T10"])
        self.assertEqual(summarize(readings, LESSONS[1])["flickering"], [])

    def test_tutor_lights_yellow_and_says_push_it_in(self):
        board = FakeBoard(1, "loose_led_leg", seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(board, lesson=1, history_path=Path(tmp) / "h.json")
            reply, _ = ask(build_agent(s, ScriptedTutorModel()), [], "Check my circuit")
        self.assertIn("push that leg in firmly", reply)
        self.assertEqual(board.lights, {"T10": "yellow"})
        self.assertTrue(board.power_on)  # not dangerous
        ml = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "loose_led_leg")["mini_lesson"]
        self.assertIn("loose", ml["title"].lower())
        self.assertTrue(all(ok for _, ok, _ in check_lesson(ml)))

    def test_page_has_a_practice_button(self):
        self.assertIn('loose_led_leg: "Loose LED leg"', HTML)
        self.assertEqual(UNLISTED[1], ["resistor_150"])


class TestPolarity(unittest.TestCase):
    def test_hint_and_drawing_point_at_the_long_leg(self):
        m = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")
        self.assertIn("long leg", m["hint"])
        self.assertEqual(m["point_at"], {"part": "Red LED", "leg": "long leg (+)"})
        self.assertIn(m["point_at"]["leg"], [leg["leg"] for p in LESSONS[1]["layout"]
                                              for leg in p["legs"] if p["part"] == "Red LED"])
        with tempfile.TemporaryDirectory() as tmp:
            app = App(FakeBoard(1, "led_backwards", seed=1), lesson=1,
                      history_path=Path(tmp) / "h.json", model=ScriptedTutorModel())
            app.ask("Check my circuit")
            self.assertEqual(app.state()["mini_lesson"]["point_at"], m["point_at"])

    def test_led_is_drawn_with_a_plus_and_a_flat_side(self):
        for needed in ('class: "bb-flat"', '"bb-plus" }, g, "+")', "state.mini_lesson.point_at",
                       "refs.points[part + \"|\" + legName]"):
            self.assertIn(needed, HTML)


class TestShowTheClass(unittest.TestCase):
    def test_every_card_has_the_button_and_a_full_screen_slide(self):
        mini = re.search(r'<div class="mini" id="mini".*?</div>\s*</div>', HTML, re.S).group(0)
        self.assertIn('id="show-class"', mini)
        self.assertIn("Show the class", mini)
        for needed in ('id="slide"', 'id="slide-title"', 'id="slide-idea"', 'id="slide-question"',
                       'id="slide-bb"', "function openSlide", "function closeSlide"):
            self.assertIn(needed, HTML)
        self.assertRegex(HTML, r"\.slide \{[^}]*position: fixed; inset: 0")
        self.assertRegex(HTML, r'if \(!\$\("slide"\)\.hidden\) \{ closeSlide\(\); return; \}')  # Esc
        self.assertRegex(HTML, r"\.slide-head h2 \{[^}]*font-size: 36px")
        self.assertRegex(HTML, r"\.slide-text \{[^}]*font-size: 28px")


class TestConsultedWith(unittest.TestCase):
    def test_readme_names_the_center_and_keeps_the_quote(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        part = readme.split("## Consulted with", 1)[1].split("\n## ", 1)[0]
        for needed in ("STEAM Works Studio", "Princeton, NJ", "Center Director",
                       "November 14, 2026", "email", "Polarity", "Lesson 0",
                       "Loose connections", "Show the class"):
            self.assertIn(needed, part)
        quote = " ".join(line[2:] for line in part.splitlines() if line.startswith("> "))
        self.assertEqual(quote, '"Polarity of component (ex LED) keeping the idea of what lines of '
                                'holes are connected in breadboard, lose connections. If many kids '
                                'are stuck we show the slides for breadboards or draw on whiteboard '
                                'the concept to clarify. Need to see an example to evaluate."')
        self.assertNotIn("[CENTER NAME]", part)

if __name__ == "__main__":
    unittest.main()
