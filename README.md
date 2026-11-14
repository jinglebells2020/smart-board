# smart-board

An electric circuit teaching board for middle school students. The
breadboard measures its own circuit. An AI agent reads the
measurements, cuts power on unsafe mistakes, and teaches with hints.
After every mistake the laptop shows a short mini-lesson about the
idea behind that mistake, with a one-question quiz.

## Target user

Middle school students, ages 11 to 14, building their first circuits.
When something is wrong they need to learn *why*, in plain words, not
just hear "wrong" or be handed the answer. All text a student sees is
written at about a 6th grade reading level.

## What works today

| Part                     | Status                                        |
|--------------------------|-----------------------------------------------|
| `sim/fake_board.py`      | Done. Speaks serial protocol version 2. Simulates lessons 1 and 2, every listed mistake and two unlisted ones. Starts with any rows, with or without a current sensor, power switch or screen |
| `lessons/*.json`         | Done for lessons 1 and 2: 10 mistakes, each with a hint, full explanation and mini-lesson (idea + quiz question). Any other mistake gets a clue from the numbers |
| `docs/SERIAL_PROTOCOL.md`| Done, version 2: a voltage per row, empty rows, current (or null), row lights with colors, screen text, 4 buttons |
| `protocol.py`            | Done. Row names, light colors, button meanings and screen size, in one place |
| `agent/`                 | Done. deepagents tutor with the CLAUDE.md tools. Uses Claude when ANTHROPIC_API_KEY is set, an offline mock otherwise |
| `firmware/`              | Waiting on docs/HARDWARE.md                    |
| `web/`                   | Done. Projector page: lesson, live readings, the lesson's rows with their lights, hints, mini-lesson card with quiz, mistake history, and a pretend board (screen + 4 buttons) with the fake board (`python web/server.py`) |

## Folders

- `docs/` : plans. TRACKS, HARDWARE, LESSONS, SERIAL_PROTOCOL.
- `lessons/` : one JSON file per lesson: expected values, the accept
  range, how to spot each mistake, and what to teach for it.
- `protocol.py` : the shared names and rules of the serial protocol.
- `sim/fake_board.py` : pretends to be the board.
- `agent/` : the deepagents tutor (see agent/README.md).
- `tests/` : automatic checks for the simulator, the agent and the web page.
- `web/` : the projector page and its small local server.
- `firmware/` : coming later.

## Setup (step by step)

**Windows: follow [RUN.md](RUN.md)**. It has every click and command.

Mac or Linux, from inside the `smart-board` folder:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## Run the tests

```bash
python -m unittest -v
```

The last line should say `OK` (184 tests). They never call Claude, so
they work without an API key.

## Run the projector page

```bash
python web/server.py --fresh
```

Then open http://localhost:8000.

## Run the tutor in a terminal

```bash
python -m agent --fresh
```

Without ANTHROPIC_API_KEY it uses an offline practice tutor. With the
key set it uses Claude (`claude-opus-5-5`; change it with
SMART_BOARD_MODEL). See RUN.md Part C for what to type.

## Try the simulator by hand

See every circuit it can pretend to be:

```bash
python3 sim/fake_board.py --list
```

Run lesson 1 with the LED in backwards:

```bash
python3 sim/fake_board.py --lesson 1 --scenario led_backwards
```

The first line is the `hello` line (what this board has). Then readings
scroll by, about 10 per second (the rate is not fixed: each reading has
`t_ms`). While they scroll, type a command and press Enter, for example
`LED T10 red`, `LED CLEAR`, `SHOW Hello|world`, `PRESS 1`, `CUT`,
`RESTORE` or `SCENARIO 1 correct`. Press Ctrl+C to stop.

Try a board with only 16 rows, no current sensor and no power switch:

```bash
python3 sim/fake_board.py --rows T1-T16 --no-current --no-power-switch
```

## Demo script

Works today with the fake board. Show it on the projector page
(`python web/server.py --fresh`, clicks in RUN.md Part E). The
**pretend board** (top right) has the board's screen and its 4 buttons.

1. Build step by step: press **3 · Next step**. The rows for the red
   wire light blue and the screen says "Step 1 of 4 / Red wire". Keep
   pressing 3: resistor, LED (long leg row 10, short leg row 12), black
   wire.
2. LED backwards, then **1 · Check my circuit** → rows 10 and 12 light
   red, the tutor gives a hint (not the answer), the screen shows the
   mini-lesson title, and the card "LEDs are one-way doors" appears with
   a quiz question. Answer it: right or wrong, the page says why.
3. **2 · I need a hint** → same mistake again: now the full explanation.
4. No resistor, then 1 → power is cut at once and the tutor asks what
   keeps the current small. Card: "Resistors keep current small".
5. Fixed → power back on, all rows green. Because of the repeated
   mistake, the tutor asks for lesson 1 once more; then lesson 2.
6. A mistake on no list ("Not on the list: 4.7k bottom resistor") → the
   tutor still explains with a clue from the row voltages, and lights
   that row yellow (careful).
7. Restart with `--no-power-switch --no-current --rows T1-T16`: the same
   lessons still work. No resistor now puts "DANGER! Switch the power
   OFF by hand now" on the board screen, and the current shows as
   "calculated".

## Consulted with

TODO (Peri track): business or student organization name, the
person's name and role, and one piece of evidence.
