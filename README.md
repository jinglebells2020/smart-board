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
| `agent/serial_board.py`  | Done. Talks to a real board on a USB serial port (pyserial). Survives junk lines, a missing hello, the cable being unplugged and plugged back, and silence. Use it with `--serial COM3` (or `--serial auto`) |
| `tools/check_board.py`   | Done. Checks a board's firmware against the protocol and prints PASS or FAIL per rule. How the hardware teammate tests the firmware (docs/FIRMWARE_GUIDE.md) |
| `firmware/`              | Not written yet. docs/FIRMWARE_GUIDE.md says what it must do |
| `web/`                   | Done. Projector page made for an 11-year-old: Zip the guide with 4 moods, a drawn plan of the breadboard with the parts and live row lights, a lesson path with stars, a pop-up mini-lesson with quiz, a short celebration, a pretend board (screen + 4 buttons) with the fake board, and a Teacher drawer (`python web/server.py`) |

## Folders

- `docs/` : plans. TRACKS, HARDWARE, LESSONS, SERIAL_PROTOCOL, and
  FIRMWARE_GUIDE (for the hardware teammate).
- `lessons/` : one JSON file per lesson: expected values, the accept
  range, how to spot each mistake, and what to teach for it.
- `protocol.py` : the shared names and rules of the serial protocol.
- `sim/fake_board.py` : pretends to be the board. `sim/fake_port.py`
  pretends to be a serial port with the fake board on it (for tests).
- `tools/check_board.py` : checks a real board's firmware.
- `agent/` : the deepagents tutor (see agent/README.md), with the
  detective subagent and the student notebook.
- `notebooks/` : one notebook per student. Made when the tutor runs;
  never committed (it is about real students).
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

The last line should say `OK` (336 tests). They never call Claude, so
they work without an API key.

## Run the projector page

```bash
python web/server.py --fresh
```

Then open http://localhost:8000.

## Use a real board

Plug the board in, then (Windows steps in RUN.md Part F):

```bash
python tools/check_board.py --serial auto
```

```bash
python web/server.py --fresh --serial auto
```

`--serial auto` finds the board when it is the only serial port;
otherwise give its name (`COM3`, `/dev/ttyUSB0`). `--baud` changes the
speed (default 115200). A line at the top of the page says "Board
connected on COM3" or, in red, "Board not answering". Without
`--serial` the fake board is used.

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

## The page

Made for an 11-year-old: bright, friendly, never scary, with our own
characters and colours. It feels like a bright creative studio: white
work surfaces on a light blue-grey page, five colours with one job each
(blue: top bar and main buttons; yellow: highlights, stars and Zip;
green: correct; coral: problem here; sky blue: "put a part here"),
big rounded buttons, and text that always has strong contrast. The
design rules are checked by tests (`tests/test_page_design.py`).

Three zones fill the screen:

- **Top bar**: the lesson, the lesson path with stars, the power sign
  and the **Teacher** button.
- **The plan: where each part goes** (the stage): a drawing of the
  lesson's rows with each part where the lesson puts it, the row
  lights in their colours (red ones gently pulse: "look here", not
  "you failed") and each row's voltage in small text. The current and
  the power voltage are small pills on top; the pretend board (screen
  and 4 buttons) sits underneath. Parts are drawn in their real
  colours (red wire, black wire, tan resistor with its colour bands,
  red LED).
- After a mistake, a **Learn why** tab appears under Zip's words; it
  opens the mini-lesson in Zip's panel, in place of Zip's picture, so it
  never covers the breadboard, the numbers or Zip's words.
- **Zip**, our robot spark guide, says the tutor's words in a speech
  bubble. 4 faces: happy (correct), thinking (checking), curious (a
  hint or a question) and careful (danger or power off). Below Zip:
  **Check my circuit**, **I fixed it** and a box to ask anything.
- **Lesson path with stars**: 1 star for passing a lesson, a 2nd with
  no mistake made twice, a 3rd when every mini-lesson question in it
  was answered right. Worked out from the history; no new rules.
