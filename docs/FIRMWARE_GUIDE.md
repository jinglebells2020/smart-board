# Genesis: Smart Breadboard: firmware guide (for the hardware teammate)

This page is for whoever writes the board's firmware. It says what the
laptop expects, the smallest firmware that the checker accepts, how to
test by hand, and how fast the board can talk.

The full rules are in [SERIAL_PROTOCOL.md](SERIAL_PROTOCOL.md). The
names (rows, colors, buttons, screen size, cut limit) are in
`protocol.py`. When this page and those two disagree, they win.

The laptop does not care which microcontroller you use. It only reads
and writes lines of text on the USB serial port at **115200 baud**.

---

## 1. The checker: `tools/check_board.py`

The checker is how you know your firmware is right. It connects to the
board, reads the hello line and 20 readings, sends every command, and
prints `PASS` or `FAIL` for each rule in plain words. It ends with
`RESULT: PASS` or `RESULT: FAIL`.

**Run it** (Windows, in the project folder, with the venv on; see
RUN.md parts A and B for the first-time setup):

1. Plug the board in with a USB cable.
2. **Close the Arduino Serial Monitor.** Only one program can use the
   port at a time. Also stop the tutor page if it is running (Ctrl+C).
3. Run:

   ```
   python tools\check_board.py --serial auto
   ```

   `auto` works when only one board is plugged in. Otherwise it lists
   the ports, and you give the name, for example
   `python tools\check_board.py --serial COM3`.
4. Read the result from the top. **Fix the first `FAIL` first**: later
   ones often come from it.

More checks that need you at the board:

```
python tools\check_board.py --serial COM3 --buttons
python tools\check_board.py --serial COM3 --power-cut
```

- `--buttons`: it asks you to press button 1, 2, 3 and 4, one at a time
  (30 seconds each), and checks the `button` line each one sends.
- `--power-cut`: it checks the safety cut (part 6). Only for a board
  with a current sensor and a power switch.

To see what a passing board looks like, without any hardware:

```
python tools\check_board.py --fake --buttons --power-cut
```

---

## 2. The smallest firmware that passes

You do not need real measurements to pass the checker. Start with
made-up numbers, pass, then add real measuring one piece at a time and
run the checker after each piece.

The firmware does four things:

1. **At start:** open serial at 115200 baud and print one hello line.
   Start small: a few rows, no current sensor, no power switch, no
   screen.
2. **Again and again** (for example every 100 ms): print one reading
   line. Its `rows` must have exactly the names from the hello line.
   `t_ms` is `millis()`.
3. **When a whole line arrives** (it ends with `\n`): answer it with
   exactly one line, as in the table below.
4. **Never print anything else.** No "Ready!", no debug text. Every
   line must be JSON. (The laptop skips junk lines, but the checker
   fails on them.)

What to answer, for a board **without** a screen, power switch or
current sensor:

| Line that arrives | Answer with |
|---|---|
| `HELLO` | the hello line again |
| `LED <row> <color>`, row is in your hello rows and starts with T or B, color is `blue` `green` `red` `yellow` `off` | `{"type":"ack","cmd":"LED <row> <color>"}` and set the light |
| `LED CLEAR` | `{"type":"ack","cmd":"LED CLEAR"}` and turn all lights off |
| `LED` with any other row or color | `{"type":"error","cmd":"LED","msg":"bad row or color"}` |
| `SHOW ...` | `{"type":"error","cmd":"SHOW","msg":"no screen"}` |
| `CUT` or `RESTORE` | `{"type":"error","cmd":"CUT","msg":"no power switch"}` (with `RESTORE` in `cmd` for RESTORE) |
| anything else | `{"type":"error","cmd":"<first word>","msg":"unknown command"}` |

That passes every rule. When you add hardware, change the hello line
and the answers:

- **Screen:** `"has_screen": true`. `SHOW line1|line2` shows the lines
  (`|` starts a new line) and answers `{"type":"ack","cmd":"SHOW"}`.
  More than 4 lines, or a line longer than 20 characters, answers
  `{"type":"error","cmd":"SHOW","msg":"too long"}` and shows nothing.
  (If your screen is a different size, change `SCREEN_LINES` and
  `SCREEN_CHARS` in `protocol.py` and tell the team.)
