"""A pretend serial port with the fake board on the other end.

It behaves like the pyserial port object SerialBoard uses (write, read,
in_waiting, close, is_open), so SerialBoard and tools/check_board.py can
be tested with no hardware. It can also misbehave on purpose:

    port = FakeSerialPort(FakeBoard(), send_hello=False)   # no hello line
    port.junk = [b"garbage", b'{"half": ']                  # junk lines
    port.unplug(); port.plug_in()                           # the cable
    port.silent = True                                      # board stops talking

Each read with nothing waiting makes the board send its next reading,
like a board that keeps streaming.
"""

import json

import serial

from sim.fake_board import FakeBoard


class FakeSerialPort:
    def __init__(self, board=None, send_hello=True, junk=(), answer_commands=True,
                 hello_on_request=True):
        self.board = board if board is not None else FakeBoard()
        self.send_hello = send_hello            # say hello when opened
        self.hello_on_request = hello_on_request  # answer the HELLO command
        self.answer_commands = answer_commands
        self.junk = list(junk)                  # lines sent before the next reading
        self.silent = False                     # True: says nothing at all
        self.plugged = True
        self.is_open = False
        self.written = []                       # every command line the laptop sent
        self._out = bytearray()
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
        """Nothing waiting: the board sends what it would send next."""
        if self.silent or self._out:
            return
        for junk in self.junk:
            self._line(junk)
        self.junk = []
        self._line(self.board.read())
        for ev in self.board.events():
            self._line(ev)

    @property
    def in_waiting(self):
        self._check()
        self._stream()
        return len(self._out)

    def read(self, size=1):
        self._check()
        self._stream()
        data = bytes(self._out[:size])
        del self._out[:size]
        return data

    def write(self, data):
        self._check()
        for line in bytes(data).decode("utf-8", "replace").splitlines():
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
