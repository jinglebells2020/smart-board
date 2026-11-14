# smart-board

A breadboard that measures its own circuit. An AI agent reads the
measurements, cuts power on unsafe mistakes, and teaches with hints.

## Target user

TODO (confirm): students learning their first circuits, who need
someone to say *why* the circuit doesn't work without just giving
the answer.

## What works today

| Part                     | Status                                        |
|--------------------------|-----------------------------------------------|
| `sim/fake_board.py`      | Done. Simulates lessons 1 and 2 plus every listed mistake |
| `lessons/*.json`         | Done for lessons 1 and 2                      |
| `docs/SERIAL_PROTOCOL.md`| Done. The JSON format the board must send     |
| `agent/`                 | Done. deepagents tutor with the CLAUDE.md tools. Uses Claude when ANTHROPIC_API_KEY is set, an offline mock otherwise |
| `firmware/`              | Waiting on docs/HARDWARE.md                    |
| `web/`                   | Done. Projector page: lesson, live readings, hints, mistake history (`python web/server.py`) |

## Folders

- `docs/` : plans. TRACKS, HARDWARE, LESSONS, SERIAL_PROTOCOL.
- `lessons/` : one JSON file per lesson: expected values, the accept
  range, and how to spot each mistake.
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

The last line should say `OK` (46 tests). They never call Claude, so
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

Readings scroll by, 10 per second. While they scroll, type a command
and press Enter: `CUT`, `RESTORE`, `LED 5`, or `SCENARIO 1 correct`.
Press Ctrl+C to stop.

## Demo script

Works today with the fake board. Show it on the projector page
(`python web/server.py --fresh`, clicks in RUN.md Part E).

1. LED in backwards → the tutor gives a polarity hint, not the answer.
2. Same mistake again → now it explains fully.
3. No resistor → power is cut at once, and the tutor asks what
   limits current.
4. Fixed → power back on. Because of the repeated mistake, the tutor
   asks for lesson 1 once more before moving to lesson 2.
5. Lesson 2 with the probe unplugged → hint, and the probe row's LED
   lights up.

## Consulted with

TODO (Peri track): business or student organization name, the
person's name and role, and one piece of evidence.
