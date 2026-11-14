# Lessons

For middle school students (ages 11 to 14). Every mistake has three
texts in `lessons/*.json`: a hint (first time), a full explanation
(second time and later), and a mini-lesson shown on the laptop: a
short idea with one everyday comparison, and a 3-choice question.
Student text is written at about a 6th grade reading level.

## Where every part goes (protocol version 2)

The board measures breadboard rows by name: `T1`..`T63` are rows 1 to 63,
holes a-e (top half). `PT+` and `PT-` are the top power strip. Both
lessons use only rows 5 to 15, so a board that measures just the first
16 rows (`T1`-`T16`) can check them. If a board does not measure a row
a lesson needs, the tutor says so plainly and does not guess.

**Lesson 1**, in build order:

| Step | Part | Legs |
|---|---|---|
| 1 | Red wire | `PT+` (5 V) -> row 5 (`T5`) |
| 2 | 220 ohm resistor | row 5 (`T5`) and row 10 (`T10`) |
| 3 | Red LED | long leg (+) in row 10 (`T10`), short leg (-) in row 12 (`T12`) |
| 4 | Black wire | row 12 (`T12`) -> `PT-` (ground) |

Correct voltages: `T5` = 5 V, `T10` = 2 V (the LED takes 2 V), `T12` = 0 V.

**Lesson 2**, in build order:

| Step | Part | Legs |
|---|---|---|
| 1 | Red wire | `PT+` (5 V) -> row 5 (`T5`) |
| 2 | Top 10k resistor | row 5 (`T5`) and row 10 (`T10`) |
| 3 | Bottom 10k resistor | row 10 (`T10`) and row 15 (`T15`) |
| 4 | Black wire | row 15 (`T15`) -> `PT-` (ground) |

Correct voltages: `T5` = 5 V, `T10` = 5 x 10 / (10 + 10) = 2.5 V, `T15` = 0 V.

## Current without a current sensor

When the board has no current sensor (`current_ma` is null), the
current is **calculated** from the voltage on both sides of a resistor
the lesson knows, and the page says "calculated":

- Lesson 1: (`T5` - `T10`) / 220 = (5 - 2) / 220 = 0.0136 A = 13.6 mA.
- Lesson 2: (`T5` - `T10`) / 10000 = 0.25 mA, and also
  (`T10` - `T15`) / 10000 = 0.25 mA. If these two answers disagree, one
  resistor is not what the lesson expects, so the current is shown as
  unknown instead of a wrong number.

A resistor with an end in an empty (floating) row carries no current.

**What a board without a current sensor cannot catch**, said plainly:

- **Resistor too big (lesson 1).** A 1k or 10k resistor still leaves
  the LED at about 2 V, so the rows look exactly like the correct
  circuit, and the calculated current (which assumes 220 ohm) says
  13.6 mA. The tutor will call it correct, even though the LED is dim.
  Only a current sensor can catch this one. The check reports it under
  `not_checked`.
- **No resistor (lesson 1)** is caught from the rows only because the
  power supply voltage drops when too much current flows (rows 5 and 10
  both sag below 4 V). If a power supply holds a steady 5 V, the rows
  look the same as an LED put in backwards. A current sensor, or a power
  supply with a current limit, is needed to be sure.
- **Not on the list: 150 ohm resistor** looks correct from the rows for
  the same reason as "resistor too big".

Every other mistake is found from row voltages and empty rows alone.
"LED backwards" keeps its two possible causes: the LED backwards, or its
long leg not in row 10. Row voltages cannot tell these apart either: in
both, row 10 sits at 5 V and row 12 at 0 V.

## Lesson 1: LED with resistor
- Circuit: 5 V → 220 ohm → red LED → ground
- Expected current: (5 - 2) / 220 = 0.0136 A = 13.6 mA
- Accept (rows): row 5 at least 4.5 V, row 10 between 1.6 and 2.4 V,
  row 12 at most 0.5 V, none of them empty, and current 10 to 17 mA
  (measured, or calculated as (row 5 - row 10) / 220).
- Mistakes to catch (rows: `T5`, `T10`, `T12`):
  - LED backwards OR the LED's long leg not in row 10: no current, so
    the resistor drops nothing and row 10 sits at 5 V; row 12 is 0 V.
    Both causes give the same row voltages, so the board cannot tell
    which one it is, and the texts say "may".
    Detect: row 10 at least 4.5 V and not empty, row 12 at most 0.5 V,
    current at most 1 mA → hint. Red lights: rows 10 and 12.
    Mini-lesson: "LEDs are one-way doors"
  - No resistor: the LED sits straight across the supply, current runs
    into the supply's limit and the supply voltage sags (rows 5 and 10
    both about 2.2 V) → cut power, explain why.
    Detect: supply at most 4 V, row 10 between 1 and 4 V, row 12 at
    most 0.5 V. Red lights: rows 5 and 10. (Without a current sensor
    this needs the supply to sag: see above.)
    Mini-lesson: "Resistors keep current small"
  - Open circuit (a part in the wrong row): row 10 holds only the LED's
    long leg, so nothing drives it.
    Detect: row 10 **empty**, row 5 at least 4.5 V, row 12 at most
    0.5 V → hint. Red light: row 10.
    Mini-lesson: "Circuits must be closed loops"
  - Resistor too big, for example 1k instead of 220 ohm. The LED still
    takes its 2 V, so the resistor gets the other 3 V:
    (5 - 2) / 1000 = 0.003 A = 3.0 mA. With a 10k:
    (5 - 2) / 10000 = 0.0003 A = 0.3 mA. Row 10 stays about 2 V and
    the LED is dim.
    Detect (**needs a current sensor**): measured current 0.1 to 9.5 mA,
    row 10 between 1.5 and 2.6 V → hint. Red lights: rows 5 and 10.
    Mini-lesson: "Bigger resistor, less current"
  - Current skips the LED: both LED legs in the same row, or a wire
    around the LED. Row 10 is joined to ground, so the resistor goes
    straight to ground: 5 / 220 = 0.0227 A = 22.7 mA (also calculated
    from the rows: (5 - 0) / 220). That is below the 30 mA safety cut,
    so it is not dangerous: hint, no cut.
    Detect: row 10 at most 0.5 V and not empty, row 12 at most 0.5 V,
    current 18 to 30 mA → hint. Red lights: rows 10 and 12.
    Mini-lesson: "Current takes the easy path"

