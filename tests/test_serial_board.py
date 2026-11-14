"""SerialBoard (a real board on a USB serial port), tested with no hardware.

sim/fake_port.py is a pretend serial port with the fake board on the
other end. It can send junk, forget its hello line, be unplugged and
plugged back in, and go silent.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent.serial_board as SB
from agent.boardlink import BoardLink
from agent.build import ask, build_agent
from agent.mock_model import ScriptedTutorModel
from agent.serial_board import BoardError, SerialBoard, choose_port
from agent.session import Session
from sim.fake_board import FakeBoard
from sim.fake_port import FakeSerialPort
from web.server import App


def connect(port, **kw):
    """A SerialBoard on a pretend port, with short waits so tests are fast."""
    opts = dict(opener=port.open, hello_wait=0.2, read_wait=0.3, answer_wait=0.3,
                retry_every=0.0)
    opts.update(kw)
    return SerialBoard("COM9", **opts)


class TestConnecting(unittest.TestCase):
    def test_hello_read_send_events(self):
        port = FakeSerialPort(FakeBoard(rows="T1-T16", seed=1))
        board = connect(port)
        self.assertEqual(board.hello()["protocol"], 2)
        self.assertEqual(len(board.hello()["rows"]), 16)
        r = board.read()
        self.assertEqual(r["type"], "reading")
        self.assertEqual(set(r["rows"]), set(board.hello()["rows"]))
        self.assertEqual(board.send("LED T10 red")["type"], "ack")
        self.assertEqual(port.board.lights, {"T10": "red"})
        self.assertEqual(board.send("LED T99 red")["type"], "error")
        port.board.send("PRESS 2")
        self.assertEqual(board.events(), [{"type": "button", "id": 2}])
        self.assertEqual(board.events(), [])
        self.assertTrue(board.status()["connected"])
        self.assertIn("Board connected on COM9 (115200 baud)", board.status()["text"])

    def test_read_gives_the_newest_reading(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port)
        first = board.read()
        second = board.read()
        self.assertGreater(second["t_ms"], first["t_ms"])

    def test_junk_lines_are_skipped_and_counted(self):
        junk = [b"Booting...", b'{"half": ', b"\xff\xfe garbage", b"[1, 2, 3]",
                b'{"type": "mystery"}', b'{"type": "hello", "protocol": 1}']
        port = FakeSerialPort(FakeBoard(seed=1), junk=junk)
        board = connect(port)
        self.assertEqual(board.read()["type"], "reading")
        self.assertEqual(board.junk_lines, len(junk))
        self.assertEqual(board.status()["junk_lines"], len(junk))
        self.assertEqual(board.hello()["protocol"], 2)  # the old hello did not replace it

    def test_missing_hello_sends_HELLO(self):
        port = FakeSerialPort(FakeBoard(seed=1), send_hello=False)
        board = connect(port)
        self.assertIn("HELLO", port.written)
        self.assertEqual(board.hello()["protocol"], 2)
        self.assertEqual(board.read()["type"], "reading")

    def test_no_hello_ever_is_a_plain_error(self):
        port = FakeSerialPort(FakeBoard(seed=1), send_hello=False, hello_on_request=False)
        with self.assertRaises(BoardError) as e:
            connect(port, answer_wait=0.05)
        msg = str(e.exception)
        self.assertIn("did not send a hello line", msg)
        self.assertIn("115200 baud", msg)
        self.assertIn("FIRMWARE_GUIDE", msg)
        self.assertEqual(port.written.count("HELLO"), 3)

    def test_port_that_cannot_open(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        port.unplug()
        with self.assertRaises(BoardError) as e:
            connect(port)
        self.assertIn("Cannot open COM9", str(e.exception))
        self.assertIn("Serial Monitor", str(e.exception))


class TestCableAndSilence(unittest.TestCase):
    def test_unplug_and_plug_back_in(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port)
        board.read()
        port.unplug()
        with self.assertRaises(BoardError) as e:
            board.read()
        self.assertIn("USB cable", str(e.exception))
        self.assertFalse(board.status()["connected"])
        self.assertIn("Board not answering on COM9", board.status()["text"])
        self.assertEqual(board.events(), [])  # never raises
        with self.assertRaises(BoardError):
            board.send("LED T1 red")
        with self.assertRaises(BoardError):  # still unplugged: still a plain error
            board.read()
        port.plug_in()
        self.assertEqual(board.read()["type"], "reading")
        self.assertTrue(board.status()["connected"])
        self.assertEqual(board.send("LED T1 green")["type"], "ack")

    def test_reconnect_is_not_tried_more_than_about_once_a_second(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port, retry_every=60.0)
        port.unplug()
        with self.assertRaises(BoardError):
            board.read()
        port.plug_in()
        with self.assertRaises(BoardError):  # too soon to try again
            board.read()

    def test_silence_then_talking_again(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port)
        board.read()
        port.silent = True
        with self.assertRaises(BoardError) as e:
            board.read()
        self.assertIn("No readings from the board on COM9", str(e.exception))
        self.assertFalse(board.status()["connected"])
        port.silent = False
        self.assertEqual(board.read()["type"], "reading")
        self.assertTrue(board.status()["connected"])

    def test_command_with_no_answer(self):
        port = FakeSerialPort(FakeBoard(seed=1), answer_commands=False)
        board = connect(port, answer_wait=0.05)
        answer = board.send("LED T1 red")
        self.assertEqual(answer["type"], "error")
        self.assertIn("did not answer LED", answer["msg"])


class TestPowerCutOverSerial(unittest.TestCase):
    def test_power_cut_line_reaches_the_laptop(self):
        fake = FakeBoard(1, "no_resistor", seed=1)
        board = connect(FakeSerialPort(fake))
        link = BoardLink(board)
        for _ in range(3):
            link.read()
        self.assertIsNotNone(link.last_cut)
        self.assertEqual(link.last_cut["reason"], "overcurrent")
        self.assertFalse(fake.power_on)

    def test_session_explains_the_board_cut(self):
        fake = FakeBoard(1, "no_resistor", seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(connect(FakeSerialPort(fake)), lesson=1,
                        history_path=Path(tmp) / "h.json")
            summary, result, cut = s.measure()
            self.assertTrue(cut)
            self.assertIn("board_cut", result)
            self.assertIn("by itself", result["board_cut"]["say"])
            self.assertEqual(result["mistake_id"], "no_resistor")


class TestChoosePort(unittest.TestCase):
    def test_a_named_port_is_used_as_it_is(self):
        self.assertEqual(choose_port("COM3", ports=[]), "COM3")

    def test_auto_with_one_port(self):
        self.assertEqual(choose_port("auto", ports=[("COM5", "USB-SERIAL CH340")]), "COM5")

    def test_auto_with_no_ports(self):
        with self.assertRaises(BoardError) as e:
            choose_port("auto", ports=[])
        self.assertIn("No serial ports found", str(e.exception))

    def test_auto_with_several_lists_them(self):
        with self.assertRaises(BoardError) as e:
            choose_port("auto", ports=[("COM3", "Bluetooth"), ("COM7", "Arduino Uno")])
        msg = str(e.exception)
        self.assertIn("More than one", msg)
        self.assertIn("COM3", msg)
        self.assertIn("COM7  Arduino Uno", msg)
        self.assertIn("--serial", msg)

    def test_server_and_terminal_stop_with_a_plain_message(self):
        import agent.__main__ as cli
        import web.server as server
        for main in (server.main, cli.main):
            out = io.StringIO()
            with mock.patch.object(SB, "list_ports", return_value=[]), \
                    contextlib.redirect_stdout(out):
                code = main(["--serial", "auto"])
            self.assertEqual(code, 2)
            self.assertIn("No serial ports found", out.getvalue())


class TestPageWithARealBoard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = FakeSerialPort(FakeBoard(1, "correct", seed=2))
        self.app = App(connect(self.port), lesson=1,
                       history_path=Path(self.tmp.name) / "h.json", model=ScriptedTutorModel())

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_line_and_no_practice_buttons(self):
        st = self.app.state()
        self.assertEqual(st["board_status"]["kind"], "serial")
        self.assertTrue(st["reading_ok"])
        self.assertTrue(st["board_status"]["connected"])
        self.assertEqual(st["board_status"]["text"], "Board connected on COM9 (115200 baud)")
        self.assertFalse(st["sim"])               # no practice-mode buttons
        self.assertFalse(st["board"]["pretend"])  # no pretend board buttons
        self.assertEqual(st["scenarios"], [])
        self.assertIn("error", self.app.press(1))  # the page cannot press real buttons

    def test_unplugged_board_shows_not_answering(self):
        self.app.state()
        self.port.unplug()
        st = self.app.state()  # does not crash
        self.assertFalse(st["board_status"]["connected"])
        self.assertIn("Board not answering on COM9", st["board_status"]["text"])
        self.assertFalse(st["reading_ok"])  # the page shows "?" instead of numbers
        self.port.plug_in()
        self.assertTrue(self.app.state()["board_status"]["connected"])

    def test_fake_board_status(self):
        app = App(FakeBoard(seed=1), lesson=1, history_path=Path(self.tmp.name) / "f.json",
                  model=ScriptedTutorModel())
        self.assertEqual(app.state()["board_status"]["kind"], "fake")

    def test_real_button_press_reaches_the_tutor(self):
        self.app.state()
        self.port.board.send("PRESS 2")
        self.app.state()  # buttons arrive with the readings
        self.app.wait_for_buttons()
        self.assertTrue(any(e["who"] == "tutor" for e in self.app.log))


class TestTutorWithAnUnpluggedBoard(unittest.TestCase):
    def test_tools_say_so_in_plain_words(self):
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1))
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(connect(port), lesson=1, history_path=Path(tmp) / "h.json")
            agent = build_agent(s, ScriptedTutorModel())
            port.unplug()
            reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("not answering", reply)
        self.assertIn("USB cable", reply)

    def test_whole_lesson_over_serial(self):
        port = FakeSerialPort(FakeBoard(1, "led_backwards", seed=1))
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(connect(port), lesson=1, history_path=Path(tmp) / "h.json")
            agent = build_agent(s, ScriptedTutorModel())
            history = []
            reply, _ = ask(agent, history, "Check my circuit")
            self.assertEqual([m["mistake"] for m in s.history["mistakes"]], ["led_backwards"])
            self.assertIn("red", port.board.lights.values())  # the problem row lights red
            port.board.send("SCENARIO 1 correct")
            ask(agent, history, "I flipped the LED. Check my circuit")
            self.assertEqual(s.lesson_id, 2)  # passed lesson 1 over the serial port


if __name__ == "__main__":
    unittest.main()
