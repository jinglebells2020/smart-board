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

Wait about 15 seconds. The last lines should be:

```
Ran 103 tests in 14.3s

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
| `check`                     | Tutor hints and lights up the measuring row      |
| `/build bottom_4k7`         | A mistake that is on no list (4.7k bottom resistor) |
| `check`                     | "This is not a mistake I know yet, but here is a clue." Then which numbers are off and what that usually means |
| `/quit`                     | Stop                                              |

`/list` shows every circuit you can `/build` for the current lesson.

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

4. Start the tutor:

   ```
   python -m agent --fresh
   ```

   The second line now says `Model: anthropic:claude-opus-5-5`
   instead of "offline practice tutor". Use the same steps as Part C.
   Replies are in Claude's own words now and take a few seconds.

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
4. Run the demo with the mouse:

   | Click                                 | What the audience sees                         |
   |---------------------------------------|------------------------------------------------|
   The buttons are on the right: **Check my circuit**, **I fixed it**,
   and under them the practice-mode buttons that pick what the pretend
   student built.

   | Click                                 | What the audience sees                         |
   |---------------------------------------|------------------------------------------------|
   | **LED backwards**, then **Check my circuit** | Current tile turns red ("NOT YET"). The tutor gives a hint. Under it a purple **Mini-lesson** card appears: "LEDs are one-way doors", a short idea, a question and 3 answer buttons |
   | The middle answer, **"The LED faces the wrong way, or a leg is not connected"** | It turns green, with "Right!" and one line saying why. In the mistake history, the Quiz column says "Right" |
   | **Check my circuit** again            | Same mistake twice: the full explanation, and a fresh card. Click the first answer: it turns red, the right one turns green, and "Not quite." plus why appears. History: "2nd, Full explanation, Wrong" |
   | **No resistor**, then **Check my circuit** | **POWER OFF** badge turns red, readings drop to 0. Card: "Resistors keep current small" |
   | **Correct circuit**, then **I fixed it** | Power back on, all green, the card goes away. Tutor asks for lesson 1 once more |
   | **Check my circuit**                  | Page switches to lesson 2                      |
   | **Measuring row not connected**, then **Check my circuit** | The voltage at the measuring row jumps around. "Row 15 LED is lit" appears at the top. Card: "Measure only what you touch" |

   Under each hint, a small grey line says what the tutor did, for
   example "The tutor measured the current, measured the voltages,
   saved the mistake".

   Each quiz takes one answer. A second click does not change it.

   More circuits to try. Pick one, then click **Check my circuit**:

   | Lesson | Button                         | What the tutor does                     |
   |--------|--------------------------------|-----------------------------------------|
   | 1      | **Resistor too big (1k)**      | Hint: the LED gets much less current, so it is dim. Card: "Bigger resistor, less current" |
   | 1      | **Current skips the LED**      | Hint: lots of current but 0 V at the LED row. No power cut (22.7 mA is safe). Card: "Current takes the easy path" |
   | 1      | **Not on the list: 150 ohm resistor** | No card. The tutor gives a clue: the current is a little higher than it should be |
   | 2      | **Wrong top resistor (1k)**    | Hint: the middle is much higher than half. Card: "Bigger resistor, bigger share" |
   | 2      | **Bottom resistor missing**    | Hint: no current, full 5 V in the middle. Card: "No flow, no voltage drop" |
   | 2      | **Top resistor missing**       | Hint: no current, 0 V in the middle. Card: "Voltage needs a path from power" |
   | 2      | **Not on the list: 4.7k bottom resistor** | No card. Clue: current a little higher, middle voltage lower than it should be |

5. **New student** (top right) forgets the history and starts again
   at lesson 1. Use it before each demo.
6. To stop: click the Command Prompt window and press **Ctrl+C**.

With `set ANTHROPIC_API_KEY=...` done first (Part D), the page uses
Claude. The top-left corner, under "Smart Breadboard", says which
tutor is running.

If the page shows a red bar saying it can't reach the server, the
server window was closed. Run step 1 again.

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
   C, D or E.

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
