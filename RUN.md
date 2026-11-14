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

Wait about 10 seconds. The last lines should be:

```
Ran 46 tests in 5.5s

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
| `check my circuit`          | Tutor gives a hint about the LED's legs          |
| `check again`               | Same mistake twice: tutor now explains fully     |
| `/build no_resistor`        | Fake board now has no resistor                   |
| `check`                     | Tutor cuts the power and asks what limits current |
| `/build correct`            | Student fixes it                                  |
| `I fixed it`                | Tutor turns power back on and says it is correct. Because the LED went in backwards twice, it asks for lesson 1 once more |
| `check`                     | Correct again: tutor moves on to lesson 2         |
| `/build middle_floating`    | Probe row not connected                          |
| `check`                     | Tutor hints and lights up the probe row          |
| `/quit`                     | Stop                                              |

`/list` shows every circuit you can `/build` for the current lesson.

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
   | **LED backwards**, then **Check my circuit** | Current tile turns red. Tutor gives a hint about the legs |
   | **Check my circuit** again            | Same mistake twice: full explanation. History shows "2nd, Full explanation" |
   | **No resistor**, then **Check my circuit** | **POWER OFF** badge turns red, readings drop to 0 |
   | **Correct circuit**, then **I fixed it** | Power back on, all green. Tutor asks for lesson 1 once more |
   | **Check my circuit**                  | Page switches to lesson 2                      |
   | **Probe not connected**, then **Check my circuit** | Probe voltage jumps around. "Row 15 LED is lit" appears |

   Under each hint, "Agent used: ..." lists the tools the agent called.

5. **New student** (top right) forgets the history and starts again
   at lesson 1. Use it before each demo.
6. To stop: click the Command Prompt window and press **Ctrl+C**.

With `set ANTHROPIC_API_KEY=...` done first (Part D), the page uses
Claude. The bottom of the tutor box says which tutor is running.

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
| The tutor says `These readings don't match`     | The fake board circuit is for a different lesson. Type `/list` and `/build` one of those |
| `OSError: ... address already in use` (Part E)  | The server is already running in another window. Use that one, or run `python web\server.py --port 8001` and open http://localhost:8001 |
