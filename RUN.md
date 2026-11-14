# How to run it on Windows (step by step)

Do these steps in order. Each grey box is one command: copy it, paste
it into the black Command Prompt window, and press **Enter**.

You do **not** need the real board or an API key. Without them the
fake board and an offline practice tutor are used.

---

## Part A: one-time setup (about 15 minutes)

### Step 1. Install Python

1. Go to https://www.python.org/downloads/ and click the yellow
   **Download Python 3.13** button (any 3.10 or newer is fine).
2. Open the downloaded file.
3. **Important:** on the first screen, tick the box
   **"Add python.exe to PATH"** at the bottom. Then click
   **Install Now**.
4. When it says "Setup was successful", click **Close**.

### Step 2. Install Git

1. Go to https://git-scm.com/download/win and download
   **64-bit Git for Windows Setup**.
2. Open it and click **Next** on every screen, then **Install**.
   The default choices are fine.

### Step 3. Open Command Prompt

1. Press the **Windows key**, type `cmd`, and press **Enter**.
2. A black window opens. This is the Command Prompt. Keep it open.

Check that Python works:

```
python --version
```

You should see something like `Python 3.13.1`. If you see
`'python' is not recognized`, see **Problems** at the bottom.

### Step 4. Download the project

```
cd %USERPROFILE%\Documents
```

```
git clone https://github.com/jinglebells2020/smart-board.git
```

If a window asks you to sign in to GitHub, sign in. Then:

```
cd smart-board
```

The newest code is on a work branch. Switch to it (skip this once it
has been merged into `main`):

```
git checkout claude/blissful-curie-udr219
```

### Step 5. Make a private Python folder for this project

This keeps the project's libraries separate from the rest of your
computer.

```
python -m venv .venv
```

Turn it on:

```
.venv\Scripts\activate.bat
```

Your prompt now starts with `(.venv)`. You must see `(.venv)` before
running anything below.

### Step 6. Install the libraries

```
python -m pip install -r requirements.txt
```

This downloads deepagents and the Claude connector. It takes a few
minutes. Lots of text scrolls by. It is done when you get your prompt
back. A yellow "new release of pip" notice is fine to ignore.

---

## Part B: check that everything works

```
python -m unittest -v
```

Wait about 40 seconds. The last lines should be:

```
Ran 184 tests in 30.4s

OK
```

If it says `FAILED`, copy the whole output and send it to your
teammate or to Claude.

---

## Part C: try the tutor (no API key needed)

```
python -m agent --fresh
```

`--fresh` starts with an empty mistake history. You see:

```
No ANTHROPIC_API_KEY set: using the offline practice tutor.
Lesson 1: LED with resistor (5 V -> 220 ohm -> red LED -> ground)
you>
```

Now type these lines one by one, pressing Enter after each. Lines
starting with `/` change the circuit on the fake board. The other
lines are what a student would say.

| You type                    | What happens                                     |
|-----------------------------|--------------------------------------------------|
| `/build led_backwards`      | Fake board now has the LED in backwards          |
| `check my circuit`          | Tutor gives a hint: look at which way the LED faces and which row its legs are in |
| `check again`               | Same mistake twice: tutor now explains fully     |
| `/build no_resistor`        | Fake board now has no resistor                   |
| `check`                     | Tutor cuts the power and asks what limits current |
| `/build correct`            | Student fixes it                                  |
| `I fixed it`                | Tutor turns power back on and says it is correct. Because the LED went in backwards twice, it asks for lesson 1 once more |
| `check`                     | Correct again: tutor moves on to lesson 2         |
| `/build middle_floating`    | Measuring row not connected                      |
| `check`                     | Tutor hints, and row 10 lights red               |
| `/build bottom_4k7`         | A mistake that is on no list (4.7k bottom resistor) |
| `check`                     | "This is not a mistake I know yet, but here is a clue." Then which numbers are off and what that usually means |
| `/quit`                     | Stop                                              |

`/list` shows every circuit you can `/build` for the current lesson.
`/press 3` presses board button 3 (next step): it shows where the next
part goes. `/press 1` checks, `/press 2` asks for a hint, `/press 4`
switches power. `/board` shows the lights and the board screen.

Mini-lessons and their quiz questions appear only on the web page
(Part E), not in this terminal.

The offline tutor always uses fixed sentences. It is there so you can
test without paying for Claude.

---

## Part D: use the real Claude (when you have an API key)

