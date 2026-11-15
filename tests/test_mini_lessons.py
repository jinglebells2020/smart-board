"""Tests for mini-lessons: lesson content, the quiz, the web page.

Middle school students (11 to 14) see this text, so the tests also check
reading level and plain words. Run from the repository folder:

    python -m unittest -v
"""

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from agent.session import Session
from sim.fake_board import SCENARIOS, UNLISTED, FakeBoard
from web.server import App, make_server

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
ALL_MISTAKES = [(lid, m) for lid, l in LESSONS.items() for m in l["mistakes"]]

# Words a middle schooler should not have to know. Plain words replace them:
# "measuring row" for probe, "long leg (+)" for anode, "middle" for node.
HARD_WORDS = ["probe", "anode", "cathode", "node", "polarity", "simulator",
              "rail", "agent", "scenario"]

# Verbs that tell the student what to do on the board. The "idea" of a
# mini-lesson explains the concept only, so none of these may appear in it.
FIX_WORDS = ["flip", "move", "swap", "put", "check", "turn", "replace",
             "change", "use", "plug", "remove", "add", "try", "connect it"]


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def syllables(word):
    w = word.lower()
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def reading_grade(text):
    """Flesch-Kincaid grade level (a rough, standard estimate)."""
    words = re.findall(r"[A-Za-z']+", text)
    syl = sum(syllables(w) for w in words)
    return 0.39 * len(words) / len(sentences(text)) + 11.8 * syl / len(words) - 15.59


def student_texts(lesson):
    """Every lesson string a student can see on the page."""
    texts = [lesson["title"], lesson["circuit"]]
    for part in lesson["layout"]:
        texts += [part["part"]] + [leg["leg"] for leg in part["legs"]]
    for m in lesson["mistakes"]:
        ml = m["mini_lesson"]
        texts += [m["description"], m["hint"], m["explain"], ml["title"], ml["idea"],
                  ml["question"], ml["why"], *ml["options"]]
        ns = m.get("no_sensor") or {}  # the texts a board with no current sensor uses
        texts += [ns[k] for k in ("description", "hint", "explain") if k in ns]
    return texts


def has_word(text, word):
    return re.search(rf"\b{re.escape(word)}", text, re.IGNORECASE) is not None