- **Power switch:** `"has_power_switch": true`. `CUT` switches
  breadboard power off, `RESTORE` on; both answer `ack`. Readings say
  `"power_on": false` while it is off. Without a switch, `power_on` is
  always `true`.
- **Current sensor:** `"has_current": true` and `current_ma` is a
  number in every reading. Without a sensor it is `null`, not 0.
- **Buttons:** when button N is pressed, print
  `{"type":"button","id":N}` once per press (not again while it is
  held down; wait about 30 ms to stop contact bounce).
- **Floating rows:** if you can tell that nothing drives a row (an
  empty row), put its name in `floating`. If you cannot tell yet, send
  `"floating":[]`.

---

## 3. Example lines to copy

Copy these exactly (each is one line). Spaces after `:` and `,` are
allowed but not needed.

Hello, 4 rows and the top power strip, nothing else:

```
{"type":"hello","protocol":2,"rows":["T1","T2","T3","T4","PT+","PT-"],"has_current":false,"has_power_switch":false,"has_screen":false,"buttons":4}
```

A reading for that board (T4 is empty):

```
{"type":"reading","t_ms":51230,"power_on":true,"supply_v":5.00,"current_ma":null,"rows":{"T1":4.98,"T2":2.01,"T3":0.00,"T4":1.37,"PT+":5.00,"PT-":0.00},"floating":["T4"]}
```

The same with a current sensor (13.6 mA flowing):

```
{"type":"reading","t_ms":51330,"power_on":true,"supply_v":4.99,"current_ma":13.6,"rows":{"T1":4.98,"T2":2.01,"T3":0.00,"T4":1.37,"PT+":4.99,"PT-":0.00},"floating":["T4"]}
```

Answers:

```
{"type":"ack","cmd":"LED T2 red"}
{"type":"ack","cmd":"LED CLEAR"}
{"type":"ack","cmd":"SHOW"}
{"type":"error","cmd":"LED","msg":"bad row or color"}
{"type":"error","cmd":"SHOW","msg":"no screen"}
{"type":"error","cmd":"CUT","msg":"no power switch"}
{"type":"error","cmd":"FLY","msg":"unknown command"}
```

A button press, and the safety cut (part 6):

```
{"type":"button","id":2}
{"type":"power_cut","reason":"overcurrent","current_ma":104.2,"t_ms":51230}
```

Things that break the laptop:

- `nan` or `inf` instead of a number (not JSON). Send a real number.
- A comma after the last item: `{"T1":4.98,}` is not JSON.
- `True`/`False`/`None` instead of `true`/`false`/`null`.
- Two readings on one line, or one reading split over two lines.

`println` ends lines with `\r\n`. That is fine: the laptop removes the
`\r`. Commands from the laptop end with `\n` only. Remove a `\r` if
there is one (the Serial Monitor can send it).

---

## 4. Test by hand in the Arduino Serial Monitor

Do this before the checker, to see the lines with your own eyes.

1. Upload the firmware. Leave the USB cable plugged in.
2. In the Arduino IDE: **Tools → Serial Monitor** (or the magnifying
   glass at the top right).
3. In the Serial Monitor's bar, set the speed to **115200 baud** and
   the line ending to **Newline**. (With the wrong speed you see
   garbage characters.)
4. You should see the hello line first (most Arduinos restart when the
   Serial Monitor opens), then readings scrolling by.
5. In the box at the top, type each of these and press Enter. Check
   that exactly one answer line comes back for each:

   ```
   HELLO
   LED T1 red
   LED T1 off
   LED CLEAR
   LED T99 red
   LED T1 purple
   SHOW Hello|world
   CUT
   RESTORE
   FLY AWAY
   ```

   With a power switch, after `CUT` the readings must say
   `"power_on":false`, and after `RESTORE` `"power_on":true`.
6. Press each board button. Each press must print one `button` line.
7. To check a line is valid JSON, copy it into any online "JSON
   validator" page.
8. **Close the Serial Monitor** before you run the checker or the
   tutor page.

---

## 5. How many readings per second fit at 115200 baud

**How fast the cable is.** Each byte (one character) costs 10 bits on
the wire: 8 data bits, a start bit and a stop bit. So:

    115200 bits per second ÷ 10 bits per character = 11,520 characters per second

