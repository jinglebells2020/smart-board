"""A real board on a serial port (USB cable), speaking protocol version 2.

SerialBoard has the same four methods as the fake board, so the rest of
the laptop software works with either:

    board = SerialBoard("COM3")      # or "/dev/ttyUSB0"
    board.hello()   # what the board has
    board.read()    # the newest reading
    board.send("LED T10 red")
    board.events()  # button presses and power_cut lines

It is made to survive real cables and young firmware:
- junk lines (not JSON, half lines, boot messages) are skipped and counted;
- no hello line at the start: it sends HELLO and waits again;
- the cable is unplugged: every call says so in plain words, and it
  tries to open the port again about once a second, until it works;
- silence: read() gives up after a few seconds with a plain message.
Every problem is raised as BoardError with a message a beginner can act on.
"""

import json
import time
from collections import deque

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - only without pyserial installed
    serial = None

from protocol import DEFAULT_BAUD, PROTOCOL_VERSION

MAX_LINE = 65536  # longer than this is junk, not a real line


class BoardError(Exception):
    """A problem with the real board, in plain words."""


def _need_pyserial():
    if serial is None:
        raise BoardError("pyserial is not installed. Run: python -m pip install -r requirements.txt")


def list_ports():
    """[(port name, description)] for every serial port on this computer."""
    _need_pyserial()
    return [(p.device, p.description or "") for p in serial.tools.list_ports.comports()]


def choose_port(name, ports=None):
    """The port to use. "auto" picks the only port there is; with none or
    more than one it says so, listing them."""
    if name != "auto":
        return name
    ports = list_ports() if ports is None else ports
    if len(ports) == 1:
        return ports[0][0]
    if not ports:
        raise BoardError("No serial ports found. Plug the board in with a USB cable "
                         "(a cable that carries data, not only power), then try again.")
    listed = "\n".join(f"  {dev}  {desc}" for dev, desc in ports)
    raise BoardError("More than one serial port found. Pick one with --serial, "
                     f"for example --serial {ports[0][0]}\n{listed}")