- A short **celebration** when a circuit is correct: the rows glow
  green, a little confetti, Zip bounces. No sound.
- **Teacher drawer** (closed when the page opens, slides in over Zip):
  practice mode, board status, New student, the notebook link, what the
  tutor did, and the mistake history.

One HTML file, no internet, fits one 1920x1080 screen without
scrolling, and has a phone layout.

## Demo script

Works today with the fake board. Show it on the projector page
(`python web/server.py --fresh`, clicks in RUN.md Part E). The
**pretend board** (under the drawing) has the board's screen and its 4
buttons. Pick circuits in **Teacher → Practice mode**.

1. Build step by step: press **3 · Next step**. The drawing shows the
   red wire strong and the rest faint, row 5 lights blue and the screen
   says "Step 1 of 4 / Red wire". Keep pressing 3: resistor, LED (long
   leg row 10, short leg row 12), black wire.
2. LED backwards, then **1 · Check my circuit** → Zip thinks, then
   looks curious. Rows 10 and 12 glow red, Zip gives a hint (not the
   answer), and the card "LEDs are one-way doors" appears with a quiz
   question. Answer it: right or wrong, the page says why.
3. **2 · I need a hint** → the next level of help: the full
   explanation. The hint button never measures, never changes power and
   never adds a mistake to the history.
