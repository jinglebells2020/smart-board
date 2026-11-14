# Smart teaching breadboard (HackPrinceton)

## Goal
A breadboard that measures its own circuit. An AI agent reads the
measurements, cuts power on unsafe mistakes, and teaches with hints.

## Structure
- firmware/ : code for the board. Prints JSON readings on serial.
  Accepts commands: CUT, RESTORE, LED <row>.
- agent/ : Python, built with LangChain deepagents
  (create_deep_agent). Tools: read_voltages, read_current,
  cut_power, light_row_led, log_mistake, next_lesson.
- web/index.html : one page UI. Shows lesson, live readings, hints.
- lessons/*.json : expected voltages and currents per lesson.
- sim/fake_board.py : simulates the board for testing.

## Rules
- Read docs/TRACKS.md, docs/HARDWARE.md, docs/LESSONS.md first.
- Agent gives hints first, never the full answer on first try.
- Agent picks the next lesson from the logged mistake history.
- Every feature must work with sim/fake_board.py before hardware.
- Never commit API keys. Read ANTHROPIC_API_KEY from environment.
- Keep README updated: target user, setup steps, demo script.
- I am a beginner. Explain every step I must do by hand.
