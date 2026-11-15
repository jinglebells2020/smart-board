"""SerialBoard (a real board on a USB serial port), tested with no hardware.

sim/fake_port.py is a pretend serial port with the fake board on the
other end. It can send junk, forget its hello line, be unplugged and
plugged back in, and go silent.
"""

import contextlib
import io
import json
import tempfile
import threading
import time
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


class TestBacklogKeepAliveAndConnect(unittest.TestCase):
    """Round 2: the newest reading after a long pause, the keep-alive, and a
    fresh start with the board's lights."""

    def test_read_takes_in_a_backlog_bigger_than_one_chunk(self):
        # A Linux tty hands over about 4 KB at a time, and between two chunks
        # it says "nothing waiting" for a moment while it refills. 20 readings
        # of every row (about 2 KB each) waited while nobody read. (A long
        # period: no new reading arrives during the test.)
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1), chunk=4095, period=60)
        board = connect(port)
        board.read()
        port.board.send("SCENARIO 1 open_circuit")  # the student changes the circuit...
        port.backlog(19)
        newest = port.board.read()  # ...and this is the board as it is now
        port._line(newest)
        self.assertGreater(len(port._out), 4 * 4095)
        r = board.read()
        self.assertEqual(r["t_ms"], newest["t_ms"])
        self.assertIn("T10", r["floating"])
        self.assertEqual(len(port._out), 0)  # nothing old is left to read later

    def test_after_a_backlog_read_waits_for_the_boards_next_line(self):
        # While the port was full the board could not send: after the old
        # lines, its next (fresh) one comes at its next reading, here 0.1 s
        # later, longer than the quiet time between chunks.
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1), period=60)
        board = connect(port)
        board.read()
        port.backlog(10)  # a second of old readings (the correct circuit)
        port.board.send("SCENARIO 1 open_circuit")  # the circuit now
        fresh = port.board.read()
        line = json.dumps(fresh).encode() + b"\n"
        threading.Timer(0.1, lambda: port._out.extend(line)).start()
        r = board.read()
        self.assertEqual(r["t_ms"], fresh["t_ms"])
        self.assertIn("T10", r["floating"])

    def test_the_fake_tty_pauses_between_chunks(self):
        # What the test above guards against: the port says 0 once after a
        # full chunk, then has the next one.
        port = FakeSerialPort(FakeBoard(seed=1), chunk=100, period=60)
        port.backlog(3)
        self.assertEqual(port.in_waiting, 100)
        self.assertEqual(len(port.read(100)), 100)
        self.assertEqual(port.in_waiting, 0)    # refilling
        self.assertEqual(port.in_waiting, 100)  # the next chunk is there

    def test_read_does_not_wait_when_nothing_arrived(self):
        port = FakeSerialPort(FakeBoard(seed=1), period=60)
        board = connect(port)
        board.read()
        pumps = []
        real = board._pump
        board._pump = lambda: pumps.append(1) or real()
        board._drain()  # nothing waiting: one look, no quiet time
        self.assertEqual(len(pumps), 1)

    def test_a_reading_that_arrives_in_two_parts_is_taken_whole(self):
        # An older reading is waiting, and the newest one's second half comes
        # a moment after its first (a new USB frame): read() waits for it
        # instead of returning the older one.
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1), period=60)
        board = connect(port)
        board.read()
        older, newer = port.board.read(), port.board.read()
        text = json.dumps(newer).encode() + b"\n"
        port._out += json.dumps(older).encode() + b"\n" + text[:500]
        threading.Timer(0.005, lambda: port._out.extend(text[500:])).start()
        r = board.read()
        self.assertGreater(newer["t_ms"], older["t_ms"])
        self.assertEqual(r["t_ms"], newer["t_ms"])

    def test_keep_alive_every_few_seconds_gets_no_answer(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port, keep_alive=0.05)
        start = port.keep_alives
        for _ in range(5):
            time.sleep(0.06)
            board.read()
        self.assertGreaterEqual(port.keep_alives - start, 3)
        self.assertNotIn("", port.written)  # an empty line is never a command
        self.assertEqual(board.send("LED T1 red")["type"], "ack")  # still in step
        self.assertIsNone(board.send("   "))  # a blank line: sent, never answered

    def test_no_keep_alive_while_lines_go_out_anyway(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port, keep_alive=60.0)
        start = port.keep_alives
        board.read()
        board.events()
        self.assertEqual(port.keep_alives, start)

    def test_led_clear_once_after_the_hello(self):
        fake = FakeBoard(seed=1)
        fake.send("LED T10 red")  # left on by a laptop that restarted
        port = FakeSerialPort(fake)
        board = connect(port)
        self.assertEqual(port.written, ["LED CLEAR"])
        self.assertEqual(fake.lights, {})
        self.assertEqual(board.connects, 1)
        link = BoardLink(board)
        self.assertEqual(link.lights, {})  # both sides agree

    def test_a_stray_answer_is_not_taken_for_another_command(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        board = connect(port)
        port._line({"type": "error", "cmd": "", "msg": "unknown command: "})  # stray
        port._line({"type": "ack", "cmd": "LED T3 blue"})  # late, from an older command
        answer = board.send("SHOW Hello")
        self.assertEqual(answer, {"type": "ack", "cmd": "SHOW"})

    def test_station_lost_says_unplug_the_cable_first(self):
        fake = FakeBoard(seed=1, has_power_switch=False, has_current=False)
        port = FakeSerialPort(fake)
        board = connect(port)
        port.unplug()
        with self.assertRaises(BoardError) as e:
            board.read()
        msg = str(e.exception)
        self.assertTrue(msg.startswith(SB.STATION_STOPPED), msg)
        self.assertIn("+ may be joined straight to -: unplug the USB cable now", msg)
        # A board that has a power switch keeps the plain words.
        other = FakeSerialPort(FakeBoard(seed=1))
        b2 = connect(other)
        other.unplug()
        with self.assertRaises(BoardError) as e:
            b2.read()
        self.assertIn("Is the USB cable still plugged in?", str(e.exception))
        # The ESP32-S3's USB id alone is enough (before any hello is known).
        b3 = connect(FakeSerialPort(FakeBoard(seed=1)), vid=SB.STATION_VID)
        self.assertTrue(b3.is_station())

    def test_station_port_gone_keeps_saying_unplug_the_cable(self):
        # A hard short resets the S3: its USB port disappears, so every try
        # to open it again fails. The words stay the station's, not "another
        # program is using the port" (round 3, G4).
        port = FakeSerialPort(FakeBoard(seed=1, has_power_switch=False, has_current=False))
        board = connect(port)
        port.unplug()
        for _ in range(3):  # lost, then the port cannot be opened again
            with self.assertRaises(BoardError) as e:
                board.read()
            self.assertTrue(str(e.exception).startswith(SB.STATION_LOST_SAY), str(e.exception))
        self.assertIn("When it is fixed, plug the cable back in: I keep trying. (Cannot open "
                      "COM9", str(e.exception))
        self.assertNotIn("Serial Monitor", str(e.exception))
        self.assertTrue(board.status()["text"].startswith(
            "Board not answering on COM9: " + SB.STATION_STOPPED))
        port.plug_in()
        self.assertEqual(board.read()["type"], "reading")
        # A board that was never connected, or one with a power switch, keeps
        # the plain words about the port.
        other = FakeSerialPort(FakeBoard(seed=1))
        b2 = connect(other)
        other.unplug()
        for _ in range(2):
            with self.assertRaises(BoardError) as e:
                b2.read()
        self.assertIn("Serial Monitor", str(e.exception))

    def test_the_page_says_it_once_without_being_asked(self):
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1, has_power_switch=False,
                                        has_current=False))
        with tempfile.TemporaryDirectory() as tmp:
            app = App(connect(port), lesson=1, history_path=Path(tmp) / "h.json",
                      model=ScriptedTutorModel())
            app.state()
            self.assertEqual(app.log, [])
            port.unplug()
            for _ in range(3):
                st = app.state()
            lost = [m for m in app.log if m["who"] == "tutor"]
            self.assertEqual([m["text"] for m in lost], [SB.STATION_LOST_SAY])  # once
            self.assertTrue(st["board_status"]["text"].startswith(
                "Board not answering on COM9: " + SB.STATION_STOPPED))
            port.plug_in()
            self.assertTrue(app.state()["reading_ok"])
            port.unplug()
            app.state()
            self.assertEqual(len([m for m in app.log if m["who"] == "tutor"]), 2)  # lost again
        # A board with a power switch: no such line (its words stay on the board line).
        other = FakeSerialPort(FakeBoard(1, "correct", seed=1))
        with tempfile.TemporaryDirectory() as tmp:
            app = App(connect(other), lesson=1, history_path=Path(tmp) / "h.json",
                      model=ScriptedTutorModel())
            app.state()
            other.unplug()
            app.state()
            self.assertEqual(app.log, [])

    def test_station_silence_says_the_same(self):
        port = FakeSerialPort(FakeBoard(seed=1, has_power_switch=False))
        board = connect(port)
        port.silent = True
        with self.assertRaises(BoardError) as e:
            board.read()
        self.assertTrue(str(e.exception).startswith(SB.STATION_STOPPED))

    def test_the_tutor_tells_the_station_student_to_pull_the_cable(self):
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1, has_power_switch=False,
                                        has_current=False))
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(connect(port), lesson=1, history_path=Path(tmp) / "h.json")
            agent = build_agent(s, ScriptedTutorModel())
            port.unplug()
            reply, _ = ask(agent, [], "Check my circuit")
        self.assertIn("not answering", reply)
        self.assertIn("+ may be joined straight to -: unplug the USB cable now", reply)


