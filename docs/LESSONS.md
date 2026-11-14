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

## Lesson 2: Voltage divider
- Circuit: 5 V → 10k → middle node → 10k → ground
- Expected middle voltage: 5 x 10 / (10 + 10) = 2.5 V
- Expected current: 5 / 20000 = 0.00025 A = 0.25 mA
- Accept range: 2.3 to 2.7 V
- Mistakes to catch:
  - Bottom resistor is 1k: 5 x 1 / (10 + 1) = 0.45 V
    → hint about the sizes of the two resistors.
    Mini-lesson: "Resistors share the voltage"
  - Measuring row not connected to the middle: reading jumps around
    → hint to check the measuring row.
    Mini-lesson: "Measure only what you touch"

## Lesson 3: ___
- Circuit: ___
- Expected values with calculation: ___
- Mistakes to catch: ___
