"""The friendly page: what the server adds for it (the lesson layout for
the drawn breadboard, the lesson path with stars, Zip's mood and the
celebration), and checks on the page file itself. No new rules: stars and
moods are worked out from what already happens."""

import re
import tempfile
import unittest
from pathlib import Path

from agent.lessons import load_lessons
from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from web.server import App

ROOT = Path(__file__).resolve().parent.parent
LESSONS = load_lessons()
QUIZ = {m["id"]: m["mini_lesson"] for m in LESSONS[1]["mistakes"]}


class Case(unittest.TestCase):
    BOARD = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = FakeBoard(1, "correct", seed=3, **self.BOARD)
        self.app = App(self.board, lesson=1, history_path=Path(self.tmp.name) / "h.json",
                       model=ScriptedTutorModel())

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, scenario):
        self.app.build(scenario)

    def check(self, text="Check my circuit"):
        return self.app.ask(text)

    def quiz(self, right):
        card = self.app.card
        ml = (self.app.session.ai_lesson(card["occurrence"]) if card.get("ai")
              else QUIZ[card["mistake"]])
        choice = ml["answer"] if right else (ml["answer"] + 1) % len(ml["options"])
        return self.app.quiz(card["occurrence"], choice, written_by_zip=bool(card.get("ai")))

    def stars(self, lesson=1):
        return next(p for p in self.app.state()["path"] if p["id"] == lesson)

    def latest(self):
        return [m for m in self.app.log if m["who"] == "tutor"][-1]


class TestBreadboardData(Case):
    def test_state_has_the_lesson_layout(self):
        s = self.app.state()
        self.assertEqual(s["lesson"]["layout"], LESSONS[1]["layout"])
        self.assertEqual(s["lesson"]["rows"], LESSONS[1]["rows"])
        self.assertIn("lights", s["board"])
        self.assertIn("step", s["board"])


class TestStars(Case):
    def test_one_path_stop_per_lesson(self):
        path = self.app.state()["path"]
        self.assertEqual([p["id"] for p in path], sorted(LESSONS))
        self.assertEqual([p["id"] for p in path if p["current"]], [1])  # this app starts at 1
        self.assertEqual([p["stars"] for p in path], [0] * len(LESSONS))

    def test_clean_pass_gets_three_stars(self):
        self.check()
        p = self.stars(1)
        self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"], p["stars"]),
                         (True, True, True, 3))
        self.assertTrue(self.stars(2)["current"])

    def test_mistake_with_quiz_right_still_three(self):
        self.build("led_backwards")
        self.check()
        self.quiz(right=True)
        self.assertEqual(self.stars(1)["stars"], 0)  # not passed yet
        self.build("correct")
        self.check("I fixed it, check again")
        self.assertEqual(self.stars(1)["stars"], 3)

    def test_quiz_wrong_or_not_answered_loses_the_third_star(self):
        for answer in (False, None):
            with self.subTest(answer=answer):
                self.setUp()
                self.build("led_backwards")
                self.check()
                if answer is not None:
                    self.quiz(right=answer)
                self.build("correct")
                self.check("I fixed it, check again")
                p = self.stars(1)
                self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"]),
                                 (True, True, False))
                self.assertEqual(p["stars"], 2)
                self.tearDown()

    def test_same_mistake_twice_loses_the_second_star(self):
        for _ in range(2):
            self.build("led_backwards")
            self.check()
            self.quiz(right=True)
        self.build("correct")
        self.check("I fixed it, check again")
        # Twice the same mistake: the lesson is practised again, but it was
        # passed, so it keeps star 1, and star 3 for the right quizzes.
        p = self.stars(1)
        self.assertEqual((p["passed"], p["no_repeat"], p["quiz_right"], p["stars"]),
                         (True, False, True, 2))

    def test_stars_come_from_the_saved_history(self):
        self.check()
        again = App(FakeBoard(1, "correct", seed=3), lesson=1,
                    history_path=Path(self.tmp.name) / "h.json", model=ScriptedTutorModel())
        self.assertEqual(next(p for p in again.state()["path"] if p["id"] == 1)["stars"], 3)