def visible_page_text():
    """Text on web/index.html a student can see: everything outside tags,
    plus the string literals in the script (button labels and messages)."""
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    body = re.sub(r"<script>.*?</script>", " ", html, flags=re.S)
    # The "Agent activity" panel is for the judges and the teacher (closed for
    # the student): it names the agents. Only that block is left out.
    body = re.sub(r"<!-- judges: start.*?<!-- judges: end -->", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    tag_text = re.sub(r"<[^>]+>", " ", body)
    # attributes that are shown: placeholder, title, aria-label
    attrs = re.findall(r'(?:placeholder|title|aria-label)="([^"]*)"', body)
    literals = []
    for js in scripts:
        js = re.sub(r"//[^\n]*", " ", js)  # comments are not shown
        literals += re.findall(r'"((?:[^"\\]|\\.)*)"', js)
        literals += re.findall(r"`((?:[^`\\]|\\.)*)`", js)
    # Strings that are code, not text: element ids, CSS classes, URLs, keys.
    shown = [t for t in literals if " " in t or t[:1].isupper()]
    return " ".join([tag_text, *attrs, *shown])


class TestMiniLessonContent(unittest.TestCase):
    def test_every_mistake_has_a_complete_mini_lesson(self):
        for lid, m in ALL_MISTAKES:
            with self.subTest(lesson=lid, mistake=m["id"]):
                self.assertTrue(m["hint"].strip() and m["explain"].strip())  # kept
                ml = m["mini_lesson"]
                self.assertTrue(3 <= len(ml["title"].split()) <= 6, ml["title"])
                self.assertIn(len(sentences(ml["idea"])), (2, 3), ml["idea"])
                self.assertTrue(ml["question"].endswith("?"))
                self.assertEqual(len(ml["options"]), 3)
                self.assertEqual(len(set(ml["options"])), 3)
                self.assertTrue(all(o.strip() for o in ml["options"]))
                self.assertIsInstance(ml["answer"], int)
                self.assertNotIsInstance(ml["answer"], bool)
                self.assertIn(ml["answer"], (0, 1, 2))
                self.assertEqual(len(sentences(ml["why"])), 1, ml["why"])

    def test_reading_level_is_middle_school(self):
        for lid, m in ALL_MISTAKES:
            ml = m["mini_lesson"]
            texts = [("idea", ml["idea"]), ("hint", m["hint"]),
                     ("question + why", ml["question"] + " " + ml["why"])]
            if m.get("no_sensor"):
                texts.append(("no-sensor hint", m["no_sensor"]["hint"]))
            for name, text in texts:
                with self.subTest(lesson=lid, mistake=m["id"], text=name):
                    self.assertLessEqual(reading_grade(text), 7.0, text)

    def test_idea_explains_the_concept_not_the_fix(self):
        for lid, m in ALL_MISTAKES:
            idea = m["mini_lesson"]["idea"]
            with self.subTest(lesson=lid, mistake=m["id"]):
                for word in FIX_WORDS:
                    self.assertFalse(has_word(idea, word), f"{word!r} in: {idea}")
                self.assertNotIn(m["explain"], idea)
                self.assertNotEqual(idea, m["hint"])

    def test_idea_has_an_everyday_comparison(self):
        for lid, m in ALL_MISTAKES:
            idea = m["mini_lesson"]["idea"]
            with self.subTest(lesson=lid, mistake=m["id"]):
                self.assertTrue(has_word(idea, "like"), idea)

    def test_quiz_answers_are_not_always_in_the_same_place(self):
        answers = {m["mini_lesson"]["answer"] for _, m in ALL_MISTAKES}
        self.assertGreater(len(answers), 1)

    def test_no_hard_words_in_lesson_text(self):
        for lid, lesson in LESSONS.items():
            for text in student_texts(lesson):
                for word in HARD_WORDS:
                    with self.subTest(lesson=lid, word=word):
                        self.assertFalse(has_word(text, word), text)


class TestLedBackwardsHasTwoCauses(unittest.TestCase):
    """0 mA with 5 V before the LED: the LED may be backwards, OR its long
    leg may not be in the resistor's row. The board cannot tell which."""

    def setUp(self):
        self.m = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "led_backwards")
        ml = self.m["mini_lesson"]
        self.texts = {
            "description": self.m["description"], "hint": self.m["hint"],
            "explain": self.m["explain"],
            "mini_lesson": " ".join([ml["idea"], ml["question"],
                                     ml["options"][ml["answer"]], ml["why"]]),
        }

    def test_both_causes_are_covered(self):
        direction = r"backward|wrong way|which way it faces|one direction|one way"
        connection = r"row|not connected|connected"
        for name, text in self.texts.items():
            with self.subTest(field=name):
                self.assertRegex(text.lower(), direction)
                self.assertRegex(text.lower(), connection)

    def test_neither_cause_is_stated_as_certain(self):
        for name, text in self.texts.items():
            with self.subTest(field=name):
                self.assertNotRegex(text, r"\bLED is (in )?backwards\b")
                self.assertNotRegex(text, r"\bleg is not in\b")
        self.assertRegex(self.m["description"], r"\bmay\b")
        self.assertRegex(self.m["explain"], r"\bmay\b")
        self.assertIn("cannot tell", self.m["explain"])

    def test_simulator_still_detects_it(self):
        session = Session(FakeBoard(1, "led_backwards", seed=5), lesson=1)
        _, result, _ = session.measure()
        self.assertEqual(result.get("mistake_id"), "led_backwards")


