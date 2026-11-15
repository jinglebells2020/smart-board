# Genesis: Smart Breadboard: lessons

For middle school students (ages 11 to 14). Every mistake has three
texts in `lessons/*.json`: a hint (first time), a full explanation
(second time and later), and a mini-lesson shown on the laptop: a
short idea with one everyday comparison, and a 3-choice question.
Student text is written at about a 6th grade reading level.

## The supply: 3.3 V (the station)

Every lesson runs from **3.3 V**, the station's breadboard supply
(`SUPPLY_V` in `protocol.py`, `docs/HARDWARE.md`). The numbers below are
for 3.3 V. Three things about the station shape the accept ranges:

- **Its meter tops out at about 2.9 to 3.1 V.** A row at or above that is
  sent as the supply, 3.3 V ("at the supply"). So a row joined to + must
  read **at least 2.8 V** (not 90 % of the supply: 2.97 V would sit inside
  the meter's top).
- **It is good to about 0.1 V.** Every range leaves at least 0.1 V on
  each side of the expected value (`tests/test_station.py` checks every
  lesson circuit with readings 0.1 V low, 0.1 V high and with the meter
  topping out at 2.9, 3.0 and 3.1 V).
- **"The + line is pulled down"** means below 0.85 × the supply: 2.8 V
  (`LOW_SUPPLY_SHARE` in `protocol.py`). A board that says another
  supply in its hello line (`"supply_v"`) is judged against its own.

**LED colours.** The lessons use **red, yellow or green** LEDs only. At
3.3 V a blue or white LED (about 3.0 V) barely lights, and two LEDs in a
row (about 3.8 V) cannot light at all. Free build and the random judge
circuits may still use any colour; the parts tray says so.

## Where every part goes (protocol version 2)

The board measures breadboard rows by name: `T1`..`T63` are rows 1 to 63,
holes a-e (top half). `PT+` and `PT-` are the top power strip. The
lessons use only rows 5 to 15, so a board that measures just the first
16 rows (`T1`-`T16`) can check them. If a board does not measure a row
a lesson needs, the tutor says so plainly and does not guess.

**Lesson 1**, in build order:

| Step | Part | Legs |
|---|---|---|
| 1 | Red wire | `PT+` (3.3 V) -> row 5 (`T5`) |
| 2 | 220 ohm resistor | row 5 (`T5`) and row 10 (`T10`) |
| 3 | Red LED | long leg (+) in row 10 (`T10`), short leg (-) in row 12 (`T12`) |
| 4 | Black wire | row 12 (`T12`) -> `PT-` (ground) |

Correct voltages: `T5` = 3.3 V, `T10` = 2 V (the LED takes 2 V), `T12` = 0 V.

**Lesson 2**, in build order:

| Step | Part | Legs |
|---|---|---|
| 1 | Red wire | `PT+` (3.3 V) -> row 5 (`T5`) |
| 2 | Top 10k resistor | row 5 (`T5`) and row 10 (`T10`) |
| 3 | Bottom 10k resistor | row 10 (`T10`) and row 15 (`T15`) |
| 4 | Black wire | row 15 (`T15`) -> `PT-` (ground) |

Correct voltages: `T5` = 3.3 V, `T10` = 3.3 x 10 / (10 + 10) = 1.65 V, `T15` = 0 V.

## Current without a current sensor

When the board has no current sensor (`current_ma` is null, like the
station), the current is **calculated** from the voltage on both sides of
a resistor the lesson knows, and the page says "worked out from the rows":

- Lesson 1: (`T5` - `T10`) / 220 = (3.3 - 2) / 220 = 0.0059 A = 5.9 mA.
- Lesson 2: (`T5` - `T10`) / 10000 = 0.165 mA, and also
  (`T10` - `T15`) / 10000 = 0.165 mA. If these two answers disagree, one
  resistor is not what the lesson expects, so the current is shown as
  unknown instead of a wrong number.

A resistor with an end in an empty (floating) row carries no current.

**What a board without a current sensor cannot catch**, said plainly:

- **Resistor too big (lessons 1, 3 and 4).** A 1k or 10k resistor still
  leaves the LED at about 2 V, so the rows look exactly like the correct
  circuit, and the calculated current (which assumes 220 ohm) says
  5.9 mA. The tutor will call it correct, even though the LED is dim.
  Only a current sensor can catch this one. The check reports it under
  `not_checked`.
- **Not on the list: 150 ohm resistor** looks correct from the rows for
  the same reason as "resistor too big".

### What the station cannot catch

- **No resistor.** It is found from the rows only because the + line
  sags when far too much current flows (rows 5 and 10 both drop below
  2.6 V; the fake board's supply has a 100 mA limit and sags to 2.2 V).
  The station's 3.3 V regulator may hold its voltage with one LED across
  it (a red LED takes roughly 90 mA): then the rows look the same as an
  LED put in backwards, and the tutor gives that hint. Only a current
  sensor, or a supply that sags, can tell them apart. (A hard short, a
  wire from + to -, resets the S3, so the laptop loses the board.)
- **Lesson 2, top resistor is 1k.** The middle is 3.3 x 10 / 11 = 3.0 V,
  right at the top of the station's meter. On a station whose meter tops
  out at 3.0 V or lower it reads as the full 3.3 V, the same as "bottom
  resistor not connected", and the tutor gives that hint. Below the top it
  reads about 3.0 V and is found as "top resistor is 1k".

Every other mistake is found from row voltages and empty rows alone.
"LED backwards" keeps its two possible causes: the LED backwards, or its
long leg not in row 10. Row voltages cannot tell these apart either: in
both, row 10 sits at 3.3 V and row 12 at 0 V.

## Lesson 0: How a breadboard connects

The STEM center also said kids struggle with "keeping the idea of what
lines of holes are connected in the breadboard". Lesson 0 comes before
lesson 1, and a new student starts there.

- Nothing to build (`"kind": "explore"`, an empty layout). The drawing
  shows rows 3 to 10 on both halves and the + and − lines.
- Tap any hole: every hole joined to it lights up yellow. That is its
  row half (holes a to e, or f to j) or a whole + or − line.
- The Learn card (`intro_lesson` in lessons/lesson0.json): "Which
  holes are joined?", with one question: "You put one leg in row 10,
  hole a. Which hole is joined to it?" (answer: row 10, hole e).
- The check passes once the student has answered the question (right
  or wrong: the card says why). Before that, Zip asks for the answer
  first. Then the student moves on to lesson 1, and `next_lesson`
  never sends anyone back to lesson 0 in the middle.
- Starting at a later lesson (`--lesson 1`, or `--demo`) **skips**
  lesson 0; it is never counted as passed. After the last building
  lesson Zip offers it: "You skipped lesson 0, How a breadboard
  connects. Let's do it now." Only when every lesson is really passed
  does Zip say "You passed every lesson".

## A loose leg (lessons 1 and 2)

The STEM center we talked to said kids struggle with "loose
connections". A leg that is only half pushed in touches its clip some
of the time. The laptop takes 10 readings for each check, so a loose
leg shows up as a row that is **empty in some readings and connected
in others**.

- Detect (`"any_flickering": true` in the lesson file): a row seen
  empty at least 2 times and connected at least 2 times in the same 10
  readings (`FLICKER_MIN = 2` in agent/lessons.py). One odd reading is
  not enough, so a single glitch is never called a loose leg.
- It is checked before every other mistake: while a leg is loose, the
  other numbers jump around and mean nothing.
- Light: that row **yellow** (careful), not red. Not dangerous: no
  power cut.
- Hint: "...Find the row with the yellow light and push that leg in
  firmly, all the way down."
- Mini-lesson: "Loose legs make blinking circuits".
- Fake board circuits: `loose_led_leg` (lesson 1) and
  `loose_resistor_leg` (lesson 2). Row 10 switches between the correct
  reading and empty, every other reading. They match only themselves.

## Lesson 1: LED with resistor
- Circuit: 3.3 V → 220 ohm → red LED → ground
- Expected current: (3.3 - 2) / 220 = 0.0059 A = 5.9 mA
- Accept (rows): row 5 at least 2.8 V, row 10 between 1.6 and 2.4 V,
  row 12 at most 0.5 V, none of them empty, and current 4 to 7.5 mA
  (measured, or calculated as (row 5 - row 10) / 220).
- Mistakes to catch (rows: `T5`, `T10`, `T12`):
  - LED backwards OR the LED's long leg not in row 10: no current, so
    the resistor drops nothing and row 10 sits at 3.3 V; row 12 is 0 V.
    Both causes give the same row voltages, so the board cannot tell
    which one it is, and the texts say "may".
    Detect: row 10 at least 2.8 V and not empty, row 12 at most 0.5 V,
    current at most 1 mA → hint. Red lights: rows 10 and 12.
    Mini-lesson: "LEDs are one-way doors". The hint and the drawing
    point at the LED's long leg (marked + in the drawing; the short leg
    side is drawn flat, like a real LED): `"point_at"` in the lesson
    file.
  - No resistor: the LED sits straight across the supply, current runs
    into the supply's limit and the supply voltage sags (rows 5 and 10
    both about 2.2 V) → cut power (on the station: "pull the red wire out
    of the + line"), explain why.
    Detect: supply at most 2.6 V, row 10 between 1 and 2.6 V, row 12 at
    most 0.5 V. Red lights: rows 5 and 10. (Without a current sensor
    this needs the supply to sag: see "What the station cannot catch".)
    Mini-lesson: "Resistors keep current small"
  - Open circuit (a part in the wrong row): row 10 holds only the LED's
    long leg, so nothing drives it.
    Detect: row 10 **empty**, row 5 at least 2.8 V, row 12 at most
    0.5 V → hint. Red light: row 10.
    Mini-lesson: "Circuits must be closed loops"
  - Resistor too big, for example 1k instead of 220 ohm. The LED still
    takes its 2 V, so the resistor gets the other 1.3 V:
    (3.3 - 2) / 1000 = 0.0013 A = 1.3 mA. With a 10k:
    (3.3 - 2) / 10000 = 0.00013 A = 0.13 mA. Row 10 stays about 2 V and
    the LED is dim.
    Detect (**needs a current sensor**): measured current 0.1 to 3.5 mA,
    row 10 between 1.5 and 2.6 V → hint. Red lights: rows 5 and 10.
    Mini-lesson: "Bigger resistor, less current"
  - Current skips the LED: both LED legs in the same row, or a wire
    around the LED. Row 10 is joined to ground, so the resistor goes
    straight to ground: 3.3 / 220 = 0.015 A = 15 mA (also calculated
    from the rows: (3.3 - 0) / 220). That is below the 30 mA safety cut,
    so it is not dangerous: hint, no cut.
    Detect: row 10 at most 0.5 V and not empty, row 12 at most 0.5 V,
    current 11 to 30 mA → hint. Red lights: rows 10 and 12.
    Mini-lesson: "Current takes the easy path"

## Lesson 2: Voltage divider
- Circuit: 3.3 V → 10k → middle (row 10) → 10k → ground
- Expected middle voltage: 3.3 x 10 / (10 + 10) = 1.65 V
- Expected current: 3.3 / 20000 = 0.000165 A = 0.165 mA
- Accept (rows): row 10 between 1.45 and 1.85 V and not empty, row 5 at
  least 2.8 V, row 15 at most 0.5 V.
- In this lesson's texts, "the measuring row" means row 10, where the
  board measures the middle.
- Mistakes to catch (rows: `T5`, `T10`, `T15`):
  - Bottom resistor is 1k: 3.3 x 1 / (10 + 1) = 0.3 V
    → hint about the sizes of the two resistors.
    Detect: row 10 between 0.17 and 0.5 V, not empty. Red: rows 10, 15.
    Mini-lesson: "Resistors share the voltage"
  - Measuring row not connected to the middle: the two resistors meet
    in another row, so row 10 is empty and its reading jumps around
    → hint to check the measuring row.
    Detect: row 10 **empty**. Red light: row 10. (On the station an
    empty row reads about 1.65 V with its float test on, the same as a
    correct middle: only the board's `floating` list tells them apart,
    and that is all Genesis uses.)
    Mini-lesson: "Measure only what you touch"
  - Top resistor is 1k: middle = 3.3 x 10 / (1 + 10) = 3.0 V,
    current = 3.3 / 11000 = 0.0003 A = 0.3 mA.
    Detect: row 10 between 2.75 and 3.2 V, not empty → hint about the
    sizes. Red lights: rows 5 and 10. (Near the top of the station's
    meter: see "What the station cannot catch".)
    Mini-lesson: "Bigger resistor, bigger share"
  - Bottom resistor (or the ground wire) not connected: the loop is
    broken below the middle, so 0 mA. With no current the top resistor
    drops 0 V, so the middle keeps the full 3.3 V.
    Detect: row 10 at least 3.2 V, not empty. Red: rows 10 and 15.
    Mini-lesson: "No flow, no voltage drop"
  - Top resistor (or the 3.3 V wire) not connected: 0 mA, and the middle
    touches only ground (through the bottom resistor), so 0 V.
    Detect: row 10 at most 0.12 V, not empty. Red: rows 5 and 10.
    Mini-lesson: "Voltage needs a path from power"

An empty row and a row at 0 V read the same number, but the board
reports empty ("floating") rows separately, so "row 10 empty" and
"row 10 at 0 V" never mix. For the two "not connected" mistakes the
texts say "may": a missing resistor and a missing wire look alike.

Without a current sensor, lesson 2's current is calculated from both
resistors. It is only shown when both give the same answer (the correct
circuit: 0.165 mA). For the wrong-resistor and missing-resistor mistakes
they disagree, so the page shows the current as not known. None of the
lesson 2 mistakes needs the current to be found.

## Lesson 3: More LEDs
- Circuit: 3.3 V → 330 ohm → red LED → ground, built 1 to 3 times
  anywhere on the board (`"copies"` in the lesson file). Each copy is
  checked on its own rows.
- Expected current for each copy: (3.3 - 2) / 330 = 0.0039 A = 3.9 mA.
  Three copies take about 12 mA, under the 30 mA cut.
- Accept: as lesson 1, with the current 3 to 5 mA for each copy (with a
  sensor, the board's total must be the number of copies times that).
- Mistakes: as lesson 1. Resistor too big (1k: 1.3 mA): measured
  current 0.1 to 2.9 mA for a copy. Current skips the LED:
  3.3 / 330 = 10 mA, detected at 7 to 30 mA.

## Lesson 4: Button switch
- Circuit: 3.3 V → push button → 220 ohm → red LED → ground. Two steps:
  "pressed" (hold the button and check) then "released" (let go and
  check).
- Pressed: rows 5 and 8 at least 2.8 V, row 13 between 1.6 and 2.4 V,
  row 15 at most 0.5 V, current 4 to 7.5 mA ((3.3 - 2) / 220 = 5.9 mA).
- Released: no current (at most 1 mA); rows 8 and 13 may be empty.
- Mistakes: button always on (current of 2 mA or more after letting go),
  button not connecting (no current while held), LED backwards (row 13
  at the full 3.3 V while held), no resistor (the + line sags below
  2.6 V).

## When a mistake is not on the list

Then the tutor still explains, with a clue from the numbers
(`clue()` in agent/lessons.py). It compares every lesson row with its
expected voltage (lesson 1: row 5 = 3.3 V, row 10 = 2 V, row 12 = 0 V;
lesson 2: row 5 = 3.3 V, row 10 = 1.65 V, row 15 = 0 V) and, when a
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

- Lesson 1, a 150 ohm resistor: (3.3 - 2) / 150 = 0.0087 A = 8.7 mA
  (above the 4 to 7.5 mA range, below the 30 mA safety cut). Rows alone
  look correct, so only a board with a current sensor gives the clue.
- Lesson 2, a 4.7k bottom resistor: middle = 3.3 x 4.7 / 14.7 = 1.06 V,
  current = 3.3 / 14700 = 0.00022 A = 0.22 mA. Row 10 is too low, so any
  board gives the clue.