4. No resistor, then 1 → power is cut at once, Zip looks careful (a
   friendly yellow sign) and asks what keeps the current small. Card:
   "Resistors keep current small". Press **4** before fixing it: power
   comes on, is measured at once, and goes off again ("still not safe:
   100 mA flowed").
5. Correct circuit, then **I fixed it** → power back on, all rows
   green, the LED in the drawing glows, Zip is happy and bounces,
   confetti falls, and lesson 1 gets its stars on the path. On to
   lesson 2. (Make the same mistake twice in one lesson and the tutor
   asks for that lesson once more.)
6. A mistake on no list ("Not on the list: 4.7k bottom resistor") → Zip
   still explains with a clue from the row voltages, lights that row
   yellow (careful), and then asks the **detective**, which answers
   with at most two questions ("Could the bottom 10k resistor be a
   different value than the plan says...?"). In the Teacher drawer, "What the
   tutor did" says "asked the detective, wrote in the notebook", and
   **Open the student's notebook** shows the line it just wrote.
7. Restart with `--no-power-switch --no-current --rows T1-T16`: the same
   lessons still work. No resistor now puts "DANGER! Switch the power
   OFF by hand now" on the board screen, and the current shows as
   "worked out from the rows".

## How we use deepagents

The tutor is one `create_deep_agent` (agent/build.py) with our board
tools. These deepagents features do real teaching work. Real Claude is
not needed to see them: offline, scripted models run the same
deepagents loop, so every test exercises the real machinery.

| Feature | deepagents part | File | Why it helps a student |
|---|---|---|---|
| **The detective** | A subagent (`subagents=[...]`), called through the built-in `task` tool, only when a mistake is not on the lesson's list. Structured answer (`response_format`) | `agent/detective.py` | The lesson file cannot list every mistake. The detective puts the row voltages next to the lesson layout and answers with at most two likely causes, as **questions** ("Is the LED's long leg pushed all the way into row 10?"). Python rejects more than two, anything that is not a question, or sure-sounding words, so the student is never told a guess as if it were true. |
| **Student notebook** | Memory (`memory=[...]`) on a `FilesystemBackend`, plus `permissions` | `agent/notebook.py` | One file per student, loaded at the start of every reply, so Zip remembers you between days ("Your notebook says this happened before"). After each check Zip adds one line with its own `edit_file` tool. Permissions let it write this one file and nothing else. |
| **Ask before power comes back on** | Human in the loop: `interrupt_on={"restore_power": ...}` with a checkpointer (`InMemorySaver`) | `agent/build.py` (`ask`, `answer_power`), `web/server.py` | Before Zip switches the power on, the run pauses and Zip asks "Turn the power back on?" with **Yes** / **No** (board button 4 = Yes). The student stays in charge of the power. Yes still measures at once and cuts again if the circuit is unsafe (plain Python); No means the tool never runs. |
| **Lesson writer with a grader** | A subagent (`task` tool) whose agent runs `RubricMiddleware`: a grader agent checks the new lesson against a rubric and sends it back for revision | `agent/lesson_writer.py` | When the same mistake comes back, or the quiz was answered wrong, the old explanation did not stick. The lesson writer writes a new mini-lesson with a **different everyday comparison** (a bike tire valve instead of a one-way door). The grader checks: at most 3 short sentences, 6th grade words, never says what to change on the board, exactly 3 options with 1 right. Then plain Python checks it again; only then is it shown, tagged **Written by Zip for you**. Otherwise the built-in card stays. The notebook notes which comparison was used, so it is never repeated. |
| **"How Zip worked"** | A middleware (`wrap_tool_call`) on the tutor and on each subagent, the memory backend, and the grader's `on_evaluation` | `agent/trace.py` | In Teacher view, every reply is listed step by step in plain words, built from what really ran, never guessed. Subagent steps carry a badge (detective, lesson writer, grader). Teachers and judges see the agent think. |

The subagents see only what they need (one read-only tool each) and run
in their own context, so the tutor's conversation stays short. Tests:
`tests/test_deepagents_features.py`, `tests/test_how_zip_worked.py`,
`tests/test_ask_before_power.py`, `tests/test_lesson_writer.py`.

## LangChain demo (60 seconds)

Start the page with a named student, so the notebook is Maya's:

```
python web\server.py --fresh --student Maya
```

Open http://localhost:8000, press F11. To pick a circuit: **Teacher**
(top right) → **Practice mode** → the circuit → **Close**. To show what
Zip did: **Teacher** → **How Zip worked**.

1. **0:00 The detective.** Pick **Not on the list: 150 ohm resistor**,
   click **Check my circuit**. Zip gives a clue and the detective's
   question. How Zip worked:
   1. Read Maya's notebook
   2. Measured the rows
   3. Wrote "Something is off." on the board screen
   4. Asked the detective
   5. Compared the rows with the plan `detective`
   6. Wrote in Maya's notebook
2. **0:15 Memory and the lesson writer.** Pick **LED backwards**, click
   **Check my circuit** twice. The second time Zip says "Your notebook
   says this happened before" and "I also wrote you a new mini-lesson".
   How Zip worked (second check): … 6. Wrote "LEDs are one-way doors"
   on the board screen, 7. Asked the lesson writer, 8. Read the old
   mini-lesson and the comparisons already used `lesson writer`,
   9. Graded the new lesson: it passes every rule `grader`, 10. Wrote in
   Maya's notebook. Click **Learn why**: the card "LEDs work like a bike
   tire valve" has the tag **Written by Zip for you**.
3. **0:35 Ask before power.** Pick **No resistor**, click **Check my
   circuit**: the power is cut (How Zip worked: … Cut the power, Saved
   the mistake (no resistor), Lit rows 5 and 10 red …). Pick **Correct
   circuit**, click **I fixed it**: Zip asks **"Turn the power back
   on?"**. Click **Yes** (or board button 4). How Zip worked:
   1. Read Maya's notebook
   2. Measured the rows
   3. Asked you: turn the power back on? You said yes
   4. Turned the power back on and measured it at once
   5. Turned the lights off
   6. Lit rows 5, 10 and 12 green
   7. Picked the next lesson
   8. Wrote "Correct! Well done." on the board screen
   9. Wrote in Maya's notebook
4. **0:55 The notebook.** Teacher → **Open the student's notebook**: one
   line per check, and "Zip wrote a new mini-lesson … using the
   comparison "a bike tire valve"".

## Consulted with

TODO (Peri track): business or student organization name, the
person's name and role, and one piece of evidence.
