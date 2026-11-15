# Genesis: Smart Breadboard: serial protocol, version 2 (board ↔ laptop)

This is the contract between the board and the laptop. The real
firmware and `sim/fake_board.py` must both follow it, so the agent and
the web page work with either one without changes. The names and rules
below are also in `protocol.py`, which the code uses.

Writing the firmware? Start with [FIRMWARE_GUIDE.md](FIRMWARE_GUIDE.md),
and test it with `python tools/check_board.py --serial COM3`.

Version 2 replaces version 1 (one `probe1` voltage). The board now
measures many breadboard rows, has a light beside every row and may
have a small screen with 4 buttons.

The real board is **the station** (`docs/HARDWARE.md`): its firmware
`firmware/smartboard_s3` (jinglebells2020/smartboard) speaks this
protocol in its **Genesis mode**, which it starts in. Its hello line:
every row once the row map from the mux test is flashed (`[]` before),
`"has_current": false`, `"has_power_switch": false`, `"has_screen": true`,
`"buttons": 4` and `"supply_v": 3.3`.

**Rules for every line**

- One JSON object per line, ending with `\n`. Both directions are text,
  so you can drive the board by hand from a serial terminal.
- The board keeps streaming readings on its own. Commands can arrive at
  any time between them.
- The reading rate is **not fixed**. Each reading has `t_ms`
  (milliseconds since the board started). The laptop uses `t_ms`, never
  an assumed rate. It reads everything waiting before it judges, so it
  always uses the newest reading, never one from seconds ago.
- **An empty line from the laptop is a keep-alive.** Genesis sends one
  about every 2 seconds while it is using the board, so the station knows
  the laptop is still there. The board must not answer it (an answer could
  be taken for the answer to the next command); `tools/check_board.py`
  checks this.
- **When the laptop connects** it reads the hello line (or asks with
  `HELLO`), then sends `LED CLEAR` once: a board that kept running while
  the laptop restarted might still show old lights.

## Names

| Name | Meaning |
|---|---|
| `T1` … `T63` | Row 1 to 63, holes a–e (top half of the breadboard) |
| `B1` … `B63` | Row 1 to 63, holes f–j (bottom half) |
| `PT+`, `PT-` | Top power strip, + and − |
| `PB+`, `PB-` | Bottom power strip, + and − |

Rows have a light beside them; the power strips do not.

## Board → laptop

### `hello`: once, when the board starts

Says what this board has (the station's answers are in
docs/HARDWARE_SUMMARY.md). The laptop adapts to it, so other boards and
the fake board still work.

```json
{"type": "hello", "protocol": 2,
 "rows": ["T1", "T2", "T3", "PT+", "PT-"],
 "has_current": false, "has_power_switch": false,
 "has_screen": true, "buttons": 4}
```

| Field | Meaning |
|---|---|
| `protocol` | Always `2` for this version |
| `rows` | Every name this board measures. Can be any set: all 130, or only 16 rows |
| `has_current` | `true` if a current sensor measures the supply current |
| `has_power_switch` | `true` if the board can switch breadboard power off and on |
| `has_screen` | `true` if the board has a screen |
| `buttons` | How many buttons (0 to 4) |
| `supply_v` | Optional. The breadboard supply voltage, in volts (the station: `3.3`). The laptop judges "the + line is pulled down" against it and says it to the student. Without it the laptop assumes 3.3 V (`SUPPLY_V` in `protocol.py`) |

If the laptop connects after the board started, it can ask again with
the `HELLO` command.

### `reading`: streamed all the time

```json
{"type": "reading", "t_ms": 51230, "power_on": true, "supply_v": 3.3,
 "current_ma": null,
 "rows": {"T5": 3.3, "T10": 2.01, "T12": 0.0, "T13": 1.65},
 "floating": ["T13"]}
```

| Field | Type | Meaning |
|---|---|---|
| `t_ms` | integer | Milliseconds since the board started |
| `power_on` | true/false | Is breadboard power on? Always `true` without a power switch |
| `supply_v` | number | Voltage of the breadboard supply. The station sends its + line when that is below the top of its meter, `0` when its + tap floats (every wire pulled out of the + line: the power is off by hand), else 3.3 |
| `current_ma` | number or `null` | Supply current in mA. **`null` when there is no current sensor** |
| `rows` | object | Name → volts, **only for measured names** (the `rows` in `hello`). A board whose meter tops out below its supply (the station: about 2.9-3.1 V) sends a row at or above that top as the supply voltage ("at the supply") |
| `floating` | list | Measured names that nothing is driving (an empty row). Their volts mean nothing |

