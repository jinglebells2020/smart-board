"""A pretend serial port with the fake board on the other end.

It behaves like the pyserial port object SerialBoard uses (write, read,
in_waiting, close, is_open), so SerialBoard and tools/check_board.py can
be tested with no hardware. It can also misbehave on purpose:

    port = FakeSerialPort(FakeBoard(), send_hello=False)   # no hello line
    port.junk = [b"garbage", b'{"half": ']                  # junk lines
    port.unplug(); port.plug_in()                           # the cable
    port.silent = True                                      # board stops talking
    port = FakeSerialPort(FakeBoard(), chunk=4095)          # a Linux tty: at most
    port.backlog(20)                                        # 4 KB per read, and 20
                                                            # readings nobody read yet

The board keeps streaming, like a real one: a new reading goes out every
`period` seconds (0.05 by default). A laptop that looks again after it
has emptied the port finds nothing at first (one look, as on a real port,
so a quick look for button lines takes no reading), then the next reading
once it is due. Only one waits at a time, unless backlog() puts more there. In chunk mode the
port also says "nothing waiting" once between two chunks of a backlog (a
real tty refills its buffer on its own, a moment later), and has the next
chunk on the look after that. Empty lines the laptop sends (keep-alives)
get no answer, like on the station.
"""

import json
import time

import serial

from sim.fake_board import FakeBoard


class FakeSerialPort:
    def __init__(self, board=None, send_hello=True, junk=(), answer_commands=True,
                 hello_on_request=True, chunk=None, period=0.05):
        self.board = board if board is not None else FakeBoard()
        # At most this many bytes per in_waiting / read (None: all of them),
        # like a Linux tty that hands over about 4 KB at a time.
        self.chunk = chunk
        self.period = period                    # seconds between two readings
        self.send_hello = send_hello            # say hello when opened
        self.hello_on_request = hello_on_request  # answer the HELLO command
        self.answer_commands = answer_commands
        self.junk = list(junk)                  # lines sent before the next reading
        self.silent = False                     # True: says nothing at all
        self.plugged = True
        self.is_open = False
        self.written = []                       # every command line the laptop sent
        self.keep_alives = 0                    # empty lines the laptop sent
        self._out = bytearray()
        self._sent_at = None                    # when the newest reading went out
        self._looked = False                    # looked once since the port was emptied
        self._refilling = False                 # chunk mode: the next look finds nothing
        self.open()

    # --- The cable ------------------------------------------------------
    def open(self):
        if not self.plugged:
            raise serial.SerialException("could not open port: device not found")
        self.is_open = True
        self._out = bytearray()
        if self.send_hello:
            self._line(self.board.hello())
        return self

    def unplug(self):
        self.plugged = False
        self.is_open = False

    def plug_in(self):
        self.plugged = True

    def _check(self):
        if not self.plugged or not self.is_open:
            raise serial.SerialException("device reports readiness to read but returned "
                                         "no data (device disconnected?)")

    # --- pyserial-like methods ---------------------------------------------
    def _line(self, obj):
        text = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj).encode()
        self._out += bytes(text) + b"\n"

    def _stream(self):
        """Nothing waiting, the laptop looks again and the next reading is
        due: the board sends it."""
        now = time.monotonic()
        if self.silent or self._out or not self._looked or \
                (self._sent_at is not None and now - self._sent_at < self.period):
            return
        for junk in self.junk:
            self._line(junk)
        self.junk = []
        self._line(self.board.read())
        for ev in self.board.events():
            self._line(ev)
        self._sent_at = now

    def backlog(self, n):
        """n readings (and any button lines) that arrived while the laptop
        was not reading."""
        for _ in range(n):
            self._line(self.board.read())
            for ev in self.board.events():
                self._line(ev)
        self._sent_at = time.monotonic()

    def _limit(self, n):
        return min(n, self.chunk) if self.chunk else n

    @property
    def in_waiting(self):
        self._check()
        if self._refilling:  # between two chunks: nothing for a moment
            self._refilling = False
            return 0
        if not self._out and not self.silent:
            for ev in self.board.events():  # a button line goes out the moment it happens
                self._line(ev)
        if not self._out:
            self._stream()      # the first look after emptying finds nothing
            self._looked = True
        return self._limit(len(self._out))

    def read(self, size=1):
        self._check()
        self._stream()
        data = bytes(self._out[:self._limit(size)])
        del self._out[:len(data)]
        if data:
            self._looked = False  # the laptop got something: its next look is a new one
        if self.chunk and len(data) == self.chunk and self._out:
            self._refilling = True  # a full chunk, and more behind it
        return data

    def write(self, data):
        self._check()
        text = bytes(data).decode("utf-8", "replace")
        if not text.strip():
            self.keep_alives += text.count("\n")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.written.append(line)
            if self.silent or not self.answer_commands:
                continue
            if line.upper() == "HELLO" and not self.hello_on_request:
                continue
            self._line(self.board.send(line))
            for ev in self.board.events():
                self._line(ev)
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._out = bytearray()

    def close(self):
        self.is_open = False
