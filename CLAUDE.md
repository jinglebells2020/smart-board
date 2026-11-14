# Smart teaching breadboard (HackPrinceton)

## Goal
An electric circuit teaching board for middle school students (ages
11 to 14). The breadboard measures its own circuit. An AI agent reads
the measurements, cuts power on unsafe mistakes, and teaches with
hints. When the student makes a mistake, the board does not just say
"wrong": it explains, and the laptop shows a short mini-lesson (with
a one-question quiz) about that exact mistake.

## Structure
- firmware/ : code for the board. Prints JSON readings on serial.
  Accepts commands: CUT, RESTORE, LED <row>.
- agent/ : Python, built with LangChain deepagents
  (create_deep_agent). Tools: read_voltages, read_current,
  cut_power, light_row_led, log_mistake, next_lesson.
- web/index.html : one page UI. Shows lesson, live readings, hints,
  and a mini-lesson card with a quiz after each mistake.
- lessons/*.json : expected voltages and currents per lesson, and a
  hint, full explanation and mini-lesson for every mistake.
- sim/fake_board.py : simulates the board for testing.

## Rules
- Read docs/TRACKS.md, docs/HARDWARE.md, docs/LESSONS.md first.
- Agent gives hints first, never the full answer on first try.
- Agent picks the next lesson from the logged mistake history.
- Every feature must work with sim/fake_board.py before hardware.
- Never commit API keys. Read ANTHROPIC_API_KEY from environment.
- Keep README updated: target user, setup steps, demo script.
- I am a beginner. Explain every step I must do by hand.
