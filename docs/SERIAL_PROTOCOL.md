# Serial protocol (board ↔ laptop)

This is the contract between the board and the laptop. The real
firmware and `sim/fake_board.py` must both follow it, so the agent
and web page work with either one without changes.

One message per line. Every line is one JSON object. Lines end
with `\n`.

## Board → laptop: readings

Sent about 10 times per second (every 100 ms).

```json
{"type": "reading", "t_ms": 1200, "power_on": true,
 "supply_v": 5.0, "current_ma": 13.6,
 "voltages": {"probe1": 2.0}, "led_row": null}
```

| Field        | Type          | Meaning                                         |
|--------------|---------------|-------------------------------------------------|
| `type`       | string        | Always `"reading"` for readings                 |
| `t_ms`       | integer       | Milliseconds since the board started            |
| `power_on`   | true/false    | false after `CUT`, true after `RESTORE`         |
| `supply_v`   | number, volts | Voltage on the breadboard supply rail           |
| `current_ma` | number, mA    | Current flowing out of the supply               |
| `voltages`   | object        | Probe name → volts. Only `probe1` for now       |
| `led_row`    | integer/null  | Row whose indicator LED is lit, or null         |

Where `probe1` sits on the breadboard depends on the lesson. Each
`lessons/*.json` file says where (field `probe1`). The real row
number goes in docs/HARDWARE.md once it is decided.

## Laptop → board: commands

Plain text, one per line.

| Command      | Effect                                   |
|--------------|------------------------------------------|
| `CUT`        | Turn breadboard power off                |
| `RESTORE`    | Turn breadboard power back on            |
| `LED <row>`  | Light the indicator LED for row `<row>`  |

The board answers every command with one line:

```json
{"type": "ack", "cmd": "CUT"}
{"type": "error", "msg": "unknown command: FOO"}
```

## Simulator only

`sim/fake_board.py` also accepts this command. The real board
ignores it (it answers with an error):

| Command                       | Effect                                |
|-------------------------------|---------------------------------------|
| `SCENARIO <lesson> <name>`    | Pretend the student built this circuit |

Example: `SCENARIO 1 led_backwards`. See `python sim/fake_board.py --list`.
