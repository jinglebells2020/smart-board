# Serial protocol, version 2 (board ↔ laptop)

This is the contract between the board and the laptop. The real
firmware and `sim/fake_board.py` must both follow it, so the agent and
the web page work with either one without changes. The names and rules
below are also in `protocol.py`, which the code uses.

Version 2 replaces version 1 (one `probe1` voltage). The board now
measures many breadboard rows, has a light beside every row and may
have a small screen with 4 buttons.

**Rules for every line**

- One JSON object per line, ending with `\n`. Both directions are text,
  so you can drive the board by hand from a serial terminal.
- The board keeps streaming readings on its own. Commands can arrive at
  any time between them.
- The reading rate is **not fixed**. Each reading has `t_ms`
  (milliseconds since the board started). The laptop uses `t_ms`, never
  an assumed rate.

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

Says what this board has, because that is not decided yet (see
docs/HARDWARE_SUMMARY.md). The laptop adapts to it.

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

If the laptop connects after the board started, it can ask again with
the `HELLO` command.

### `reading`: streamed all the time

```json
{"type": "reading", "t_ms": 51230, "power_on": true, "supply_v": 5.0,
 "current_ma": null,
 "rows": {"T5": 4.99, "T10": 2.01, "T12": 0.0, "T13": 0.12},
 "floating": ["T13"]}
```

| Field | Type | Meaning |
|---|---|---|
| `t_ms` | integer | Milliseconds since the board started |
| `power_on` | true/false | Is breadboard power on? Always `true` without a power switch |
| `supply_v` | number | Voltage of the breadboard supply |
| `current_ma` | number or `null` | Supply current in mA. **`null` when there is no current sensor** |
| `rows` | object | Name → volts, **only for measured names** (the `rows` in `hello`) |
| `floating` | list | Measured names that nothing is driving (an empty row). Their volts mean nothing |

### `button`: when a button is pressed

```json
{"type": "button", "id": 1}
```

`id` is 1 to 4. What each one does is set on the laptop, in one place
(`BUTTONS` in `protocol.py`):

| Button | Meaning |
|---|---|
| 1 | Check my circuit |
| 2 | I need a hint |
| 3 | Next step (light the rows for the next part) |
| 4 | Power off / on |

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
  on the laptop and on the board screen.
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