class TestBoardLinkKeepsOnlyWhatTheBoardConfirmed(unittest.TestCase):
    """Round 2 (G2): the laptop's picture of the lights and the screen only
    changes on an ack, and a refusal never leaves a stale picture."""

    class Refusing(FakeBoard):
        refuse = None  # (command word, error message) to refuse

        def send(self, line):
            word = line.split()[0].upper() if line.split() else ""
            if self.refuse and word == self.refuse[0]:
                return {"type": "error", "cmd": word, "msg": self.refuse[1]}
            return super().send(line)

    def test_a_refused_show_or_led_changes_nothing_it_should_not(self):
        board = self.Refusing(seed=1)
        link = BoardLink(board)
        link.show("Hello")
        link.set_light("T10", "red")
        board.refuse = ("SHOW", "too long")  # the old text stays on the screen
        link.show("x")
        self.assertEqual(link.screen, ["Hello"])
        board.refuse = ("SHOW", "a rail is down: the screen keeps the short warning")
        link.show("New text")
        self.assertEqual(link.screen, [])  # not known: never the old text
        board.refuse = ("LED", "unknown color: x")
        link.set_light("T12", "red")
        self.assertEqual(link.lights, {"T10": "red"})  # the board kept T10
        board.refuse = ("LED", "a rail is down: the row lights stay off until it recovers")
        link.set_light("T12", "blue")
        self.assertEqual(link.lights, {})  # that firmware switched every light off

    def test_a_reconnected_board_starts_dark(self):
        port = FakeSerialPort(FakeBoard(seed=1))
        link = BoardLink(connect(port))
        link.set_light("T5", "blue")
        link.show("Step 1")
        port.unplug()
        with self.assertRaises(BoardError):
            link.read()
        port.plug_in()
        link.read()  # connects again: hello, LED CLEAR
        self.assertEqual((link.lights, link.screen), ({}, []))
        self.assertEqual(port.board.lights, {})