class TestZipsMood(Case):
    def test_starts_happy(self):
        self.assertEqual(self.app.state()["mood"], "happy")

    def test_mistake_is_curious_not_scary(self):
        self.build("led_backwards")
        self.check()
        self.assertEqual(self.latest()["mood"], "curious")
        self.assertEqual(self.app.state()["mood"], "curious")
        self.assertFalse(self.latest()["celebrate"])

    def test_danger_is_careful(self):
        self.build("no_resistor")
        self.check()
        self.assertEqual(self.latest()["mood"], "careful")
        self.assertEqual(self.app.state()["mood"], "careful")

    def test_unknown_mistake_and_detective_are_curious(self):
        self.build("resistor_150")
        out = self.check()
        self.assertIn("task", out["tools"])
        self.assertEqual(self.latest()["mood"], "curious")

    def test_correct_is_happy_and_celebrates_once(self):
        self.check()
        self.assertEqual(self.latest()["mood"], "happy")
        self.assertTrue(self.latest()["celebrate"])
        self.check("I need a hint")  # a hint never measures: nothing to celebrate
        self.assertFalse(self.latest()["celebrate"])

    def test_hint_button_is_curious(self):
        self.board.send("PRESS 2")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertEqual(self.latest()["mood"], "curious")

    def test_power_off_is_careful(self):
        self.board.send("PRESS 4")
        self.app.pump_events()
        self.app.wait_for_buttons()
        self.assertEqual(self.latest()["mood"], "careful")
        self.assertEqual(self.app.state()["mood"], "careful")

    def test_thinking_while_the_tutor_works(self):
        with self.app.agent_lock:
            self.assertEqual(self.app.mood(self.board.read(), True), "thinking")

    def test_only_the_four_moods(self):
        for scenario in ("correct", "led_backwards", "no_resistor", "resistor_150",
                         "open_circuit"):
            self.build(scenario)
            self.check()
            self.assertIn(self.app.state()["mood"], App.MOODS)
        self.assertEqual(App.MOODS, ("happy", "thinking", "curious", "careful"))


class TestNoPowerSwitchMood(Case):
    BOARD = {"has_power_switch": False}

    def test_switch_off_by_hand_is_careful(self):
        self.build("no_resistor")
        self.check()
        self.assertEqual(self.latest()["mood"], "careful")