class TestSessionMiniLesson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_log_mistake_returns_mini_lesson_and_keeps_hint_first(self):
        for lid, m in ALL_MISTAKES:
            with self.subTest(lesson=lid, mistake=m["id"]):
                session = Session(FakeBoard(lid), lesson=lid)
                first = session.log_mistake(m["id"])
                self.assertEqual(first["level"], "hint")
                self.assertEqual(first["say"], m["hint"])
                self.assertEqual(first["mini_lesson"], m["mini_lesson"])
                # The full answer never appears on the first try.
                self.assertNotIn(m["explain"], json.dumps(first))
                second = session.log_mistake(m["id"])
                self.assertEqual(second["level"], "explain")
                self.assertEqual(second["mini_lesson"], m["mini_lesson"])

    def test_mini_lesson_is_a_copy(self):
        session = Session(FakeBoard(1), lesson=1)
        session.log_mistake("open_circuit")["mini_lesson"]["title"] = "changed"
        spec = next(m for m in session.lesson["mistakes"] if m["id"] == "open_circuit")
        self.assertNotEqual(spec["mini_lesson"]["title"], "changed")

    def test_log_mistake_remembers_which_one(self):
        session = Session(FakeBoard(1), lesson=1)
        session.log_mistake("led_backwards")
        session.log_mistake("open_circuit")
        self.assertEqual(session.last_logged,
                         {"lesson": 1, "mistake": "open_circuit", "occurrence": 1})

    def test_right_and_wrong_answers_are_saved_in_the_history_file(self):
        session = Session(FakeBoard(1), lesson=1, history_path=self.path)
        spec = next(m for m in LESSONS[1]["mistakes"] if m["id"] == "no_resistor")
        right = spec["mini_lesson"]["answer"]
        wrong = (right + 1) % 3
        r1 = session.record_quiz(1, "no_resistor", right, occurrence=0)
        r2 = session.record_quiz(1, "no_resistor", wrong, occurrence=1)
        self.assertEqual((r1["correct"], r2["correct"]), (True, False))
        self.assertEqual(r1["why"], spec["mini_lesson"]["why"])
        self.assertEqual(r2["answer"], right)

        saved = json.loads(self.path.read_text(encoding="utf-8"))["quiz"]
        self.assertEqual([(q["mistake"], q["choice"], q["correct"]) for q in saved],
                         [("no_resistor", right, True), ("no_resistor", wrong, False)])
        again = Session(FakeBoard(1), history_path=self.path)
        self.assertTrue(again.quiz_result(0)["correct"])
        self.assertFalse(again.quiz_result(1)["correct"])
        self.assertIsNone(again.quiz_result(2))

    def test_quiz_answer_for_a_lesson_2_mistake(self):
        session = Session(FakeBoard(1), lesson=1)  # still on lesson 1
        out = session.record_quiz(2, "bottom_1k", 1)
        self.assertTrue(out["correct"])

    def test_bad_quiz_input_is_rejected_and_not_saved(self):
        session = Session(FakeBoard(1), lesson=1, history_path=self.path)
        for args in [(1, "led_backwards", 3), (1, "led_backwards", -1),
                     (1, "led_backwards", "1"), (1, "led_backwards", True),
                     (1, "led_backwards", None), (1, "bottom_1k", 0),
                     (9, "led_backwards", 0), (1, "nope", 0)]:
            with self.subTest(args=args):
                self.assertIn("error", session.record_quiz(*args))
        self.assertEqual(session.history["quiz"], [])
        self.assertFalse(self.path.exists())

    def test_old_history_file_without_quiz_still_loads(self):
        self.path.write_text(json.dumps({"mistakes": [], "passes": {}}), encoding="utf-8")
        session = Session(FakeBoard(1), history_path=self.path)
        self.assertTrue(session.record_quiz(1, "open_circuit", 0)["correct"])


class _NegativeCurrentBoard(FakeBoard):
    """A board whose sensor noise gives slightly negative readings."""

    def read(self):
        r = super().read()
        r["current_ma"] = -0.02
        r["rows"]["T10"] = -0.001
        return r


