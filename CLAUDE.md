# Genesis: Smart Breadboard (HackPrinceton)

The app is called "Genesis". Everything a user sees calls it Genesis;
files and docs use the full name "Genesis: Smart Breadboard". The repository, folders,
Python modules and commands keep their names (smart-board).

## Goal
An electric circuit teaching board for middle school students (ages
11 to 14). The breadboard measures its own circuit. An AI agent reads
the measurements, cuts power on unsafe mistakes, and teaches with
hints. When the student makes a mistake, the board does not just say
"wrong": it explains, and the laptop shows a short mini-lesson (with
a one-question quiz) about that exact mistake.

## Hardware direction
The board measures many breadboard rows, has a lane of colored lights
beside the rows on each half, and a small screen with 4 buttons. Not
known yet: how many rows are wired, whether there is a current sensor,
whether anything can switch breadboard power, the exact screen size.
The software must work with any of those answers.

## Structure
- firmware/ : code for the board (not written yet). Speaks serial
  protocol version 2 (docs/SERIAL_PROTOCOL.md): a hello line listing
  what the board has, JSON readings with a voltage per measured row
  (T1..T63, B1..B63), floating rows, and current_ma (null without a
  current sensor). Commands: LED <row> <color>, LED CLEAR, SHOW,
  CUT, RESTORE. Sends a line when a button is pressed, and a
  power_cut line when it switched power off by itself.
  docs/FIRMWARE_GUIDE.md is for the hardware teammate.
- protocol.py : row names, light colors and meanings, what the 4
  buttons do, and the screen size (4 x 20 for now), in one place.
- agent/ : Python, built with LangChain deepagents
  (create_deep_agent). Tools: read_rows, read_current, cut_power,
  restore_power, get_hint, light_row, clear_lights, show_on_board,
  log_mistake, next_lesson. Subagent "detective" (task tool, only for
  unknown mistakes, at most two causes as questions). Subagent
  "lesson_writer" (same mistake again or quiz wrong: a new mini-lesson,
  graded by RubricMiddleware, then checked in Python). Memory: one
  notebook per student in notebooks/, the only place it may write.
  restore_power asks the student first (interrupt_on, human in the
  loop). agent/trace.py records each step for "How Zip worked".
  agent/fallback.py: if Claude fails, takes more than 25 s or there is
  no internet, the offline tutor answers that one message (the page
  says "Zip is in offline mode for this answer"); the next message
  tries Claude again. The server prints how long each Claude reply took.
  LangSmith tracing is optional and off by default: environment variables
  only (LANGSMITH_TRACING, LANGSMITH_API_KEY, LANGSMITH_PROJECT; RUN.md
  Part H); the server prints "LangSmith tracing: on (project ...)".
  python web/server.py --demo starts Maya at lesson 1, fresh; Teacher
  view has "Restart demo".
- web/index.html : one page UI, made for an 11-year-old (bright,
  friendly, never scary; our own guide "Zip" and colours, no other
  brand's look). Zip says the tutor's words (moods: happy, thinking,
  curious, careful), "The plan: where each part goes" draws the lesson's parts and
  live row lights and voltages, a lesson path shows stars, a
  mini-lesson card with a quiz after each mistake, and (with the fake
  board) a pretend board with its screen and 4 buttons. Practice mode,
  board status, tool trace and mistake history are in a Teacher drawer,
  closed by default. Design rules (palette, contrast, type, spacing) are
  checked by tests/test_page_design.py. The LED is drawn with a "+"
  by its long leg and a flat short-leg side. Every mini-lesson card has
  "Show the class": one full-screen slide, closed with Esc.
  An "Agent activity" panel for judges (agent/activity.py; closed for the
  student, opens with the Teacher view) lists the agents' last 50 actions
  live, with counts; it only reads what happens.
- lessons/*.json : where every part goes (layout), expected row
  voltages, and a hint, full explanation and mini-lesson for every
  mistake. Lesson 0 "How a breadboard connects" (kind "explore") has
  nothing to build: tap a hole to light every hole joined to it, and
  it passes once its question is answered. A new student starts there.
  Lessons 1 and 2 catch a loose leg (a row both empty and connected in
  one check: yellow light, "push that leg in firmly").
- sim/fake_board.py : simulates the board for testing, with or
  without a current sensor, power switch or screen, and any rows.
  sim/solver.py : a DC circuit solver (nodal analysis) so the fake
  board can read any circuit (NETLIST <json>, free build).
  sim/random_circuit.py : random judge circuits on usable rows,
  sometimes with a typical mistake; the true netlist never goes to the AI.
  sim/fake_port.py : a pretend serial port with the fake board on it.
- Usable rows (--usable-rows / SMART_BOARD_USABLE_ROWS, agent/boardlink.py):
  rows outside the list are never measured, never empty, never lit.
- Free build (--free-build, agent/lessons.py FREE_LESSON, id 5): after
  lesson 4, no plan and no PASS; the check reports the measured rows,
  current and plain facts. Parts tray: agent/tray.py, /api/tray.
  Phase 2 (agent/reader.py, agent/feedback.py): every free build check
  runs facts -> circuit_reader subagent (CircuitGuess) -> physics check
  (sim/solver.py, max 2 tries, best kept) -> feedback list (hint first,
  usable rows only). tools/eval_free_build.py measures accuracy.
  The reader gives up to 3 hypotheses (CircuitReading); Python keeps all
  that pass and ranks them (tray parts used, usual mistakes when no
  current flows). agent/planner.py picks the experiment that tells them
  apart (tool plan_experiment); after it, the hypothesis that came true is
  kept (result["hypothesis_kept"]). U1..U9 = rows the board cannot see.
  The physics check compares every usable row: every row with a voltage
  must be explained, and no joint between parts in a row that reads empty.
- agent/serial_board.py : SerialBoard, a real board on a USB serial
  port (pyserial), same methods as the fake board. Server and
  python -m agent take --serial <port|auto> and --baud.
- tools/check_board.py : checks a board's firmware against the
  protocol, PASS or FAIL per rule.

## Light colors (always the same meaning)
blue = put a part here, green = correct, red = problem here,
yellow = careful, off = light off.

## Board buttons
1 = check my circuit, 2 = I need a hint, 3 = next step,
4 = power off or on.

## Rules
- Read docs/TRACKS.md, docs/HARDWARE.md, docs/LESSONS.md first.
- Agent gives hints first, never the full answer on first try.
- Agent picks the next lesson from the logged mistake history.
- Every feature must work with sim/fake_board.py before hardware.
- Never send more text to the board screen than it holds.
- With no power switch, a dangerous mistake means telling the student
  to switch the power off by hand, on the laptop and the board screen.
- Every time power is turned on, measure at once and cut again if
  unsafe, and say why. Power only comes on when the student asks
  (button 4, or saying they fixed it). Enforced in plain Python.
- The hint button never measures, never changes power and never adds
  a mistake: it only gives the next level of help.
- A board with a current sensor and a power switch cuts power by
  itself above the limit and sends a power_cut line (firmware
  backstop; the laptop also cuts at 30 mA).
- Every firmware must pass tools/check_board.py.
- Never commit API keys. Read ANTHROPIC_API_KEY from environment.
- Keep README updated: target user, setup steps, demo script.
- I am a beginner. Explain every step I must do by hand.