1. Get a key from the Claude Console at https://platform.claude.com
   (Settings → API keys → Create key). It starts with `sk-ant-`.
   Using it costs money, so set a spending limit in the Console.
2. **Never** paste the key into a file in this folder, and never
   commit it to Git. Only type it into Command Prompt as shown here.
3. In the same Command Prompt window (with `(.venv)` showing):

   ```
   set ANTHROPIC_API_KEY=sk-ant-PASTE-YOUR-KEY-HERE
   ```

   No spaces around `=`. No quotes.

4. Start the tutor, with the student's first name:

   ```
   python -m agent --fresh --student Maya
   ```

   A line now says `Model: anthropic:claude-opus-5-5` instead of
   "offline practice tutor". Use the same steps as Part C. Replies are
   in Claude's own words now and take a few seconds.

5. The line above it says where Maya's **notebook** is
   (`notebooks\maya.md`). Claude reads it at the start of every
   message and adds one line after each check. Type `/notebook` to see
   it. It stays on the laptop: next time, start with the same
   `--student Maya` and the tutor remembers her. Leave out `--student`
   and the notebook is `notebooks\student.md`.

The `set` command only lasts until you close this window. Next time,
run it again. To use a different model, also run
`set SMART_BOARD_MODEL=claude-sonnet-5-5` (cheaper) before starting.

The tests in Part B never use the key, even when it is set.

---

## Part E: the projector page

1. In Command Prompt (with `(.venv)` showing), start the page's server:

   ```
   python web\server.py --fresh
   ```

   It prints `Open http://localhost:8000 in your browser`. Leave this
   window open: closing it stops the page.

2. Open Chrome or Edge and go to **http://localhost:8000**
3. Press **F11** for full screen. Everything fits on one 1080p screen.

   What you see:

   - **Top bar:** the lesson name, the **lesson path** (a circle for
     each lesson, with 3 stars under it), the power sign and the
     **Teacher view** button.
   - **Your breadboard** (left): a drawing of the lesson's rows with
     every part where the lesson puts it (red wire, resistor with its
     colour bands, LED with its long leg marked +, black wire). Under
     each row: its light (blue, green, red or yellow, the real colours)
     and its voltage in small text. Below the drawing: the current, the
     power voltage and the **Pretend board** (the board's small screen
     and its 4 buttons).
   - **Zip** (right): our robot spark guide. The tutor's words are in
     Zip's speech bubble. Zip has 4 faces: **happy** (correct),
     **thinking** (while checking), **curious** (a hint or a question)
     and **careful** (danger or power off).
   - The big buttons: **Check my circuit**, **I fixed it**, and a box to
     ask Zip anything.