class TestPageFile(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_zip_has_four_moods(self):
        self.assertIn('id="guide"', self.html)
        for mood in App.MOODS:
            self.assertIn(f"mood-{mood}", self.html)
            self.assertIn(f'[data-mood="{mood}"]', self.html)

    def test_drawn_breadboard_and_path(self):
        for needed in ['id="bb"', "function buildBreadboard", "L.layout", 'id="path"',
                       "state.path", "@keyframes glow"]:
            self.assertIn(needed, self.html)

    def test_teacher_view_is_closed_by_default(self):
        self.assertRegex(self.html, r'<aside[^>]*id="teacher"[^>]*\bhidden\b')
        tag = re.search(r'<aside[^>]*id="teacher".*?</aside>', self.html, re.S).group(0)
        for inside in ['id="sim"', 'id="history"', 'id="tutor-did"', 'id="boardline"',
                       'id="reset"', "Mistake history"]:
            self.assertIn(inside, tag)

    def test_celebration_is_short_and_silent(self):
        self.assertIn("function celebrate()", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertNotRegex(self.html, r"<audio|new Audio|AudioContext")

    def test_one_file_no_internet(self):
        self.assertNotRegex(self.html, r'<script[^>]+src=|<link[^>]+href=|@import|url\(\s*["\']?http')
        # Only the SVG namespace name (a label, not a download) and localhost.
        self.assertNotRegex(self.html, r"https?://(?!localhost|www\.w3\.org/2000/svg)")

    def test_no_other_brand(self):
        self.assertNotRegex(self.html.lower(), r"scratch|sprite")


# --- The design rules (they must hold in the page file itself) ------------------

PALETTE = {  # name in the page -> the colour the design rules give it
    "blue": "#2F5FE8", "yellow": "#FFC93C", "green": "#1FAA6B", "coral": "#E5484D",
    "sky": "#4DB5FF", "ink": "#1D2742", "ink-2": "#5B6785", "white": "#FFFFFF",
    "page": "#EEF3FA", "line": "#DCE4F0",
}
SHADOW = "0 2px 8px rgba(29,39,66,0.08)"
# Real part colours, only for drawing the parts (resistor body and the
# colour-code bands that the palette does not already have).
PART_COLOURS = {"part-tan": "#E3C08F", "band-brown": "#8B5A2B", "band-orange": "#F28C28",
                "band-violet": "#6B47B8", "band-grey": "#8C95A6"}


def luminance(hex_colour):
    """Relative luminance, as WCAG defines it."""
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TestDesignRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.css = re.search(r"<style>(.*?)</style>", cls.html, re.S).group(1)
        cls.css_code = re.sub(r"/\*.*?\*/", " ", cls.css, flags=re.S)
        cls.tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", cls.css))
        # Every rule: (selector, declarations)
        cls.rules = re.findall(r"([^{}]+)\{([^{}]*)\}", cls.css_code)

    def values(self, prop):
        """Every value given to a CSS property, like "font-size"."""
        return re.findall(rf"(?<![\w-]){prop}\s*:\s*([^;}}]+)", self.css_code)

    def test_palette_is_exactly_the_five_colours_and_neutrals(self):
        for name, colour in PALETTE.items():
            self.assertEqual(self.tokens.get(name, "").upper(), colour, name)
        self.assertIn(f"--shadow: {SHADOW}", self.css)

    def test_no_colour_outside_the_palette(self):
        allowed = {c.upper() for c in PALETTE.values()} | {c.upper() for c in PART_COLOURS.values()}
        used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", self.html)}
        self.assertEqual(used - allowed, set())
        self.assertNotRegex(self.html, r"#[0-9A-Fa-f]{3}\b(?![0-9A-Fa-f])")  # no short forms
        rgb_allowed = {tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in allowed}
        for r, g, b in re.findall(r"rgba\((\d+),\s*(\d+),\s*(\d+),", self.html):
            self.assertIn((int(r), int(g), int(b)), rgb_allowed)
        self.assertNotIn("855CD6", self.html.upper())  # never Scratch's purple

    def test_part_colours_only_in_the_drawing(self):
        for name, colour in PART_COLOURS.items():
            self.assertEqual(self.tokens.get(name, "").upper(), colour, name)
        for selector, body in self.rules:
            if any(f"var(--{name})" in body for name in PART_COLOURS):
                for one in selector.split(","):
                    self.assertRegex(one.strip(), r"^\.(bb-|band-)", selector)

    def test_parts_in_their_real_colours(self):
        css = self.css_code
        self.assertRegex(css, r"\.bb-wire\.red \{ stroke: var\(--coral\); \}")      # #E5484D
        self.assertRegex(css, r"\.bb-wire\.black \{ stroke: var\(--ink\); \}")     # #1D2742
        self.assertRegex(css, r"\.bb-body\.resistor \{ fill: var\(--part-tan\); \}")
        self.assertRegex(css, r"\.bb-led \{ fill: var\(--coral\);")
        # The resistor colour code: 0 black, 1 brown, 2 red, 3 orange.
        for digit, token in (("0", "ink"), ("1", "band-brown"), ("2", "coral"), ("3", "band-orange")):
            self.assertRegex(css, rf"\.band-{digit} \{{ fill: var\(--{token}\); \}}")
        self.assertIn("function resistorBands(part)", self.html)
        self.assertIn('"band-" + digit', self.html)

    def test_resistor_bands_match_the_value(self):
        """Run the page's resistorBands with the same arithmetic in Python."""
        body = re.search(r"function resistorBands\(part\) \{(.*?)\n  \}", self.html, re.S).group(1)
        self.assertIn("Math.log10(ohms)", body)

        def bands(part):  # the same steps as the page
            m = re.search(r"(\d+(?:\.\d+)?)\s*([kM]?)", part)
            ohms = float(m.group(1)) * {"k": 1e3, "M": 1e6}.get(m.group(2), 1)
            import math
            exp = math.floor(math.log10(ohms) + 1e-9)
            return [math.floor(ohms / 10 ** exp + 1e-9),
                    math.floor(ohms / 10 ** (exp - 1) + 1e-9) % 10, exp - 1]
        self.assertEqual(bands("220 ohm resistor"), [2, 2, 1])    # red red brown
        self.assertEqual(bands("Top 10k resistor"), [1, 0, 3])    # brown black orange
        for lesson in LESSONS.values():
            for part in lesson["layout"]:
                if "resistor" in part["part"].lower():
                    self.assertEqual(len(bands(part["part"])), 3, part["part"])

    def test_every_text_pair_has_4_5_to_1_contrast(self):
        listed = re.search(r"contrast-pairs:([^*]*)\*/", self.css).group(1)
        pairs = [p.strip().split("/") for p in listed.replace("\n", " ").split(",") if p.strip()]
        self.assertGreater(len(pairs), 5)
        for text, back in pairs:
            ratio = contrast(self.tokens[text], self.tokens[back])
            self.assertGreaterEqual(ratio, 4.5, f"{text} on {back}: {ratio:.2f}")
        # Every rule that sets both a text colour and a background is listed.
        listed_pairs = {(t, b) for t, b in pairs}
        for selector, body in self.rules:
            fg = re.search(r"(?<![\w-])color:\s*var\(--([\w-]+)\)", body)
            bg = re.search(r"background:\s*var\(--([\w-]+)\)", body)
            if fg and bg:
                self.assertIn((fg.group(1), bg.group(1)), listed_pairs, selector.strip())

    def test_type_sizes_and_weights(self):
        sizes = {v.strip() for v in self.values("font-size")}
        self.assertEqual(sizes - {"16px", "20px", "28px", "36px"}, set())
        weights = {v.strip() for v in self.values("font-weight")}
        self.assertEqual(weights - {"400", "700"}, set())
        for size in re.findall(r'font-size="(\d+)"', self.html):
            self.assertIn(size, {"16", "20", "28", "36"})
        for weight in re.findall(r'font-weight="(\d+)"', self.html):
            self.assertIn(weight, {"400", "700"})
        self.assertIn('ui-rounded, "SF Pro Rounded", "Segoe UI", system-ui, sans-serif', self.css)

    def test_monospace_only_on_the_board_screen(self):
        for selector, body in self.rules:
            if "monospace" in body:
                self.assertEqual(selector.strip(), ".screen")

    def test_spacing_is_on_the_8px_grid(self):
        grid = {0, 8, 16, 24, 32, 48}
        for prop in ("margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
                     "padding", "padding-top", "padding-bottom", "padding-left",
                     "padding-right", "gap", "row-gap", "column-gap"):
            for value in self.values(prop):
                for number in re.findall(r"(-?\d+(?:\.\d+)?)px", value):
                    self.assertIn(float(number), grid, f"{prop}: {value}")
        self.assertRegex(self.css_code, r"\.zones \{[^}]*gap: 24px")

    def test_one_shadow_and_thin_lines(self):
        for value in self.values("box-shadow"):
            self.assertEqual(value.strip(), "var(--shadow)")
        for prop in ("border", "border-top", "border-bottom", "border-left", "border-right"):
            for value in self.values(prop):
                self.assertIn(value.strip(), {"0", "none", "1px solid var(--line)"}, prop)

    def test_buttons_are_big_pills(self):
        self.assertRegex(self.css_code, r"\.btn \{[^}]*min-height: 56px[^}]*border-radius: 999px")
        self.assertRegex(self.css_code, r"\.opt \{[^}]*min-height: 56px")
        self.assertRegex(self.css_code, r"\.btn:active \{[^}]*transform")
        for button in ("check", "fixed"):
            tag = re.search(rf'<button[^>]*id="{button}".*?</button>', self.html, re.S).group(0)
            self.assertIn("<svg", tag)  # a small icon on the main buttons
        self.assertIn('class="btn btn-blue" type="button" id="check"', self.html)
        self.assertIn('class="btn btn-yellow" type="button" id="fixed"', self.html)

    def test_three_zones_fill_the_screen(self):
        self.assertIn("grid-template-rows: 72px minmax(0, 1fr)", self.css)
        self.assertIn("grid-template-columns: minmax(0, 62fr) minmax(0, 38fr)", self.css)
        self.assertRegex(self.css_code, r"\.zone \{[^}]*background: var\(--white\)"
                                        r"[^}]*border-radius: 16px")
        self.assertRegex(self.css_code, r"\.topbar \{[^}]*background: var\(--blue\)")
        self.assertIn("The plan: where each part goes", self.html)
        toggle = re.search(r'<button[^>]*id="teacher-toggle".*?</button>', self.html, re.S).group(0)
        self.assertIn(">Teacher<", toggle)

    def test_phone_stacks_top_bar_zip_stage(self):
        phone = re.search(r"@media \(max-width: 999px\) \{(.*?)\n  \}", self.css, re.S).group(1)
        self.assertIn(".zip { grid-column: 1; grid-row: 1; }", phone)
        self.assertIn(".stage { grid-column: 1; grid-row: 2; }", phone)

    def test_mini_lesson_opens_in_the_zip_panel_never_over_things(self):
        stage = re.search(r'<section class="zone stage".*?</section>', self.html, re.S).group(0)
        zip_panel = re.search(r'<section class="zone zip".*?</section>', self.html, re.S).group(0)
        self.assertNotIn('id="mini"', stage)
        for needed in ('id="mini"', 'id="mini-close"', 'id="mini-open"', "Learn why:"):
            self.assertIn(needed, zip_panel)
        # The card comes right after Zip's picture and before the bubble; the
        # "Learn why" tab comes after the bubble.
        order = [zip_panel.index(x) for x in ('class="zip-top"', 'id="mini"', 'id="bubble"',
                                               'id="mini-open"')]
        self.assertEqual(order, sorted(order))
        mini = re.search(r"\n  \.mini \{([^}]*)\}", self.css_code).group(1)
        self.assertNotRegex(mini, r"position:\s*(absolute|fixed)")
        self.assertNotRegex(self.css_code, r"\.mini \{[^}]*position:\s*(absolute|fixed)")
        self.assertIn(".zip.learning .zip-top { display: none; }", self.css_code)
        self.assertRegex(self.css_code, r"\.mini-head \{[^}]*background: var\(--yellow\)")
        # The tab has a yellow book icon.
        tab = re.search(r'<button[^>]*id="mini-open".*?</button>', self.html, re.S).group(0)
        self.assertIn('fill="#FFC93C"', tab)
        # Phone: below the bubble, in the normal flow.
        phone = re.search(r"@media \(max-width: 999px\) \{(.*?)\n  \}", self.css, re.S).group(1)
        self.assertIn(".mini { order: 3;", phone)
        self.assertIn(".bubble { order: 1; }", phone)

    def test_zip_words_get_the_space_no_fade(self):
        self.assertNotIn("mask-image", self.css_code)
        self.assertNotIn("fadeIfMore", self.html)
        hint = re.search(r"\n  \.hint \{([^}]*)\}", self.css_code).group(1)
        self.assertNotIn("max-height", hint)
        self.assertIn("overflow-y: auto", hint)          # scrolls only if still too long
        self.assertIn("font-size: 28px", hint)
        self.assertIn("clamp(64px, 15vh, 160px)", self.css_code)  # Zip about half size

    def test_phone_pills_wrap(self):
        phone = re.search(r"@media \(max-width: 999px\) \{(.*?)\n  \}", self.css, re.S).group(1)
        self.assertRegex(phone, r"\.pill \{[^}]*white-space: normal; flex-wrap: wrap; max-width: 100%")

    def test_phone_lesson_path_stays_inside_the_screen(self):
        """At 390 px (a phone) the lesson path must not make the page scroll
        sideways: it is held inside the screen, and every lesson fits."""
        code = self.css_code
        narrow = re.search(r"@media \(max-width: 999px\) \{(.*?)\n  \}", code, re.S).group(1)
        phone = re.search(r"@media \(max-width: 599px\) \{(.*?)\n  \}", code, re.S).group(1)
        path_rule = re.search(r"\.path \{([^}]*)\}", narrow)
        self.assertIsNotNone(path_rule, "the phone CSS needs its own .path rule")
        path_rule = path_rule.group(1)
        for needed in ("min-width: 0", "max-width: 100%", "overflow-x: auto"):
            self.assertIn(needed, path_rule)   # never wider than the top bar

        def px(pattern, text):
            found = re.search(pattern + r"(\d+)(?:px)?\b", text)  # "0" or "8px"
            self.assertIsNotNone(found, pattern)
            return int(found.group(1))

        star = px(r"\.stop \.stars svg \{ width: ", code)
        dot = px(r"\.stop \.dot \{[^}]*width: ", code)
        stop = max(3 * star, dot)                      # 3 stars under each lesson dot
        road = px(r"\.road \{ width: ", phone)
        gap = px(r"\.path \{ gap: ", phone)
        pad = px(r"padding: ", path_rule)
        bar_pad = px(r"\.topbar \{[^}]*padding: ", narrow)
        n = len(LESSONS)
        width = n * stop + (n - 1) * road + 2 * (n - 1) * gap + 2 * pad
        self.assertLessEqual(width, 390 - 2 * bar_pad,
                             f"{n} lessons need {width} px on a 390 px phone")

    def test_board_fills_the_stage_and_stars_are_big(self):
        self.assertIn("const oy = 0;", self.html)
        self.assertIn('height: Math.max(H, bottom), rx: 16, class: "bb-board"', self.html)
        self.assertIn(".stop .stars svg { width: 20px; height: 20px; }", self.css_code)

    def test_teacher_drawer_slides_over_the_zip_panel(self):
        self.assertRegex(self.css_code, r"\.zip \{ grid-column: 2; grid-row: 1; \}")
        self.assertRegex(self.css_code, r"\.teacher \{[^}]*grid-column: 2; grid-row: 1;[^}]*"
                                        r"transform: translateX")
        drawer = re.search(r'<aside[^>]*id="teacher".*?</aside>', self.html, re.S).group(0)
        self.assertIn("/api/notebook", drawer)

    def test_red_never_shakes(self):
        self.assertNotRegex(self.css_code, r"shake|translateX\(-?\d+px\)\s*;?\s*\}?\s*"
                                           r"(?:\d+%|to)\s*\{\s*transform: translateX")


class TestTalksToYou(Case):
    """Every sentence a student sees says "you", never "the student"."""

    def replies(self):
        return [m["text"] for m in self.app.log if m["who"] == "tutor"]

    def test_next_lesson_reason_after_a_pass(self):
        self.assertIn("This is the next lesson you have not passed yet.", self.check()["reply"])

    def test_next_lesson_reason_after_the_same_mistake_twice(self):
        for _ in range(2):  # the same mistake twice: practise the lesson again
            self.build("led_backwards")
            self.check()
        self.build("correct")
        out = self.check("I fixed it, check again")
        self.assertIn("practice this lesson once more", out["reply"])
        self.assertIn("you made the same mistake", out["reply"])

    def test_next_lesson_reason_when_everything_is_passed(self):
        self.check()                    # lesson 1, no mistakes
        self.check()                    # lesson 2, no mistakes
        self.check()                    # lesson 3, no mistakes
        self.app.hold(True)             # lesson 4: hold the button, check,
        self.check()
        self.app.hold(False)            # then let go and check again
        out = self.check()
        # Started at lesson 1: lesson 0 was skipped, so it comes now.
        self.assertIn("You skipped lesson 0", out["reply"])
        self.assertNotIn("You passed every lesson", out["reply"])
        self.app.quiz("intro", LESSONS[0]["intro_lesson"]["answer"])
        out = self.check()              # lesson 0, its question answered
        self.assertIn("You passed every lesson", out["reply"])

    def test_no_reply_talks_about_the_student(self):
        for scenario in ("correct", "led_backwards", "no_resistor", "resistor_150",
                         "open_circuit", "correct"):
            self.build(scenario)
            self.check("I fixed it, check again")
        self.board.send("PRESS 2")
        self.app.pump_events()
        self.app.wait_for_buttons()
        for text in self.replies():
            self.assertNotRegex(text.lower(), r"\bthe student\b", text)

    def test_session_reason_strings(self):
        source = (ROOT / "agent" / "session.py").read_text(encoding="utf-8")
        for reason in re.findall(r'_pick\([^,]+,\s*f?"([^"]+)"', source):
            self.assertNotIn("the student", reason.lower())


if __name__ == "__main__":
    unittest.main()