**How long one reading is.** Each measured name costs about 11
characters in `rows` (`"T10":2.01,`), and about 6 more if it is also in
`floating` (`"T10",`). The rest of the line is about 100 characters.
Measured on real reading lines (2 decimals, no spaces, every row
floating, which is the longest case):

| Rows measured | Longest reading | Most readings per second | Use at most |
|---|---|---|---|
| 16 (T1–T16) | about 360 characters | 11,520 ÷ 360 ≈ **32** | **10 per second** |
| 130 (all rows and power strips) | about 2,270 characters | 11,520 ÷ 2,270 ≈ **5** | **2 to 3 per second** |

**Why "use at most" is lower:** the board also has to answer commands,
and the line must be built and measured too. Keep the cable at most
half busy. The laptop takes 10 readings for each check, so at 2 per
second a check takes about 5 seconds; at 10 per second, 1 second.

The checker prints the real numbers for your board ("About 9.8 readings
per second... Longest reading is about 384 characters"). Spaces after
`:` and `,` make lines about 20% longer: leave them out.

**Watch out for the receive buffer.** An Arduino Uno keeps only 64
incoming characters. While your firmware is busy printing a long
reading (2,270 characters take about 0.2 seconds), a `SHOW` command
from the laptop (up to about 90 characters) can arrive and overflow
it, and the command is lost. Read incoming characters into your own
command buffer (at least 128 characters) between rows while you print,
not only between readings. The laptop waits 1 second for each answer.

---

## 6. Safety rule: the board cuts power by itself

**A board with a current sensor and a power switch must switch
breadboard power off by itself** when the current is too high. It must
not wait for the laptop: the laptop can be slow, busy, or unplugged.

1. In every measurement, compare the current with the limit:
   **50 mA** (`FIRMWARE_CUT_MA` in `protocol.py`). The laptop already
   cuts at 30 mA when it sees it; yours is the backstop.
2. Above the limit: **switch power off first**, then print one line:

   ```
   {"type":"power_cut","reason":"overcurrent","current_ma":104.2,"t_ms":51230}
   ```

   `reason` is `overcurrent`, `short`, `overheat` or `other`.
   `current_ma` is the current you saw. `t_ms` is `millis()`.
3. **Stay off** until the laptop sends `RESTORE`. Do not switch back on
   by yourself after a while. Readings say `"power_on":false` meanwhile.
4. After `RESTORE`, measure again at once. If it is still too high,
   cut again and send another `power_cut` line.

The laptop shows the student what happened in plain words and only
turns power back on when the student asks (button 4).

A board **without** a power switch cannot cut. The laptop then tells
the student to switch the power off by hand. Nothing to do in firmware.

**Test it safely with `--power-cut`.** Do **not** short the supply to
test it. Instead:

1. In your firmware, set the limit low for the test, for example
   10 mA. Upload it.
2. Run `python tools\check_board.py --serial COM3 --power-cut`.
3. When it asks, build lesson 1 on the breadboard: the LED with the
   220 Ω resistor (about 14 mA). That is above your 10 mA test limit,
   so the board must cut.
4. The checker checks the `power_cut` line, that power stays off, and
   that `RESTORE` works.
5. **Set the limit back to 50 mA** and upload again.

---

## 7. When the checker says FAIL

| FAIL | What it usually means |
|---|---|
| Cannot open COM3 | Serial Monitor or the tutor page still has the port. Close it. Or the wrong port: run with `--serial auto` to list ports. |
| The board answers HELLO… | Wrong baud rate, or the hello line is not JSON. Look in the Serial Monitor. |
| No junk lines | Debug `Serial.print` lines are still in the firmware. Remove them. |
| "rows" has exactly the names… | A reading is missing a row from the hello line, or has one that is not there. The message lists them. |
| "current_ma" is null | No current sensor, but you sent `0` or nothing. Send `null`. |
| t_ms goes up | Use `millis()`, not a counter that restarts. |
| no answer within 1 s | A command was lost (see "receive buffer" in part 5) or not answered. Every command needs exactly one answer. |
| No extra answer lines | A command got two answers (for example `ack` and then `error`). |