class TestWebMiniLesson(unittest.TestCase):
    def start(self, board=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self.tmp.name) / "h.json"
        self.app = App(board or FakeBoard(1, "correct", seed=2), lesson=1,
                       history_path=self.history_path, model=ScriptedTutorModel())
        self.server = make_server(self.app, port=0)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def setUp(self):
        self.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def state(self):
        with urllib.request.urlopen(self.url + "/api/state", timeout=10) as r:
            return json.loads(r.read())

    def make_mistake(self, scenario):
        self.post("/api/build", {"scenario": scenario})
        return self.post("/api/ask", {"text": "Check my circuit"})

    def spec(self, lesson, mistake_id):
        return next(m for m in LESSONS[lesson]["mistakes"] if m["id"] == mistake_id)

    def test_card_appears_after_a_mistake_without_the_answer(self):
        self.assertIsNone(self.state()["mini_lesson"])
        self.make_mistake("led_backwards")
        card = self.state()["mini_lesson"]
        ml = self.spec(1, "led_backwards")["mini_lesson"]
        self.assertEqual((card["title"], card["idea"], card["question"], card["options"]),
                         (ml["title"], ml["idea"], ml["question"], ml["options"]))
        self.assertIsNone(card["answered"])
        self.assertNotIn("answer", card)
        self.assertNotIn("why", card)

    def test_every_mistake_shows_its_own_card(self):
        for lesson, scenarios in SCENARIOS.items():
            if LESSONS[lesson].get("states"):
                continue  # a lesson with steps (lesson 4): tests/test_button_lesson.py
            for scenario in scenarios:
                if scenario == "correct" or scenario in UNLISTED[lesson]:
                    continue
                with self.subTest(lesson=lesson, mistake=scenario):
                    self.post("/api/reset", {})
                    for _ in range(1, lesson):  # pass the lessons before it
                        self.post("/api/ask", {"text": "check"})
                    self.assertEqual(self.state()["lesson"]["id"], lesson)
                    out = self.make_mistake(scenario)
                    card = self.state()["mini_lesson"]
                    self.assertEqual(card["title"],
                                     self.spec(lesson, scenario)["mini_lesson"]["title"])
                    for word in HARD_WORDS:  # the tutor's reply is plain too
                        self.assertFalse(has_word(out["reply"], word), out["reply"])

    def test_right_answer_is_saved_and_shown(self):
        self.make_mistake("open_circuit")
        card = self.state()["mini_lesson"]
        ml = self.spec(1, "open_circuit")["mini_lesson"]
        out = self.post("/api/quiz", {"occurrence": card["occurrence"], "choice": ml["answer"]})
        self.assertTrue(out["correct"])
        self.assertEqual(out["why"], ml["why"])
        s = self.state()
        self.assertEqual(s["mini_lesson"]["answered"],
                         {"choice": ml["answer"], "correct": True,
                          "answer": ml["answer"], "why": ml["why"]})
        self.assertEqual(s["history"][-1]["quiz"], "right")
        saved = json.loads(self.history_path.read_text(encoding="utf-8"))["quiz"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["mistake"], "open_circuit")

    def test_wrong_answer_is_saved_and_shown(self):
        self.make_mistake("led_backwards")
        card = self.state()["mini_lesson"]
        wrong = (self.spec(1, "led_backwards")["mini_lesson"]["answer"] + 1) % 3
        out = self.post("/api/quiz", {"occurrence": card["occurrence"], "choice": wrong})
        self.assertFalse(out["correct"])
        self.assertEqual(self.state()["history"][-1]["quiz"], "wrong")

    def test_only_the_first_answer_counts(self):
        self.make_mistake("led_backwards")
        occ = self.state()["mini_lesson"]["occurrence"]
        right = self.spec(1, "led_backwards")["mini_lesson"]["answer"]
        first = self.post("/api/quiz", {"occurrence": occ, "choice": (right + 1) % 3})
        second = self.post("/api/quiz", {"occurrence": occ, "choice": right})
        self.assertFalse(first["correct"])
        self.assertFalse(second["correct"])
        self.assertTrue(second["already_answered"])
        self.assertEqual(len(self.app.session.history["quiz"]), 1)

    def test_card_goes_away_when_the_circuit_is_correct(self):
        self.make_mistake("led_backwards")
        self.assertIsNotNone(self.state()["mini_lesson"])
        self.post("/api/build", {"scenario": "correct"})
        self.post("/api/ask", {"text": "check again"})
        self.assertIsNone(self.state()["mini_lesson"])

    def test_bad_quiz_requests(self):
        self.assertIn("error", self.post("/api/quiz", {"occurrence": 0, "choice": 0}))  # no card
        self.make_mistake("no_resistor")
        occ = self.state()["mini_lesson"]["occurrence"]
        self.assertIn("error", self.post("/api/quiz", {"occurrence": occ + 5, "choice": 0}))
        self.assertIn("error", self.post("/api/quiz", {"occurrence": occ, "choice": 7}))
        self.assertIn("error", self.post("/api/quiz", {"occurrence": occ}))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/quiz", [1, 2])
        self.assertEqual(e.exception.code, 400)
        self.assertEqual(self.app.session.history["quiz"], [])

    def test_reset_clears_the_card(self):
        self.make_mistake("open_circuit")
        self.post("/api/reset", {})
        self.assertIsNone(self.state()["mini_lesson"])

    def test_shown_numbers_are_never_negative(self):
        self.tearDown()
        self.start(_NegativeCurrentBoard(1, "correct", seed=1))
        s = self.state()
        self.assertEqual(s["reading"]["current_ma"], -0.02)  # protocol unchanged
        self.assertEqual(s["shown"]["current_ma"], 0.0)
        t10 = next(r for r in s["board"]["rows"] if r["name"] == "T10")
        self.assertEqual(t10["volts"], 0.0)
        self.assertEqual(str(t10["volts"]), "0.0")  # not -0.0
        self.assertEqual(str(s["shown"]["current_ma"]), "0.0")  # not -0.0

    def test_shown_values_match_normal_readings(self):
        s = self.state()
        self.assertAlmostEqual(s["shown"]["current_ma"], s["reading"]["current_ma"], places=3)
        self.assertAlmostEqual(s["shown"]["supply_v"], s["reading"]["supply_v"], places=3)


