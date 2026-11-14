# Lessons

For middle school students (ages 11 to 14). Every mistake has three
texts in `lessons/*.json`: a hint (first time), a full explanation
(second time and later), and a mini-lesson shown on the laptop: a
short idea with one everyday comparison, and a 3-choice question.
Student text is written at about a 6th grade reading level.

## Lesson 1: LED with resistor
- Circuit: 5 V → 220 ohm → red LED → ground
- Expected current: (5 - 2) / 220 = 0.0136 A = 13.6 mA
- Accept range: 10 to 17 mA
- Mistakes to catch:
  - LED backwards OR the LED's long leg not in the resistor's row:
    current about 0 mA, 5 V at the LED row. Both give the same
    readings, so the board cannot tell which one it is.
    → hint about which way the LED faces and which row its legs are in.
    Mini-lesson: "LEDs are one-way doors"
  - No resistor: current hits the limit → cut power, explain why.
    Mini-lesson: "Resistors keep current small"
  - Open circuit: 0 mA and 0 V at the LED row → hint to check rows.
    Mini-lesson: "Circuits must be closed loops"
  - Resistor too big, for example 1k instead of 220 ohm. The LED still
    takes its 2 V, so the resistor gets the other 3 V:
    (5 - 2) / 1000 = 0.003 A = 3.0 mA. With a 10k:
    (5 - 2) / 10000 = 0.0003 A = 0.3 mA. The LED row stays about 2 V
    and the LED is dim.
    Detect: 0.1 to 9.5 mA, and LED row 1.5 to 2.6 V → hint.
    Mini-lesson: "Bigger resistor, less current"
  - Current skips the LED: both LED legs in the same row, or a wire
    around the LED. The resistor then goes straight to ground:
    5 / 220 = 0.0227 A = 22.7 mA, and the LED row is about 0 V. That is
    below the 30 mA safety cut, so it is not dangerous: hint, no cut.
    Detect: 18 to 30 mA, and LED row 0.5 V or less → hint.
    Mini-lesson: "Current takes the easy path"

## Lesson 2: Voltage divider
- Circuit: 5 V → 10k → middle node → 10k → ground
- Expected middle voltage: 5 x 10 / (10 + 10) = 2.5 V
- Expected current: 5 / 20000 = 0.00025 A = 0.25 mA
- Accept range: 2.3 to 2.7 V
- Mistakes to catch:
  - Bottom resistor is 1k: 5 x 1 / (10 + 1) = 0.45 V
    → hint about the sizes of the two resistors.
    Detect: middle 0.3 to 0.6 V, steady.
    Mini-lesson: "Resistors share the voltage"
  - Measuring row not connected to the middle: reading jumps around
    → hint to check the measuring row.
    Detect: middle moves by 1 V or more during one measurement.
    Mini-lesson: "Measure only what you touch"
  - Top resistor is 1k: middle = 5 x 10 / (1 + 10) = 4.55 V,
    current = 5 / 11000 = 0.00045 A = 0.45 mA.
    Detect: middle 4.3 to 4.75 V, steady → hint about the sizes.
    Mini-lesson: "Bigger resistor, bigger share"
  - Bottom resistor (or the ground wire) not connected: the loop is
    broken below the middle, so 0 mA. With no current the top resistor
    drops 0 V, so the middle keeps the full 5 V.
    Detect: 0.05 mA or less, middle 4.85 V or more, steady.
    Mini-lesson: "No flow, no voltage drop"
  - Top resistor (or the 5 V wire) not connected: 0 mA, and the middle
    touches only ground (through the bottom resistor), so 0 V.
    Detect: 0.05 mA or less, middle 0.2 V or less, steady.
    Mini-lesson: "Voltage needs a path from power"

"Steady" means the middle moves by less than 0.5 V during one
measurement, so these never mix with "measuring row not connected".
For the two "not connected" mistakes the board cannot tell a missing
resistor from a missing wire, so the texts say "may".

## When a mistake is not on the list

Then the tutor still explains, with a clue from the numbers
(`clue()` in agent/lessons.py). It compares each number with the
lesson's expected value (current 13.6 mA and LED row 2 V in lesson 1;
current 0.25 mA and middle 2.5 V in lesson 2) and says which one is
off, which way, and what that usually means. For example:

- Current under 10 % of expected: "Almost no current is flowing. That
  usually means the loop is broken somewhere."
- Current under half: "The current is much lower than it should be.
  Something is holding it back more than the 220 ohm resistor would."
- Measuring row near 0 V, or near the full 5 V, or jumping: a clue
  about the path to the power supply, the current, or the row not
  touching anything.

It never names a specific fix, because for an unknown mistake we do
not know which part is wrong. Two practice circuits in the simulator
are on purpose not on any list, to try this:

- Lesson 1, a 150 ohm resistor: (5 - 2) / 150 = 0.020 A = 20 mA
  (above the 10 to 17 mA range, below the 30 mA safety cut).
- Lesson 2, a 4.7k bottom resistor: middle = 5 x 4.7 / 14.7 = 1.60 V,
  current = 5 / 14700 = 0.00034 A = 0.34 mA.

## Lesson 3: ___
- Circuit: ___
- Expected values with calculation: ___
- Mistakes to catch: ___