### `button`: when a button is pressed

```json
{"type": "button", "id": 1}
```

`id` is 1 to 4. What each one does is set on the laptop, in one place
(`BUTTONS` in `protocol.py`). On the station the buttons have colours,
not numbers (`BUTTON_COLORS`), and the StickS3's orange key also sends 1:

| Button | Station | Meaning |
|---|---|---|
| 1 | yellow | Check my circuit |
| 2 | white | I need a hint |
| 3 | blue | Next step (light the rows for the next part) |
| 4 | red | Power off / on |

### `power_cut`: when the board switches power off by itself

A board with a current sensor **and** a power switch must not wait for
the laptop: when the current is too high it switches power off at once
(suggested limit: 50 mA, `FIRMWARE_CUT_MA` in `protocol.py`), then says
so with one line, and keeps power off until it gets `RESTORE`:

```json
{"type": "power_cut", "reason": "overcurrent", "current_ma": 104.2, "t_ms": 51230}
```

| Field | Meaning |
|---|---|
| `reason` | `overcurrent`, `short`, `overheat` or `other` |
| `current_ma` | The current it saw, in mA (`null` if it has no current sensor) |
| `t_ms` | When it happened |

After this line, readings show `"power_on": false`. The laptop tells the
student what happened, in plain words, and power only comes back on when
the student asks (button 4).

### Answers to commands

Every command gets exactly one answer line:

```json
{"type": "ack", "cmd": "LED T10 red"}
{"type": "error", "cmd": "CUT", "msg": "no power switch"}
```

`cmd` starts with the command's first word. The laptop only takes an
answer for the command it belongs to (a late answer to an earlier
command is skipped), and it only changes its picture of the lights and
the screen when the board answers `ack`.

## Laptop → board: commands

Plain text, one command per line.

| Command | Effect |
|---|---|
| `LED <row> <color>` | Light the light beside `<row>` in a color (below) |
| `LED CLEAR` | Turn every light off |
| `SHOW <line>\|<line>\|…` | Show text on the screen; `\|` starts a new line |
| `CUT` | Switch breadboard power off |
| `RESTORE` | Switch breadboard power back on |
| `HELLO` | Send the `hello` line again |
| an empty line | Nothing: the laptop's keep-alive (about every 2 s). **No answer** |

**Light colors.** Each color always means the same thing:

| Color | Meaning |
|---|---|
| `blue` | Put a part here |
| `green` | Correct |
| `red` | Problem here |
| `yellow` | Careful |
| `off` | Light off |

**Errors the board must give:**

- `CUT` or `RESTORE` on a board with no power switch:
  `{"type": "error", "cmd": "CUT", "msg": "no power switch"}`.
  The laptop then tells the student to switch the power off by hand,
  on the laptop and on the board screen. On the station that means
  "pull every wire out of the + line" (`MANUAL_OFF_WORDS` in
  `protocol.py`). When the student does, the station's readings show it
  (`supply_v` 0 and its + taps in `floating`, or the plan's + row empty):
  the laptop says "Good, the power is off" and waits for the fix, and it
  never takes `supply_v` 0 with floating + taps for a short.
- `SHOW` on a board with no screen: `"msg": "no screen"`.
- `SHOW` with more lines or longer lines than the screen holds:
  `"msg": "too long"`. The laptop never sends that: it fits the text
  first (`fit_screen` in `protocol.py`, 4 lines of 20 characters for
  now, plain ASCII).
- `LED` with a name that is not a row, or an unknown color.

## Simulator only

`sim/fake_board.py` also accepts these. A real board answers them with
an error.

| Command | Effect |
|---|---|
| `SCENARIO <lesson> <name>` | Pretend the student built this circuit |
| `PRESS <id>` | Pretend button `<id>` was pressed (sends a `button` line) |

See `python sim/fake_board.py --help` for starting it with or without a
current sensor, with or without a power switch, and with any set of rows.
