# Genesis: Smart Breadboard — how to run it on Windows (step by step)

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

Wait about 2 minutes. The last lines should be:

```
Ran 593 tests in 80.0s

OK (skipped=1)
```

(The time is different on every computer. `skipped=1` is the page
layout check that needs a browser tool most laptops do not have; it is
fine. Plain `OK` is fine too.)

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
Lesson 0: How a breadboard connects (No circuit to build: ...)
learn> Which holes are joined? Under the holes of a breadboard ...
learn> You put one leg in row 10, hole a. Which hole is joined to it?
learn>   1. Row 10, hole e
learn>   2. Row 11, hole a
learn>   3. Row 10, hole f
learn> Type /answer 1, 2 or 3.
you>
```

A new student always starts at **lesson 0**: there is nothing to
build, only one question about which breadboard holes are joined. Type
`/answer 1` and press Enter, then type `check my circuit`. The tutor
says "You know how a breadboard connects!" and moves on to lesson 1.
(To skip lesson 0, start with `python -m agent --fresh --lesson 1`.)

Now type these lines one by one, pressing Enter after each. Lines
starting with `/` change the circuit on the fake board. The other
lines are what a student would say.

| You type                    | What happens                                     |
|-----------------------------|--------------------------------------------------|
| `/build led_backwards`      | Fake board now has the LED in backwards          |
| `check my circuit`          | Tutor gives a hint: look at which way the LED faces and which row its legs are in |
| `check again`               | Same mistake twice: tutor now explains fully     |
| `/build loose_led_leg`      | The LED's leg in row 10 is loose: the row keeps switching between connected and empty |
| `check`                     | Row 10 lights yellow and the tutor says "push that leg in firmly" |
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
   python -m agent --fresh --student Maya --lesson 1
   ```

   (`--lesson 1` skips lesson 0; leave it out to start there.)
   A line now says `Model: anthropic:claude-opus-5-5` instead of
   "offline practice tutor". Use the same steps as Part C. Replies are
   in Claude's own words now and take a few seconds. After each one a
   line says how long it took (`[14:05:12] Claude reply took 6.4 s`).
   If Claude fails, takes more than 60 seconds or the internet is down,
   the offline tutor answers that message and the tutor's reply is
   followed by `(Zip is in offline mode for this answer.)`. The next
   message tries Claude again.

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
   On a smaller laptop screen the page scrolls a little instead (scroll
   down to see the pretend board); nothing is squeezed or covered.

   A new student starts at **lesson 0, How a breadboard connects**.
   There is nothing to build:

   - **Tap any hole** in the drawing. Every hole joined to it lights up
     yellow, and a sentence under the drawing says why, for example
     "row 6, holes a to e: these 5 holes are joined by one metal clip".
     Tap a hole in the + or − line at the top or bottom: the whole line
     lights up. Tap the same hole again to turn it off. (With the
     keyboard: Tab to a hole, then Enter.)
   - Under Zip's words, click **Learn: Which holes are joined?**. The
     card has one question with 3 answers. Click one.
   - Click **Check my circuit**. Zip says "You know how a breadboard
     connects!" and the page moves to lesson 1. (If you click it before
     answering, Zip asks you to answer the question first.)

   To start straight at lesson 1, run
   `python web\server.py --fresh --lesson 1` instead.

   What you see: three parts that fill the screen.

   - **Top bar** (blue): the app name, the lesson name, the **lesson
     path** (a circle for each lesson, with 3 stars under it), the
     **Power on / Power off** sign and the **Teacher** button.
   - **The plan: where each part goes** (left, the big white area): a
     drawing of the lesson's rows with every part where the lesson puts
     it (wires, the resistor with its value, the LED with its long leg
     marked +). Under each row: its light (sky blue, green, red or
     yellow) and its voltage in small text. The current and the power
     voltage are small pills at the top. Under the drawing, the
     **Pretend board**: its little screen and its 4 buttons.
   - **Zip** (right): our robot spark guide, with the tutor's words in
     a big speech bubble (it scrolls only if a reply is longer than the
     space). Zip has 4 faces:
     **happy** (correct), **thinking** (while checking), **curious** (a
     hint or a question) and **careful** (danger or power off). Below:
     **Check my circuit** (blue), **I fixed it** (yellow) and a box to
     ask Zip anything, with **Send**.

