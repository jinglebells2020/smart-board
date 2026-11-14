# Smart teaching breadboard (HackPrinceton)

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
  CUT, RESTORE. Sends a line when a button is pressed.
- protocol.py : row names, light colors and meanings, what the 4
  buttons do, and the screen size (4 x 20 for now), in one place.
- agent/ : Python, built with LangChain deepagents
  (create_deep_agent). Tools: read_rows, read_current, cut_power,
  restore_power, light_row, clear_lights, show_on_board, log_mistake,
  next_lesson.
- web/index.html : one page UI. Shows lesson, live readings, the rows
  the lesson uses with their lights, hints, a mini-lesson card with a
  quiz after each mistake, and (with the fake board) a pretend board
  with its screen and 4 buttons.
- lessons/*.json : where every part goes (layout), expected row
  voltages, and a hint, full explanation and mini-lesson for every
  mistake.
- sim/fake_board.py : simulates the board for testing, with or
  without a current sensor, power switch or screen, and any rows.

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
- Never commit API keys. Read ANTHROPIC_API_KEY from environment.
- Keep README updated: target user, setup steps, demo script.
- I am a beginner. Explain every step I must do by hand.