4. Run the demo with the mouse. The pretend board's buttons work like
   the real board's buttons will. To choose what the pretend student
   built, open **Teacher view** (top right), click a circuit under
   **Practice mode**, then **Close** (or press Esc).

   | Click | What the audience sees |
   |---|---|
   | **3 · Next step** | The red wire is drawn strong, the other parts faint. Row 5's light turns **blue** (put a part here). Under the drawing: "Step 1 of 4: Red wire". Zip: "Put one end in the top + line (5 V) and the other end in row 5" |
   | **3 · Next step** three more times | Resistor (rows 5 and 10), LED ("long leg: row 10 / short leg: row 12"), black wire (row 12). Each time that part is outlined in blue and its rows light blue |
   | **3 · Next step** once more | Lights off, all parts drawn. Screen: "All parts placed! / Press 1 to check." |
   | Teacher view → **LED backwards**, Close, then **1 · Check my circuit** | Zip looks **thinking**, then **curious**. Rows 10 and 12 glow **red**, gently pulsing (red means "look here"). Zip gives a hint. A yellow **Mini-lesson** card: "LEDs are one-way doors", with a question and 3 big answers |
   | The middle answer, **"The LED faces the wrong way, or a leg is not connected"** | It turns green: "Right!" and why |
   | **2 · I need a hint** | Zip, still **curious**, gives the next level of help: the full explanation. Nothing is measured, power is not touched |
   | Teacher view → **No resistor**, Close, then **1 · Check my circuit** | Zip looks **careful** (a friendly yellow sign, nothing scary). The power sign says **Power off**. Card: "Resistors keep current small" |
   | **4 · Power off / on** (before fixing) | Power comes on, is measured at once and goes off again. Zip: "The power went off again right away, because the circuit is still not safe: 100 mA flowed…" |
   | Teacher view → **Correct circuit**, Close, then **I fixed it** | Zip turns **happy** and bounces, a short burst of confetti falls (no sound), the LED in the drawing glows, and lesson 1 on the path turns green with its stars. The page moves to lesson 2 |
   | **4 · Power off / on** (twice) | Power off (Zip careful), then on again (Zip happy) |

   **The stars** (under each lesson on the path) come from what the
   student already did: 1 star for passing the lesson, a 2nd if no
   mistake happened twice in it, a 3rd if every mini-lesson question in
   it was answered right. A lesson passed with no mistakes gets all 3.
   New stars pop when they appear.

   Power only comes back on when the student asks: button 4, or saying
   they fixed it ("I fixed it", "I added the resistor"). "Check my circuit"
   while power is off just says how to turn it on. Every time power comes
   on, the circuit is measured at once and switched off again if unsafe.

   Each quiz takes one answer. A second click does not change it.

   **Teacher view** (top right, closed when the page opens) has
   everything for the grown-ups: whether a board is connected, which
   tutor is running, **Practice mode**, **New student**, a link to the
   student's notebook, "What the tutor did" (for example "The tutor
   measured the rows, saved the mistake, cleared the lights, lit rows,
   wrote on the screen"), the earlier messages and the **Mistake
   history** table. Close it with **Close** or the Esc key.

   More circuits to try. In Teacher view pick one, close it, then click
   **1 · Check my circuit**:

   | Lesson | Circuit | What the tutor does |
   |---|---|---|
   | 1 | **Part in wrong row** | Row 10 shows "empty" and lights red. Card: "Circuits must be closed loops" |
   | 1 | **Resistor too big (1k)** | Hint: the LED gets much less current, so it is dim. Card: "Bigger resistor, less current" |
   | 1 | **Current skips the LED** | Hint: lots of current but 0 V on row 10. No power cut (22.7 mA is safe). Card: "Current takes the easy path" |
   | 1 | **Not on the list: 150 ohm resistor** | No card. Zip gives a clue, then the detective's question |
   | 2 | **Wrong top resistor (1k)** | Hint: the middle is much higher than half. Card: "Bigger resistor, bigger share" |
   | 2 | **Bottom resistor missing** | Hint: no current, full 5 V in the middle. Card: "No flow, no voltage drop" |
   | 2 | **Top resistor missing** | Hint: no current, 0 V in the middle. Card: "Voltage needs a path from power" |
   | 2 | **Not on the list: 4.7k bottom resistor** | No card. Row 10 lights **yellow** (careful), Zip gives a clue and the detective's two questions |

   **Try a different board.** The real board's details are not decided
   yet. Stop the server (Ctrl+C in its window) and start it with any of
   these, then open the page again:

   ```
   python web\server.py --fresh --no-current --no-power-switch --rows T1-T16
   ```

   Teacher view now says "Board: 16 rows measured, current sensor no,
   power switch no". Both lessons still work. The current tile says
   "Worked out from the rows". Pick **No resistor**, then
   **1 · Check my circuit**: Zip says "Switch the power off by hand
   right now", and the board screen says "DANGER! Switch the power OFF by
   hand now." **4 · Power off / on** says the board cannot switch power.
   With `--no-current`, **Resistor too big (1k)** looks correct, and the
   tutor says plainly that this board cannot measure current, so it could
   not check for it.

5. **New student** (in Teacher view) forgets the history and the stars
   and starts again at lesson 1, with an empty notebook. (The old
   notebook is kept in the `notebooks` folder with the date in its
   name.) Use it before each demo.
6. To see the student's notebook, open Teacher view and click **Open
   the student's notebook** (or go to http://localhost:8000/api/notebook
   in a second tab), and press F5 to refresh it after a check. To give
   the notebook a name, start the server with `--student Maya`.
   `--fresh` forgets the history but keeps the notebook.
7. To stop: click the Command Prompt window and press **Ctrl+C**.

With `set ANTHROPIC_API_KEY=...` done first (Part D), the page uses
Claude. Teacher view says which tutor is running.

If the page shows a yellow bar saying Zip cannot reach the tutor
program, the server window was closed. Run step 1 again.

The page also has a phone layout. To see it on this laptop, make the
browser window narrow: Zip and the buttons come first, then the
breadboard. (The server only answers on this laptop, so a real phone
cannot open it.)

---

## Part F: use a real board

Do this when the hardware teammate gives you a board with firmware on
it. Everything else (lessons, tutor, page) stays the same.

