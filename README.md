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
| `firmware/`              | Waiting on docs/HARDWARE.md                    |
| `agent/`, `web/`         | Not started                                   |

## Folders

- `docs/` : plans. TRACKS, HARDWARE, LESSONS, SERIAL_PROTOCOL.
- `lessons/` : one JSON file per lesson: expected values, the accept
  range, and how to spot each mistake.
- `sim/fake_board.py` : pretends to be the board.
- `tests/` : automatic checks for the simulator.
- `firmware/`, `agent/`, `web/` : coming later.

## Setup (step by step)

1. Install Python 3.10 or newer. Check by opening a terminal and typing
   `python3 --version`. On Windows, type `python` instead of `python3`
   in all the steps below.
2. Download this repository and open a terminal **inside** the
   `smart-board` folder. Every command below runs from there.
3. Nothing else to install yet. The simulator and tests only use
   Python's built-in libraries.

## Run the tests

```bash
python3 -m unittest -v
```

Each line should end with `ok`, and the last line should say `OK`. If
you see `FAILED`, read the lines above it to see which test failed.

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

TODO, written once the agent and web page exist. Draft:

1. Start lesson 1. The student builds the circuit with the LED in
   backwards → the agent gives a polarity hint, not the answer.
2. The student removes the resistor → the agent cuts power and
   explains why.
3. The agent picks lesson 2 based on the mistakes it logged.

## Consulted with

TODO (Peri track): business or student organization name, the
person's name and role, and one piece of evidence.