4. Run the demo with the mouse. The pretend board's buttons work like
   the real board's buttons will. To choose what the pretend student
   built, click **Teacher** (top right): a drawer slides in over Zip.
   Click a circuit under **Practice mode**, then **Close** (or press
   Esc).

   | Click | What the audience sees |
   |---|---|
   | **3 · Next step** | The red wire is drawn strong with a dashed sky-blue outline, the other parts faint. Row 5's light turns **sky blue** (put a part here). Under the title: "Step 1 of 4: Red wire". Zip: "Put one end in the top + line (5 V) and the other end in row 5" |
   | **3 · Next step** three more times | Resistor (rows 5 and 10), LED ("long leg: row 10 / short leg: row 12"), black wire (row 12). Each time that part is outlined and its rows light sky blue |
   | **3 · Next step** once more | Lights off, all parts drawn. Screen: "All parts placed! / Press 1 to check." |
   | Teacher → **LED backwards**, Close, then **1 · Check my circuit** | Zip looks **thinking**, then **curious**. The lights of rows 10 and 12 turn **red** and gently pulse (red means "look here"). Zip gives a hint. Under Zip's words a tab appears: **Learn why: LEDs are one-way doors** (yellow book). Click it: the mini-lesson opens in Zip's panel, in place of Zip's picture, with a yellow top strip, a question and 3 big answers. The breadboard and Zip's words stay in view (on a phone it opens below Zip's words) |
   | (still LED backwards) look at the drawing | A yellow ring with the words "long leg (+)" points at the LED's long leg. The LED is always drawn with a **+** by its long leg and a flat side by its short leg, like a real LED |
   | **Show the class** (top right of the mini-lesson card) | The mini-lesson fills the whole screen as one slide: big title, the idea, a drawing of the lesson's parts, and the question with its 3 answers. Big enough to read from the back of the room. Press **Esc** (or click **Close**) to go back |
   | The middle answer, **"The LED faces the wrong way, or a leg is not connected"** | It turns green: "Right!" and why. Click **✕** to close the mini-lesson and Zip comes back; the **Learn why** tab opens it again |
   | **2 · I need a hint** | Zip, still **curious**, gives the next level of help: the full explanation. Nothing is measured, power is not touched |
   | Teacher → **No resistor**, Close, then **1 · Check my circuit** | Zip looks **careful** (a friendly yellow sign, nothing scary). The power sign says **Power off**. Mini-lesson: "Resistors keep current small" |
   | **4 · Power off / on** (before fixing) | Power comes on, is measured at once and goes off again. Zip: "The power went off again right away, because the circuit is still not safe: 100 mA flowed…" |
   | Teacher → **Correct circuit**, Close, then **I fixed it**, then **Yes** | Zip first asks **"Turn the power back on?"** with **Yes** and **No** (board button 4 also means Yes; No keeps the power off). After Yes, Zip turns **happy** and bounces, the lesson's rows glow green for a moment, a short burst of confetti falls (no sound), the LED in the drawing glows red, and lesson 1 on the path gets a green tick with its stars. Zip: "…This is the next lesson you have not passed yet." The page moves to lesson 2 |
   | **4 · Power off / on** (twice) | Power off (Zip careful), then on again (Zip happy) |

   **Instant help** (on by default; the "Instant help" switch under the
   buttons turns it off) makes Zip react as soon as the board changes,
   without Check. After a lesson is passed, the pretend board starts
   **empty** for the next lesson, like a student clearing the board, and
   nothing is checked until the board changes. To put a whole circuit in,
   pick it under **Practice mode** (for example **Correct circuit**).

   **The stars** (under each lesson on the path) come from what the
   student already did: 1 star for passing the lesson, a 2nd if no
   mistake happened twice in it, a 3rd if every mini-lesson question in
   it was answered right. A lesson passed with no mistakes gets all 3.
   New stars pop when they appear.

   Power only comes back on when the student asks: button 4, or saying
   they fixed it ("I fixed it", "I added the resistor"). "Check my circuit"
   while power is off just says how to turn it on. Every time power comes
   on, the circuit is measured at once and switched off again if unsafe.

   Each quiz takes one answer. A second click does not change it. A
   wrong answer, or the same mistake a second time, makes Zip ask its
   lesson writer for a new mini-lesson with a different comparison; if
   it passes the checks, the card says **Written by Zip for you**.

   **Teacher** (top right, closed when the page opens) slides in a
   drawer over Zip with everything for the grown-ups, including **How
   Zip worked**: every step of Zip's latest reply in plain words, taken
   from what really ran (helper steps carry a badge: detective, lesson
   writer, grader). Also: whether a board is connected, which
   tutor is running, **Practice mode**, **New student**, a link to the
   student's notebook, "What the tutor did" (for example "The tutor
   measured the rows, saved the mistake, cleared the lights, lit rows,
   wrote on the screen"), the earlier messages and the **Mistake
   history** table. Close it with **Close** or the Esc key.

   More circuits to try. In the Teacher drawer pick one, close it, then click
   **1 · Check my circuit**:

   | Lesson | Circuit | What the tutor does |
   |---|---|---|
   | 1 | **Loose LED leg** | Row 10 keeps switching between connected and empty while the board measures. It lights **yellow** (careful) and Zip says "push that leg in firmly". No power cut. Mini-lesson: "Loose legs make blinking circuits" |
   | 1 | **Part in wrong row** | Row 10 shows "empty" and lights red. Mini-lesson: "Circuits must be closed loops" |
   | 1 | **Resistor too big (1k)** | Hint: the LED gets much less current, so it is dim. Mini-lesson: "Bigger resistor, less current" |
   | 1 | **Current skips the LED** | Hint: lots of current but 0 V on row 10. No power cut (22.7 mA is safe). Mini-lesson: "Current takes the easy path" |
   | 1 | **Not on the list: 150 ohm resistor** | No mini-lesson. Zip gives a clue, then the detective's question |
   | 2 | **Wrong top resistor (1k)** | Hint: the middle is much higher than half. Mini-lesson: "Bigger resistor, bigger share" |
   | 2 | **Bottom resistor missing** | Hint: no current, full 5 V in the middle. Mini-lesson: "No flow, no voltage drop" |
   | 2 | **Top resistor missing** | Hint: no current, 0 V in the middle. Mini-lesson: "Voltage needs a path from power" |
   | 2 | **Loose resistor leg** | Like the loose LED leg, for the middle row (row 10) of lesson 2 |
   | 2 | **Not on the list: 4.7k bottom resistor** | No mini-lesson. Row 10 lights **yellow** (careful), Zip gives a clue and the detective's two questions |

   **Try a different board.** The real board's details are not decided
   yet. Stop the server (Ctrl+C in its window) and start it with any of
   these, then open the page again:

   ```
   python web\server.py --fresh --no-current --no-power-switch --rows T1-T16
   ```

   The Teacher drawer now says "Board: 16 rows measured, current sensor no,
   power switch no". Both lessons still work. The current pill says
   "worked out from the rows". Pick **No resistor**, then
   **1 · Check my circuit**: Zip says "Switch the power off by hand
   right now", and the board screen says "DANGER! Switch the power OFF by
   hand now." **4 · Power off / on** says the board cannot switch power.
   With `--no-current`, **Resistor too big (1k)** looks correct, and the
   tutor says plainly that this board cannot measure current, so it could
   not check for it.

5. **New student** (in the Teacher drawer) forgets the history and the stars
   and starts again at lesson 0, with an empty notebook. (The old
   notebook is kept in the `notebooks` folder with the date in its
   name.) **Restart demo**, next to it, starts the demo again: Maya at
   lesson 1 with a fresh history and notebook (Part G).
6. To see the student's notebook, open the Teacher drawer and click **Open
   the student's notebook** (or go to http://localhost:8000/api/notebook
   in a second tab), and press F5 to refresh it after a check. To give
   the notebook a name, start the server with `--student Maya`.
   `--fresh` forgets the history but keeps the notebook.
