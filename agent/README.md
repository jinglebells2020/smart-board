# agent/

The teaching agent, built with deepagents (`create_deep_agent`).
How to run it: see RUN.md in the main folder.

| File            | What it does                                              |
|-----------------|-----------------------------------------------------------|
| `build.py`      | Builds the agent. Uses Claude if ANTHROPIC_API_KEY is set, otherwise the offline mock |
| `tools.py`      | The tools: read_rows, read_current, cut_power, restore_power, get_hint, light_row, clear_lights, show_on_board, log_mistake, next_lesson |
| `session.py`    | Rules that must never go wrong: safety, hints first, step-by-step building, the power button, picking the next lesson, saving history |
| `boardlink.py`  | The laptop's handle on any board (fake or real): what it has, lights and screen as last set, checks before sending |
| `buttons.py`    | What the 4 board buttons do (the meanings are in `protocol.py`) |
| `lessons.py`    | Checks a circuit from row voltages against lessons/*.json, calculates current when there is no sensor, and writes the clue for unknown mistakes |
| `prompts.py`    | The instructions Claude follows                           |
| `mock_model.py` | Offline stand-in for Claude, used in tests and without a key |
| `__main__.py`   | The chat in a terminal (`python -m agent`)                |

## Any board

The board says what it has in its `hello` line: which rows it measures,
whether it has a current sensor, a power switch, a screen, and how many
buttons. Everything here works with any answer:

- **No current sensor:** current is calculated from the voltage across
  a resistor the lesson knows, for example (5 - 2) / 220 = 13.6 mA, and
  marked "calculated". If two known resistors disagree, the current is
  "unknown" instead of a guess. Mistakes that need a real current
  sensor are listed under `not_checked`, and the tutor says so.
- **Not every row measured:** if a lesson needs a row the board does not
  measure, the check says so plainly (`missing_rows`) and does not guess.
- **No power switch:** see below.

## Safety does not depend on the AI

`session.measure()` (called by `read_rows` and `read_current`) acts at
once, before the AI answers:

- a measured current over 30 mA (`SAFETY_MAX_MA` in lessons.py) cuts
  power, when the board has a power switch;
- a dangerous mistake found from the rows (no resistor) cuts power too;
- on a board with **no power switch**, it puts "DANGER! Switch the power
  OFF by hand now" on the board screen. The tutor's first words then
  tell the student the same.

Turning power **on** has its own rules, also in plain Python
(`session.turn_power_on()`, the only way power is switched on):

- every time power comes on (button 4, the `restore_power` tool, a
  "fixed it" message) the circuit is measured at once, and power is cut
  again if it is still unsafe, with the reason ("still not safe: 100 mA
  flowed") on the laptop and the board screen;
- power only comes on when the student asks: button 4, or a message that
  says they fixed something or asks for power. `session.hear()` decides
  this from the student's own words before the AI sees them, and
  `restore_power` refuses otherwise. "Check my circuit" never turns
  power on.

## The hint button

Button 2 (and the `get_hint` tool) never measures, never changes power
and never adds a mistake to the history. It gives the next level of
help for the most recent mistake not yet fixed (after the hint comes the
full explanation), or, with no mistake, where the current build step
goes.

## Lights, screen and buttons

- Light colors always mean the same (`protocol.COLORS`): blue = put a
  part here, green = correct, red = problem here, yellow = careful.
  A mistake lights its rows red, a correct circuit lights all lesson
  rows green, an unknown mistake lights the rows the clue is about yellow.
- Screen text is always fitted to `SCREEN_LINES` x `SCREEN_CHARS` in
  `protocol.py` (4 x 20 for now) before it is sent.
- Buttons (`protocol.BUTTONS`, carried out in `buttons.py`):
  1 = check my circuit (asks the tutor), 2 = I need a hint (next level
  of help, no AI),
  3 = next step (lights the next part's rows blue and names the part
  on the screen; no AI), 4 = power off or on.

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