## Lesson 2: Voltage divider
- Circuit: 5 V → 10k → middle (row 10) → 10k → ground
- Expected middle voltage: 5 x 10 / (10 + 10) = 2.5 V
- Expected current: 5 / 20000 = 0.00025 A = 0.25 mA
- Accept (rows): row 10 between 2.3 and 2.7 V and not empty, row 5 at
  least 4.5 V, row 15 at most 0.5 V.
- In this lesson's texts, "the measuring row" means row 10, where the
  board measures the middle.
- Mistakes to catch (rows: `T5`, `T10`, `T15`):
  - Bottom resistor is 1k: 5 x 1 / (10 + 1) = 0.45 V
    → hint about the sizes of the two resistors.
    Detect: row 10 between 0.3 and 0.6 V, not empty. Red: rows 10, 15.
    Mini-lesson: "Resistors share the voltage"
  - Measuring row not connected to the middle: the two resistors meet
    in another row, so row 10 is empty and its reading jumps around
    → hint to check the measuring row.
    Detect: row 10 **empty**. Red light: row 10.
    Mini-lesson: "Measure only what you touch"
  - Top resistor is 1k: middle = 5 x 10 / (1 + 10) = 4.55 V,
    current = 5 / 11000 = 0.00045 A = 0.45 mA.
    Detect: row 10 between 4.3 and 4.75 V, not empty → hint about the
    sizes. Red lights: rows 5 and 10.
    Mini-lesson: "Bigger resistor, bigger share"
  - Bottom resistor (or the ground wire) not connected: the loop is
    broken below the middle, so 0 mA. With no current the top resistor
    drops 0 V, so the middle keeps the full 5 V.
    Detect: row 10 at least 4.85 V, not empty. Red: rows 10 and 15.
    Mini-lesson: "No flow, no voltage drop"
  - Top resistor (or the 5 V wire) not connected: 0 mA, and the middle
    touches only ground (through the bottom resistor), so 0 V.
    Detect: row 10 at most 0.2 V, not empty. Red: rows 5 and 10.
    Mini-lesson: "Voltage needs a path from power"

An empty row and a row at 0 V read the same number, but the board
reports empty ("floating") rows separately, so "row 10 empty" and
"row 10 at 0 V" never mix. For the two "not connected" mistakes the
texts say "may": a missing resistor and a missing wire look alike.

Without a current sensor, lesson 2's current is calculated from both
resistors. It is only shown when both give the same answer (the correct
circuit: 0.25 mA). For the wrong-resistor and missing-resistor mistakes
they disagree, so the page shows the current as not known. None of the
lesson 2 mistakes needs the current to be found.

## When a mistake is not on the list

Then the tutor still explains, with a clue from the numbers
(`clue()` in agent/lessons.py). It compares every lesson row with its
expected voltage (lesson 1: row 5 = 5 V, row 10 = 2 V, row 12 = 0 V;
lesson 2: row 5 = 5 V, row 10 = 2.5 V, row 15 = 0 V) and, when a
current sensor measured it, the current. It says which number is off,
which way, and what that usually means, and lights those rows yellow
(careful). For example:

- An empty row: "Row 10 has nothing connected to it. A part that should
  reach row 10 is not touching it."
- A row too low or too high: "Row 10 has less voltage than it should.
  Less of the push from the power supply is reaching that row."
- Measured current under half: "The current is much lower than it
  should be. Something is holding it back more than the 220 ohm
  resistor would."

A calculated current is not used for the clue: it comes from the same
row voltages, so it adds nothing. The clue never names a specific fix,
because for an unknown mistake we do not know which part is wrong. Two
practice circuits in the simulator are on purpose not on any list:

- Lesson 1, a 150 ohm resistor: (5 - 2) / 150 = 0.020 A = 20 mA
  (above the 10 to 17 mA range, below the 30 mA safety cut). Rows alone
  look correct, so only a board with a current sensor gives the clue.
- Lesson 2, a 4.7k bottom resistor: middle = 5 x 4.7 / 14.7 = 1.60 V,
  current = 5 / 14700 = 0.00034 A = 0.34 mA. Row 10 is too low, so any
  board gives the clue.

## Lesson 3: ___
- Circuit: ___
- Expected values with calculation: ___
- Mistakes to catch: ___
