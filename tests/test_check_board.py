"""tools/check_board.py: the firmware checker, tested on good and broken
pretend boards (no hardware)."""

import contextlib
import io
import json
import unittest

from sim.fake_board import FakeBoard
from sim.fake_port import FakeSerialPort
from tools import check_board as CB


def run(board=None, port=None, buttons=False, power_cut=False, **kw):
    board = board if board is not None else FakeBoard(rows="T1-T8,PT+,PT-", seed=1)
    port = port if port is not None else FakeSerialPort(board, period=0)
    lines = []
    checker = CB.Checker(port, out=lines.append, answer_wait=0.15, hello_wait=0.2,
                         event_wait=0.5, **kw)

    def ask(what, n):
        board.send(f"PRESS {n}" if what == "button" else "SCENARIO 1 no_resistor")
    ok = checker.run(buttons_ask=ask if buttons else None, power_cut_ask=ask if power_cut else None)
    return ok, checker.results, "\n".join(lines)


def failed(results):
    return [text for status, text, _ in results if status == CB.FAIL]


class TestGoodBoards(unittest.TestCase):
    def test_full_board_passes_everything(self):
        ok, results, text = run(buttons=True, power_cut=True)
        self.assertTrue(ok, failed(results))
        self.assertEqual(failed(results), [])
        self.assertIn("RESULT: PASS", text)
        self.assertGreater(len(results), 40)
        self.assertNotIn(CB.SKIP, [r[0] for r in results])

    def test_smallest_board_passes(self):
        board = FakeBoard(rows="T1-T4,PT+,PT-", has_current=False, has_power_switch=False,
                          has_screen=False, seed=1)
        ok, results, text = run(board)
        self.assertTrue(ok, failed(results))
        self.assertIn('PASS  "current_ma" is null', text)
        self.assertIn('CUT gives the error "no power switch"', text)
        self.assertIn('SHOW gives the error "no screen"', text)

    def test_board_without_hello_at_start_but_answers_HELLO(self):
        port = FakeSerialPort(FakeBoard(seed=1), send_hello=False, period=0)
        ok, results, text = run(port=port, board=port.board)
        self.assertTrue(ok, failed(results))
        self.assertIn("SKIP  No hello line when the laptop connected.", text)

    def test_buttons_and_cut_are_skipped_unless_asked(self):
        ok, results, text = run()
        self.assertTrue(ok)
        self.assertIn("SKIP  Buttons not tested.", text)
        self.assertIn("SKIP  Power cut not tested.", text)

    def test_command_line_on_the_fake_board(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = CB.main(["--fake", "--buttons", "--power-cut", "--count", "5"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("RESULT: PASS", out.getvalue())

    def test_command_line_without_a_port(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CB.main([]), 2)


# --- Broken boards ----------------------------------------------------------

class ZeroCurrent(FakeBoard):
    """No current sensor, but sends 0 instead of null."""
    def read(self):
        return dict(super().read(), current_ma=0)


class MissingRow(FakeBoard):
    def read(self):
        r = super().read()
        r["rows"].pop("T2", None)
        return r


class TimeGoesBack(FakeBoard):
    def read(self):
        r = super().read()
        r["t_ms"] = 1000 - r["t_ms"] % 1000
        return r


class AckForEverything(FakeBoard):
    """Says ack to every command, even ones it must refuse."""
    def send(self, line):
        answer = super().send(line)
        if answer["type"] == "error":
            return {"type": "ack", "cmd": line.split()[0] if line.split() else ""}
        return answer


class NoCmdField(FakeBoard):
    def send(self, line):
        answer = dict(super().send(line))
        answer.pop("cmd", None)
        return answer


class LongScreen(FakeBoard):
    """Accepts any SHOW, even text that does not fit."""
    def send(self, line):
        if line.upper().startswith("SHOW"):
            return {"type": "ack", "cmd": "SHOW"}
        return super().send(line)


class TwoAnswers(FakeSerialPort):
    """Every command is answered twice."""
    def write(self, data):
        n = super().write(data)
        for line in bytes(data).decode().splitlines():
            if line.strip() and line.strip().upper() != "HELLO":
                self._line({"type": "ack", "cmd": line.strip()})
        return n


class AnswersEmptyLines(FakeSerialPort):
    """Answers an empty line (the laptop's keep-alive) with an error."""
    def write(self, data):
        n = super().write(data)
        if not bytes(data).decode().strip():
            self._line({"type": "error", "cmd": "", "msg": "unknown command: "})
        return n


class TestBrokenBoards(unittest.TestCase):
    def test_an_answer_to_an_empty_line_is_only_a_note(self):
        board = FakeBoard(rows="T1-T4", seed=1)
        # Only a note (SKIP), never a FAIL: Genesis ignores that answer.
        ok, results, text = run(board=board, port=AnswersEmptyLines(board, period=0))
        self.assertTrue(ok, failed(results))
        self.assertIn("RESULT: PASS", text)
        self.assertIn("SKIP  An empty line (the laptop's keep-alive) got an answer: Genesis "
                      "ignores it.", text)
        self.assertIn("Boards should ignore empty lines", text)

    def assertFailsOn(self, words, **kw):
        ok, results, text = run(**kw)
        self.assertFalse(ok)
        self.assertIn("RESULT: FAIL", text)
        fails = failed(results)
        self.assertTrue(any(words in f for f in fails), f"{words!r} not in {fails}")
        return fails, text

    def test_current_must_be_null_without_a_sensor(self):
        self.assertFailsOn('"current_ma" is null',
                           board=ZeroCurrent(has_current=False, rows="T1-T4", seed=1))

    def test_every_hello_row_in_every_reading(self):
        fails, text = self.assertFailsOn('"rows" has exactly the names',
                                         board=MissingRow(rows="T1-T4", seed=1))
        self.assertIn("missing: ['T2']", text)

    def test_time_must_go_up(self):
        self.assertFailsOn('"t_ms" goes up', board=TimeGoesBack(rows="T1-T4", seed=1))

    def test_junk_lines(self):
        board = FakeBoard(rows="T1-T4", seed=1)
        port = FakeSerialPort(board, period=0)
        port.junk = [b"Ready!", b"debug: adc=512"]
        port.read(10**6)              # the hello line: junk comes with the readings
        port._out = bytearray(json.dumps(board.hello()).encode() + b"\n")
        fails, text = self.assertFailsOn("no junk lines", board=board, port=port)
        self.assertIn("'Ready!'", text)

    def test_refusals_must_be_errors(self):
        fails, _ = self.assertFailsOn("LED with a row that does not exist",
                                      board=AckForEverything(has_power_switch=False, seed=1))
        self.assertTrue(any("FLY AWAY" in f for f in fails))
        self.assertTrue(any("no power switch" in f for f in fails))

    def test_answers_need_cmd(self):
        self.assertFailsOn("LED CLEAR is answered with ack", board=NoCmdField(seed=1))

    def test_screen_text_that_does_not_fit(self):
        self.assertFailsOn("SHOW with 5 lines gives an error", board=LongScreen(seed=1))

    def test_each_command_exactly_one_answer(self):
        board = FakeBoard(rows="T1-T4", seed=1)
        self.assertFailsOn("No extra answer lines", board=board, port=TwoAnswers(board, period=0))

    def test_board_that_never_says_hello(self):
        board = FakeBoard(seed=1)
        port = FakeSerialPort(board, send_hello=False, hello_on_request=False, period=0)
        fails, text = self.assertFailsOn("answers the HELLO command", board=board, port=port)
        self.assertEqual(len(fails), 1)  # stops there: nothing else can be checked
        self.assertIn("baud", text)

    def test_power_cut_that_never_comes(self):
        board = FakeBoard(seed=1, firmware_cut_ma=None)
        board.firmware_cut_ma = None
        self.assertFailsOn('sends a "power_cut" line', board=board, power_cut=True)


if __name__ == "__main__":
    unittest.main()