class SerialBoard:
    def __init__(self, port, baud=DEFAULT_BAUD, opener=None, hello_wait=3.0,
                 read_wait=3.0, answer_wait=1.0, retry_every=1.0):
        self.port = port
        self.baud = baud
        if opener is None:
            _need_pyserial()
            opener = lambda: serial.Serial(port, baud, timeout=0, write_timeout=1)
        self._opener = opener
        self.hello_wait = hello_wait
        self.read_wait = read_wait
        self.answer_wait = answer_wait
        self.retry_every = retry_every
        self._ser = None
        self._buf = b""
        self._hello = None
        self._readings = deque(maxlen=200)
        # The last power-on readings that arrived, even ones read() skipped:
        # a power_cut line takes them along, so the tutor can still see
        # what the student had built when the board cut power.
        self._recent_on = deque(maxlen=5)
        self._events = deque()
        self._answers = deque()
        self.junk_lines = 0          # lines that were not protocol JSON
        self.connected = False
        self.last_error = None
        self._last_try = 0.0
        self._connect()

    # --- Connecting ------------------------------------------------------
    def _connect(self):
        self._last_try = time.monotonic()
        try:
            self._ser = self._opener()
        except (OSError, ValueError) as e:  # SerialException is an OSError
            self.connected = False
            self.last_error = (f"Cannot open {self.port}: {e}. Is the board plugged in? "
                               "Is another program (like the Arduino Serial Monitor) "
                               "using the port? Close it and try again.")
            raise BoardError(self.last_error) from None
        self._buf = b""
        self.connected = True
        got = self._wait_for_hello(self.hello_wait)
        for _ in range(3):  # no hello yet: ask for it
            if got:
                break
            self._write("HELLO")
            got = self._wait_for_hello(self.answer_wait)
        if not got:
            self._close()
            self.last_error = (f"The board on {self.port} did not send a hello line, even "
                               f"after being asked (HELLO). Check that the firmware is running, "
                               f"that it uses {self.baud} baud, and that it sends "
                               '{"type": "hello", "protocol": 2, ...} first '
                               "(see docs/FIRMWARE_GUIDE.md).")
            raise BoardError(self.last_error)
        self.last_error = None

    def _wait_for_hello(self, seconds):
        self._hello_fresh = False
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._pump()
            if self._hello_fresh:
                return True
            time.sleep(0.005)
        return False

    def _close(self):
        self.connected = False
        try:
            if self._ser is not None:
                self._ser.close()
        except (OSError, AttributeError):
            pass

    def _lost(self, e):
        self._close()
        self.last_error = (f"The board on {self.port} stopped answering ({e}). Is the "
                           "USB cable still plugged in? Plug it back in: I keep trying.")
        raise BoardError(self.last_error) from None

    def _ensure(self):
        """Raise BoardError while the board is gone; try to reconnect about
        once a second."""
        if self.connected:
            return
        if time.monotonic() - self._last_try < self.retry_every:
            raise BoardError(self.last_error or f"The board on {self.port} is not connected.")
        self._connect()

    # --- Lines -------------------------------------------------------------
    def _write(self, line):
        try:
            self._ser.write((line + "\n").encode("ascii", "replace"))
        except (OSError, ValueError) as e:
            self._lost(e)

    def _pump(self):
        """Read whatever has arrived and sort it into lines."""
        try:
            waiting = self._ser.in_waiting
            data = self._ser.read(waiting or 1) if waiting else b""
        except (OSError, ValueError, AttributeError) as e:
            self._lost(e)
        if not data:
            return
        self._buf += data
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            self._handle(raw)
        if len(self._buf) > MAX_LINE:  # a line that never ends
            self._buf = b""
            self.junk_lines += 1

    def _handle(self, raw):
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except ValueError:
            msg = None
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self.junk_lines += 1
            return
        kind = msg["type"]
        if kind == "hello":
            if msg.get("protocol") != PROTOCOL_VERSION:
                self.junk_lines += 1
                return
            self._hello = msg
            self._hello_fresh = True
            self._answers.append(msg)  # the answer to a HELLO command
        elif kind == "reading":
            self._readings.append(msg)
            if msg.get("power_on"):
                self._recent_on.append(msg)
        elif kind == "power_cut":
            # Laptop-only field (not sent by the board): the readings before it.
            self._events.append(dict(msg, readings_before=list(self._recent_on)))
            self._recent_on.clear()
        elif kind == "button":
            self._events.append(msg)
        elif kind in ("ack", "error"):
            self._answers.append(msg)
        else:
            self.junk_lines += 1

    # --- The four methods ---------------------------------------------------
    def hello(self):
        return dict(self._hello)

    def read(self):
        """The newest reading not returned before. Older ones waiting are
        skipped, so the laptop always judges what is on the board now."""
        self._ensure()
        deadline = time.monotonic() + self.read_wait
        while True:
            self._pump()
            if self._readings:
                newest = self._readings.pop()
                self._readings.clear()
                self.last_error = None  # it is talking again
                return newest
            if time.monotonic() > deadline:
                self.last_error = (f"No readings from the board on {self.port} for "
                                   f"{self.read_wait:g} seconds. Is the firmware still "
                                   "running? Try pressing the board's reset button.")
                raise BoardError(self.last_error)
            time.sleep(0.005)

    def send(self, command):
        """Send one command and return the board's answer line."""
        self._ensure()
        self._answers.clear()
        self._write(command.strip())
        deadline = time.monotonic() + self.answer_wait
        while time.monotonic() < deadline:
            self._pump()
            if self._answers:
                return self._answers.popleft()
            time.sleep(0.005)
        word = (command.split() or [""])[0].upper()
        return {"type": "error", "cmd": word,
                "msg": f"the board did not answer {word} within {self.answer_wait:g} s"}

    def events(self):
        """Button presses and power_cut lines since the last call. Never
        raises: the page asks for these all the time."""
        try:
            self._ensure()
            self._pump()
        except BoardError:
            return []
        out = list(self._events)
        self._events.clear()
        return out

    # --- For the page ---------------------------------------------------------
    def status(self):
        if self.connected and self.last_error is None:
            text = f"Board connected on {self.port} ({self.baud} baud)"
        else:
            text = f"Board not answering on {self.port}: {self.last_error}"
        return {"kind": "serial", "port": self.port, "baud": self.baud,
                "connected": self.connected and self.last_error is None,
                "junk_lines": self.junk_lines, "text": text}

    def close(self):
        self._close()