class TestPage(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_page_has_the_card_and_uses_the_quiz_api(self):
        for needed in ['id="mini"', 'id="mini-title"', 'id="mini-idea"',
                       'id="mini-question"', 'id="mini-options"', 'id="mini-result"',
                       '"/api/quiz"', "state.shown"]:
            self.assertIn(needed, self.html)

    def test_current_is_clamped_on_the_page_too(self):
        self.assertIn("Math.max(0, shown.current_ma)", self.html)

    def test_no_hard_words_on_the_page(self):
        text = visible_page_text()
        self.assertIn("Check my circuit", text)  # the extraction works
        self.assertIn("The plan: where each part goes", text)
        for word in HARD_WORDS:
            with self.subTest(word=word):
                self.assertFalse(has_word(text, word),
                                 re.findall(rf".{{0,40}}\b{word}.{{0,40}}", text, re.I))

    def test_stays_on_one_projector_screen(self):
        # Widescreen: three zones fill the window exactly (100vh), and long
        # replies scroll inside Zip's bubble instead of growing the page.
        self.assertRegex(self.html, r"\.app \{[^}]*height: 100vh")
        self.assertIn("grid-template-rows: 72px minmax(0, 1fr)", self.html)
        self.assertRegex(self.html, r"\.hint \{[^}]*overflow-y: auto")


if __name__ == "__main__":
    unittest.main()