7. To stop: click the Command Prompt window and press **Ctrl+C**.

With `set ANTHROPIC_API_KEY=...` done first (Part D), the page uses
Claude. The Teacher drawer says which tutor is running. If Claude fails,
takes more than 60 seconds, or the internet is down, that one answer
comes from the offline tutor and the page says **"Zip is in offline mode
for this answer"** under Zip's words; the next message tries Claude
again (Part G, step 5).

If the page shows a yellow bar saying Zip cannot reach the tutor
program, the server window was closed. Run step 1 again.

The page also has a phone layout. To see it on this laptop, make the
browser window narrow: the top bar, then Zip and the buttons, then
the plan with the breadboard. (The server only answers on this laptop, so a real phone
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

- Click **Teacher** (top right): a green line says **Board
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

## Part G: the demo (one command, safe if the internet fails)

Use this for judges or a class.

1. Open Command Prompt in the project folder with `(.venv)` showing
   (see "Every time after the first" below). For Claude's own words,
   also run the `set ANTHROPIC_API_KEY=...` line from Part D. Without a
   key the demo still works, with the offline tutor's fixed sentences.
2. Start the demo:

   ```
   python web\server.py --demo
   ```

   This starts fresh: the student is **Maya**, at **lesson 1**, with an
   empty mistake history and a new notebook (her old notebook is kept
   in the `notebooks` folder with the date in its name). The window
   prints:

   ```
   DEMO READY: student Maya, lesson 1, fresh start.
   Demo URL: http://localhost:8000   (press F11 in the browser for full screen)
   ```

3. Open Chrome or Edge, go to **http://localhost:8000**, press **F11**.
   Follow the demo script in the README ("Demo script" or "LangChain
   demo (60 seconds)").
4. **Leave the black window where you can see it.** With a key, every
   Claude reply adds one line with how long it took, for example:

   ```
   [14:05:12] Claude reply took 6.4 s
   ```

5. **If the internet drops, or Claude takes more than 60 seconds**, the
   demo keeps going: that one answer comes from the offline tutor, and
   under Zip's words a small grey note says **"Zip is in offline mode
   for this answer"**. The black window says why, for example:

   ```
   [14:06:40] Claude failed after 0.5 s (could not reach Claude: is the internet connected?). The offline tutor answered this message; the next one tries Claude again.
   ```

   Just keep going: the next message tries Claude again by itself. You
   do not need to restart anything. Power cuts, "power on only when
   you ask", measuring right after power comes on, and the hint button
   never need Claude: they always work.
6. **To start the demo over** (for the next judge): click **Teacher**
   (top right) → **Restart demo** → **OK**. Maya is back at lesson 1
   with a fresh history and notebook, the pretend board has a correct
   circuit again, and the power is on. Then **Close**.
7. To stop: click the black window and press **Ctrl+C**.

`--demo` works with the other options too, for example
`python web\server.py --demo --serial COM3` for a real board, or
`--port 8001` if port 8000 is busy.

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
   C, D, E, F or G.

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
| "Zip is in offline mode for this answer" under Zip's words | Claude did not answer in time, or the internet is down. The black window says why. Keep going: the next message tries Claude again. If it says `did not accept the API key`, run the `set` line from Part D again and restart the server |
| `OSError: ... address already in use` (Part E)  | The server is already running in another window. Use that one, or run `python web\server.py --port 8001` and open http://localhost:8001 |
| `Cannot open COM3 ... Access is denied` (Part F) | Another program has the port: close the Arduino Serial Monitor, or the other server window (Ctrl+C), then try again |
| `No serial ports found` (Part F)                | The board is not plugged in, the cable carries no data, or the driver is missing. See Part F, Step 1 |
| `More than one serial port found` (Part F)      | Use the name instead of `auto`, for example `--serial COM3`. The message lists the ports |
| `did not send a hello line` (Part F)            | The firmware is not running or uses another speed. Press the board's reset button. Ask the hardware teammate to run `tools\check_board.py` |
