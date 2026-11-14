# agent/

The teaching agent, built with deepagents (`create_deep_agent`).
How to run it: see RUN.md in the main folder.

| File            | What it does                                              |
|-----------------|-----------------------------------------------------------|
| `build.py`      | Builds the agent. Uses Claude if ANTHROPIC_API_KEY is set, otherwise the offline mock |
| `tools.py`      | The tools: read_voltages, read_current, cut_power, restore_power, light_row_led, log_mistake, next_lesson |
| `session.py`    | Rules that must never go wrong: safety cut, hints first, picking the next lesson, saving history |
| `lessons.py`    | Compares readings with lessons/*.json                     |
| `prompts.py`    | The instructions Claude follows                           |
| `mock_model.py` | Offline stand-in for Claude, used in tests and without a key |
| `__main__.py`   | The chat in a terminal (`python -m agent`)                |

## Safety does not depend on the AI

`read_current` and `read_voltages` cut power themselves the moment
current goes over 30 mA (`SAFETY_MAX_MA` in lessons.py). The agent
also calls `cut_power`, but the board is already safe by then.

## Hints first

`log_mistake` decides what the agent may say. The first time a
mistake happens it returns the lesson's `hint`. From the second time
it returns `explain`, the full answer. Every time it also returns the
mistake's `mini_lesson` (the idea behind it, never the fix), which the
web page shows as a card with a quiz question. `Session.record_quiz`
saves the student's answer, right or wrong, in the history file; the
right answer always comes from the lesson file.

## How the next lesson is picked (session.py, `next_lesson`)

1. Lesson not passed yet → stay on it.
2. Same mistake 2+ times in this lesson → practise it once more.
3. Otherwise → the first lesson not passed yet.
4. All passed → review the lesson with the most mistakes.

The history is saved in `agent/student_history.json` (not committed).