### Step 1. Find the board's COM port

Windows gives every USB serial device a name like `COM3`.

1. Plug the board into the laptop with its USB cable. (Some cables only
   charge and carry no data. If nothing below shows up, try another
   cable.)
2. Press the Windows key, type `Device Manager`, press Enter.
3. Click the arrow next to **Ports (COM & LPT)**.
4. You see a line like **USB-SERIAL CH340 (COM3)** or
   **Arduino Uno (COM4)**. The name in brackets is the port. Write it
   down.
5. To be sure: unplug the board. The line disappears. Plug it back in.
   It comes back.

You can also list the ports in Command Prompt (with `(.venv)` showing):

```
python -m serial.tools.list_ports -v
```

If **Ports (COM & LPT)** is missing, Windows needs a driver for the
board's USB chip. Ask the hardware teammate which chip it uses (often
CH340 or CP2102) and install that driver.

### Step 2. Check the board

1. **Close the Arduino IDE's Serial Monitor** if it is open. Only one
   program can use a COM port at a time.
2. In Command Prompt, in the project folder with `(.venv)` showing,
   run (use your port name):

   ```
   python tools\check_board.py --serial COM3
   ```

3. The last line should say `RESULT: PASS`. If it says `RESULT: FAIL`,
   send the whole output to the hardware teammate. They know what to do
   (docs/FIRMWARE_GUIDE.md).

### Step 3. Start the page with the real board

```
python web\server.py --fresh --serial COM3
```

If only one board is plugged in, `--serial auto` finds the port by
itself:

```
python web\server.py --fresh --serial auto
```

If there is more than one port, it lists them and stops. Run it again
with the right name.

Open http://localhost:8000 in Chrome or Edge. With a real board:

- Open **Teacher view** (top right): a green line says **Board
  connected on COM3 (115200 baud)**.
- If it turns red, **Board not answering on COM3**, the cable is loose
  or the board restarted. The student's side shows **No board** and "?"
  instead of numbers. Plug it back in. The page reconnects by itself
  within a few seconds.
- Practice mode and the pretend board's buttons are gone. The student
  uses the real board's buttons.

The terminal tutor works the same way:

```
python -m agent --fresh --serial COM3
```

If the firmware uses another speed than 115200, add `--baud` with that
number, for example `--baud 57600`. Both sides must use the same speed.

---

## Every time after the first

1. Press the Windows key, type `cmd`, press Enter.
2. Go to the project and turn on the Python folder:

   ```
   cd %USERPROFILE%\Documents\smart-board
   ```

   ```
   .venv\Scripts\activate.bat
   ```

3. Get your teammates' latest changes:

   ```
   git pull
   ```

4. If `requirements.txt` changed, run Step 6 again. Then run Part B,
   C, D, E or F.

---

## Problems

| You see                                         | Fix |
|-------------------------------------------------|-----|
| `'python' is not recognized`                    | Python was installed without "Add python.exe to PATH". Run the installer again, choose **Modify**, then **Next**, tick **Add Python to environment variables**, **Install**. Close and reopen Command Prompt |
| `No module named deepagents`                    | `(.venv)` is not showing. Run `.venv\Scripts\activate.bat` |
| `No module named agent` or `Ran 0 tests`        | You are in the wrong folder. Run the `cd ...\smart-board` command |
| `'git' is not recognized`                       | Do Step 2, then close and reopen Command Prompt |
| Error with `401` or `authentication` in Part D  | The key is wrong or has spaces. Run the `set` line again |
| The tutor says `This is not a mistake I know yet` | Normal for the "Not on the list" circuits. Otherwise the fake board may show a circuit for another lesson: type `/list` and `/build` one of those |
| `OSError: ... address already in use` (Part E)  | The server is already running in another window. Use that one, or run `python web\server.py --port 8001` and open http://localhost:8001 |
| `Cannot open COM3 ... Access is denied` (Part F) | Another program has the port: close the Arduino Serial Monitor, or the other server window (Ctrl+C), then try again |
| `No serial ports found` (Part F)                | The board is not plugged in, the cable carries no data, or the driver is missing. See Part F, Step 1 |
| `More than one serial port found` (Part F)      | Use the name instead of `auto`, for example `--serial COM3`. The message lists the ports |
| `did not send a hello line` (Part F)            | The firmware is not running or uses another speed. Press the board's reset button. Ask the hardware teammate to run `tools\check_board.py` |