class TestTerminalTutorButtons(unittest.TestCase):
    """Round 2 (G8): python -m agent --serial acts on the board's buttons
    while it waits for the student to type."""

    def test_the_poller_hands_presses_to_the_same_handler(self):
        import threading
        from agent.__main__ import ButtonPoller
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1))
        session = Session(connect(port), lesson=1)
        pressed = []
        poller = ButtonPoller(session, pressed.append, threading.Lock())
        port.board.send("PRESS 3")
        self.assertEqual(poller.poll_once(), [3])
        self.assertEqual(pressed, [3])
        self.assertEqual(poller.poll_once(), [])

    def test_a_press_while_waiting_for_typing(self):
        import os
        import agent.__main__ as cli
        port = FakeSerialPort(FakeBoard(1, "correct", seed=1))
        pressed = []

        def typing(prompt=""):
            if prompt == "you> " and not pressed:
                pressed.append(3)
                port.board.send("PRESS 3")  # blue, pressed on the board itself
                time.sleep(1.0)  # the student has not typed anything yet
            return "/quit"

        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli, "choose_port", return_value="COM9"), \
                mock.patch.object(cli, "SerialBoard", lambda p, b: connect(port)), \
                mock.patch("builtins.input", typing), contextlib.redirect_stdout(out):
            code = cli.main(["--serial", "auto", "--lesson", "1", "--fresh",
                             "--history", str(Path(tmp) / "h.json"),
                             "--notebooks", str(Path(tmp) / "nb")])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("(button 3: Next step)", text)
        self.assertIn("step> 1 of 4: Red wire", text)
        self.assertEqual(port.board.lights, {"T5": "blue"})

    def test_a_question_from_a_button_turn_is_answered_at_the_prompt(self):
        """Yellow pressed while the power is off: Zip asks "Turn the power back
        on?". The terminal is waiting at you>, so the next typed line is the
        answer (never two threads reading the keyboard)."""
        import os
        import agent.__main__ as cli
        fake = FakeBoard(1, "correct", seed=1)
        port = FakeSerialPort(fake)
        typed = iter(["yes", "/quit"])
        state = {"pressed": False}

        def typing(prompt=""):
            self.assertEqual(prompt, "you> ")  # only the main loop reads the keyboard
            if not state["pressed"]:
                state["pressed"] = True
                fake.send("CUT")
                fake.send("PRESS 1")  # yellow: check my circuit
                time.sleep(1.5)
            return next(typed)

        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli, "choose_port", return_value="COM9"), \
                mock.patch.object(cli, "SerialBoard", lambda p, b: connect(port)), \
                mock.patch("builtins.input", typing), contextlib.redirect_stdout(out):
            cli.main(["--serial", "auto", "--lesson", "1", "--fresh",
                      "--history", str(Path(tmp) / "h.json"),
                      "--notebooks", str(Path(tmp) / "nb")])
        text = out.getvalue()
        self.assertIn("(button 1: Check my circuit)", text)
        self.assertIn("(type your answer at you>: yes/no)", text)
        self.assertTrue(fake.power_on)  # "yes" turned it back on
        self.assertIn("Your circuit is correct!", text)


if __name__ == "__main__":
    unittest.main()
