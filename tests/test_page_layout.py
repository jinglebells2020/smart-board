"""Every wide window from 1280x560 up is readable: the breadboard keeps
at least 300 px of height, Zip's words show at least 4 lines, and
nothing overlaps. Short windows scroll instead of squeezing.

The browser check needs Node.js and Playwright (the cloud test machine
has them); without them it is skipped. The CSS checks always run.
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from agent.mock_model import ScriptedTutorModel
from sim.fake_board import FakeBoard
from web.server import App, make_server

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = HTML.split("<style>", 1)[1].split("</style>", 1)[0]
SIZES = ["1280x560", "1280x585", "1366x768", "1920x1080",  # the ones asked for
         "1024x700", "1280x720", "1440x900", "1600x899", "1100x999", "1280x1024"]


def short_screen_rules():
    m = re.search(r"@media \(min-width: 1000px\) and \(max-height: 899px\),\s*"
                  r"\(min-width: 1000px\) and \(max-width: 1279px\) and \(max-height: 999px\) \{"
                  r"(.*?)\n  \}", CSS, re.S)
    return m.group(1) if m else ""


class TestShortScreenCss(unittest.TestCase):
    def test_short_wide_windows_scroll_instead_of_squeezing(self):
        rules = short_screen_rules()
        self.assertTrue(rules, "no rules for short wide windows")
        self.assertIn(".app { height: auto; min-height: 100vh;", rules)
        self.assertIn("overflow: visible", rules)
        self.assertIn(".drawing { flex: none; height: max(300px,", rules)
        self.assertIn("min-height: calc(4 * 1.3 * 28px + 34px)", rules)  # 4 lines of 28px words

    def test_big_screens_still_fill_the_screen(self):
        self.assertIn("grid-template-rows: 72px minmax(0, 1fr)", CSS)


def browser_ready():
    if shutil.which("node") is None:
        return False
    probe = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True,
                           cwd=ROOT, timeout=60)
    return probe.returncode == 0


@unittest.skipUnless(browser_ready(), "needs Node.js and Playwright")
class TestInARealBrowser(unittest.TestCase):
    def test_every_size_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App(FakeBoard(1, "led_backwards", seed=1), lesson=1,
                      history_path=Path(tmp) / "h.json", model=ScriptedTutorModel())
            server = make_server(app, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                out = subprocess.run(
                    ["node", "tests/browser/layout_check.js",
                     f"http://127.0.0.1:{server.server_address[1]}", *SIZES],
                    capture_output=True, text=True, cwd=ROOT, timeout=180)
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(out.returncode, 0, out.stderr)
        results = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        self.assertEqual([r["size"] for r in results], SIZES)
        for r in results:
            with self.subTest(size=r["size"]):
                self.assertGreaterEqual(r["drawing"], 300)
                self.assertGreaterEqual(r["lines"], 4)
                self.assertEqual(r["overlaps"], [])
                self.assertFalse(r["sideways"])


if __name__ == "__main__":
    unittest.main()
