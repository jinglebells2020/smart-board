# Genesis: Smart Breadboard: technical plan

This plan describes the hardware, the software architecture, the decisions
made so far, and the questions still open for Genesis, the smart breadboard.

**Where it comes from.** A separate, older repository
(`jinglebells2020/smartboard`, commit `16f512f`) holds an earlier design
for the same idea. It was read as reference only. Nothing from it (code,
files, figures, data) is copied into this repository. Everything below is a
summary in new words, plus how it fits what this repository already has.
Section 9 lists what that old repository contained, so it is clear what
must be built from scratch.

**Status of the old design.** Paper only. Nothing in it was built or
measured. Every size, range and speed below is an estimate until a bench
test confirms it.

---

## 1. The idea in one paragraph

A full-size breadboard sits in a 3D-printed frame. Every row is wired,
through a high-value resistor, to a set of analog switches, so one
microcontroller can read the voltage on every row. A light next to each row
lets the board point at a row. A power section supplies the breadboard,
measures its current and cuts it on a fault. The board reports everything
as JSON over USB to a laptop, where the AI tutor (in `agent/`) reads it,
decides what is wrong, and teaches.

## 2. Hardware

### 2.1 Breadboard and frame

- Keep the metal clips and the plastic block of an ordinary 63-row
  breadboard. A 3D printer cannot make clips that grip a lead for years.
- Remove the foam backing and the two side power rails, then seat the block
  in a printed frame. The frame leaves room for a strip of row lights on
  each side, and the rails go back on outside those lights.
- A printed plate under the block replaces the foam and stops clips being
  pushed out.
- Clone breadboards differ by fractions of a millimetre, so the frame must be
  modelled from measurements of the actual board, not from a datasheet.

### 2.2 Measuring every row

The problem: rows can see more than 5 V (12 V supplies, motors) or less than
0 V (op-amp circuits on split supplies). The analog switch chips only accept
signals between their own supply rails. One out-of-range row can corrupt the
readings of its neighbours on the same chip.

The planned answer:

- **A divider on every row.** A 1 MΩ resistor from the clip, and a 100 kΩ
  resistor from the switch input to a shared "bias" line, scale every row
  down by 11 before it reaches a chip. To the circuit, each row looks like a
  multimeter probe (about 1.1 MΩ).
- **Wiring without a circuit board.** Each 1 MΩ resistor is soldered to the
  back of its clip. The switch modules have pins on the same 2.54 mm spacing
  as the rows, so each resistor drops straight into the pin below it.
  Resistor packs (8 resistors with one common pin) supply the 100 kΩ side.
- **Switch tree.** CD74HC4067 16-channel analog switch modules: 8 for the
  rows, 1 for rails and reference voltages, 1 for the docked ESP32's pins,
  and 1 "root" switch that picks which of those reaches the measuring input.
  That is 11 chips.
- **Measuring.** A buffer amplifier (MCP6002) after the root switch feeds two
  converters: a precise but slow one (ADS1115, 16-bit) and the ESP32's own
  converter for fast, rough passes and waveforms.
- **Range.** The bias line is set by the firmware. At 0 V the range is about
  0 to +33 V. Raised to 1.5 V, it is about −15 to +18 V. One range at a time.
- **Floating rows.** A row with nothing in it reads 0 V, exactly like a
  grounded row.
  Moving the bias line tells them apart: an unconnected row follows the
  change completely, a driven row does not. One extra scan labels every row.
- **Calibration.** A one-time pass with every row tied to a known voltage
  stores each channel's exact ratio, so cheap 5 % resistor packs are fine.

Expected performance (estimates): about 1.5 mV resolution at the row a few
times per second, a rough full scan about 25 times per second, and
waveforms up to roughly 10 kHz on one row.

What it cannot do: measure the current through a single part, see which
rows are wired together, or see fast signals. With only one measuring path,
it can look at one row at a time but cannot drive one row and watch another.

### 2.3 Row lights

- Addressable RGB LED strip (WS2812B, 160 LEDs per metre, so 6.25 mm apart)
  runs beside the rows. With LEDs every 6.25 mm and rows every 2.54 mm, a
  single strip leaves many rows without an LED of their own.
- Three parallel strips, offset from each other by a third of the LED
  spacing, guarantee that no row is left without a light. This uses 156 LEDs, nearly a whole reel,
  so a spare reel is needed.
- A printed grid over the strips stops light leaking into the next row and
  turns each row's light into a narrow bar. It needs one filament change
  (light colour first, then black).
- A level-shifter gate (74AHCT125) lifts the ESP32's 3.3 V data signal to
  what the 5 V strip needs.
- Simpler fallback: one strip per side that points at a group of 2 to 3 rows.

### 2.4 Power and safety

- **Sources.** USB-C 5 V, a 12 V adapter, or later one small protected
  lithium cell. They are combined through diodes. A fuse sits right behind
  the 12 V input.
- **Rails for the student.** 3.3 V and 5 V always, plus an adjustable rail
  available only in bench mode with 12 V present.
- **Per rail:** a self-resetting fuse and a MOSFET to switch it, and the
  rail's voltage and current read by an INA3221 (a 3-channel sensor chip).
- **Separate safety controller.** A small ESP32-C3 owns the rail switches and
  reads the current sensor and two temperature sensors (battery, power
  stage). The rails stay off unless it is running. Protection therefore
  does not depend on the main firmware, which will be reflashed often.
- **Kid mode** (what it always starts in): only the 3.3 V and 5 V rails,
  each limited to roughly 200 mA. **Bench mode:** a physical jumper inside a screwed compartment; up to
  1 A, the adjustable rail and a relay.
- **Pre-power check.** A rail is first fed a very small test current while
  the rows are read, and only switched on fully if that looks normal. A dead short is found, and its row lit, before
  real power is applied.
- **Left out on purpose:** high-current racing LiPo packs, and kit parts
  containing mercury or a laser.

### 2.5 Interaction (later)

- A small OLED with four buttons, showing rail voltages, the current step and
  any fault.
- A speaker module for chimes. Speech is generated on the laptop and sent
  down.
- An offline voice-command module for fixed phrases such as "check it",
  "next", "help", "power off".
- A fixed colour meaning for row lights: blue "place it here", green correct,
  red fault, yellow caution.

### 2.6 Extras (later)

- **Dock** for a second ESP32 that the student programs. Its pins appear
  beside the breadboard and are measured too. The laptop can flash it and
  read its log.
- **Built-in parts** next to the breadboard: buttons, potentiometers, LEDs,
  buzzer, level shifter, servo headers, two loose probe leads, a bench-only
  relay.
- **Sensor ports** for the kit's three-pin modules, and an I²C port that can
  list the devices answering on it.
- **Further out:** a second switch tree to sense which rows are connected; a
  Raspberry Pi Zero 2 W so the station runs without a laptop; a camera arm.

### 2.7 Parts

Already owned, per the old notes: ESP32-S3 (N16R8) as main controller,
ESP32-C3 boards with small displays, 12 CD74HC4067 modules, a WS2812B reel,
LM2596 and MP1584EN converters, DS18B20 sensors, buttons, potentiometers,
OLED, speaker and voice modules, a 3D printer (Bambu P1S, no AMS).

To buy before the core works: about 150 × 1 MΩ resistors (1 %), about 24
bussed 100 kΩ resistor packs (9-pin), an ADS1115 module, 2 × MCP6002, a
74AHCT125, a second LED reel, plus M3 heat-set inserts and filament.
For the power section: an INA3221 module, P-channel MOSFETs, resettable
fuses, a 3 A fuse with holder, Schottky diodes, and later a cell charger and
5 V boost board.

## 3. Software architecture

```
 breadboard rows ──► dividers ──► switch tree ──► buffer ──► ADC ─┐
 LED strips ◄──────────────────────────────────────────────────┐  │
                                                     ESP32-S3 firmware
 power rails ◄── switches/fuses ◄── ESP32-C3 safety controller ──┘ (link)
                                                        │
                                       USB serial, one JSON object per line
                                                        │
 laptop:  board driver ── agent/ tools (deepagents) ── web/ page
          sim/fake_board.py can stand in for the board at any time
```

- **Main firmware (ESP32-S3).** Scans the rows (fast rough pass, then a slow
  precise pass), runs the floating-row test, captures a waveform on request,
  drives row lights by row number (it keeps the mapping from rows to LEDs,
  and a brightness correction for each LED), runs a self-test, and stores calibration.
  Blinking runs on the board so it does not depend on serial timing.
- **Safety firmware (ESP32-C3).** Small and rarely changed. Rail switches,
  current limits, temperatures, pre-power check.
- **Laptop side.** One driver module talks to the serial port and gives the
  agent plain Python calls. The tools in `agent/tools.py` call that driver.
  `sim/fake_board.py` implements the same interface, so every feature still
  runs without hardware (a rule in `CLAUDE.md`).

## 4. Decisions

"Old design" means it was decided in the reference repository. "Proposed"
means this plan suggests it for this repository and needs your OK.

| Topic | Decision | Why | Status |
|---|---|---|---|
| Breadboard | Real clips and block, printed frame around them | Printers cannot make good clips | Old design |
| Row sensing | 1 MΩ + 100 kΩ divider on every row, 11 analog switch chips, no custom PCB | Survives −15 to +33 V; buildable by hand | Old design |
| ADC | ADS1115 for accuracy, ESP32 ADC for speed | ESP32 ADC alone is too rough after ÷11 | Old design |
| Floating rows | Detect by moving the bias line | Tells "empty" from "0 V" | Old design |
| Row lights | 3 staggered strips per side + printed light grid | One LED per row without a custom board | Old design, pending a test print |
| Safety | Separate C3 controller, fuses, kid mode at 200 mA, physical bench key | Safety must not depend on often-flashed firmware | Old design |
| Main controller | ESP32-S3 N16R8 | Already owned; enough pins | Old design |
| Link | USB serial, one JSON object per line | Easy to debug by hand | Old design and this repo agree |
| AI side | Keep the deepagents tutor in `agent/`, not an MCP server | `CLAUDE.md` requires deepagents; the tutor already works | Proposed |
| Software safety cut | Keep the agent's 30 mA cut as a teaching check, on top of the hardware limits | Lessons draw under 17 mA; hardware stays the real protection | Proposed |
| FPGA | Not used | The old design does not need one; `docs/HARDWARE.md` mentions one | Needs your call |

## 5. What this means for the current repository

- **`docs/HARDWARE.md`.** The old design answers most blanks: ESP32-S3
  controller, USB serial, 5 V supply, about 200 mA per-rail limit enforced by
  a resettable fuse and the C3 controller, MOSFET power switches, INA3221
  for current, every row measured. Still blank: actual pin numbers, the
  baud rate to use, and the FPGA line.
- **`docs/SERIAL_PROTOCOL.md`.** Today it sends one probe voltage and the
  supply current. The hardware can measure every row. The protocol needs:
  - row names for both halves of the board (for example top and bottom row 12),
  - a voltage per row, a list of floating rows, current per rail,
  - a request/answer style with an id, so the laptop knows which reply
    belongs to which command.
  The simulator, agent and web page must then follow.
- **`lessons/*.json`.** `probe1_row` becomes unnecessary: a lesson can name
  the exact rows it expects at each voltage, and mistakes can be detected per
  row (for example "LED rows both at 5 V" means the LED is backwards).
- **`sim/fake_board.py`.** Must grow from one probe to every row, model
  floating rows, and model per-rail current and the hardware current limit.
- **`agent/`.** New tools follow from the hardware: read all rows, list
  floating rows, rail status, turn a rail on or off (the pre-power check runs
  first), light a row with a colour meaning.

## 6. Build order

After each step there is a working result to demo.

1. **Bench test on an unmodified breadboard.** A pin-header strip plugged into
   16 rows, 16 dividers from kit resistors, one switch chip, the ADS1115 and
   the ESP32-S3. Proves the measuring chain and its accuracy. Meanwhile:
   test-solder clips from a spare breadboard, print a 16-row light-grid test
   piece, measure the breadboard for the frame.
2. **Sensing core.** Frame, all row taps, switch tree, LED strips, firmware,
   USB power through a fuse. The tutor diagnoses a real circuit.
3. **Power and interaction.** Rail switches, current sensing, C3 safety
   controller, pre-power check, kid mode; display, speaker, voice.
4. **Dock and built-in parts.**
5. **Battery and guided projects.**

Shortcuts if time runs out: one LED strip per side; tap one half of the
board to begin with and add the other half later, since the firmware can
work with any number of switch chips.

## 7. Risks

| Risk | How to find out early | Fallback |
|---|---|---|
| Solder does not stick to the clips | Practise on 3 clips taken from a spare breadboard | Try another flux; or press spring pins against the clips instead |
| Switch modules too long to sit side by side | Plug two into one breadboard and see if they fit | Mount every other one lower |
| Light grid leaks light or prints badly | 16-row test print | Finer nozzle; one strip per side |
| 1.1 MΩ on every row upsets sensitive circuits | Bench test: an RC timer and a high-resistance divider, with and without the taps | Tell the tutor about it; larger resistors |
| Switch leakage adds an offset | Bench test comparing empty rows and rows tied to ground | Measure the offset and subtract it |
| Precise scan feels slow | Bench test | Use the fast pass for lights; faster ADC |

## 8. Open questions

1. **Deadline.** What date is the demo, and which build step must be done by
   then?
2. **FPGA.** `docs/HARDWARE.md` lists one; the old design has none. Drop it?
3. **AI link.** Keep only the deepagents tutor, or also offer an MCP server
   so Claude Desktop can use the board?
4. **Protocol change.** OK to replace the current one-probe protocol with
   per-row readings and request ids? It changes the simulator, agent, web
   page and tests.
5. **Light layout.** One LED per row (3 strips + light grid) or one per group
   of 2 to 3 rows (1 strip)? Depends on the test print.
6. **12 V adapter.** What current is it rated for?
7. **Battery.** Is there a single-cell charger board on hand, or must it be
   ordered? (Not needed until step 5.)
8. **Display module.** Is the HW-937 module the small OLED screen that has
   four buttons on the same board?
9. **Parts order.** The resistors, resistor packs, ADS1115, MCP6002 and
   74AHCT125 are needed for step 1. Who orders them, and by when?

## 9. What the old repository contained: code versus notes

Nothing from it was copied here. This shows what must be rebuilt from
scratch here, and what exists only as ideas.

| Old file | Kind | What it did | Here |
|---|---|---|---|
| `README.md` | Notes | Overview of the idea and of the repository | Summarised in this plan |
| `docs/DESIGN.md` | Plan | Sensing core: row taps, switch tree, ADC, row lights, serial protocol sketch, printed parts, risks | Summarised in sections 2, 3, 7 |
| `docs/STATION.md` | Plan | Power, safety, interaction, dock, parts list, build order, shopping list, open questions | Summarised in sections 2, 6, 8 |
| `tools/led_vernier.py` | **Code** (Python) | Calculates the LED-to-row assignment for the staggered strips, with each LED's offset from its row's centre | Must be rewritten if the 3-strip layout is chosen |
| `tools/make_figures.py` | **Code** (Python) | Draws the design diagrams as SVG files | Not needed; draw new figures only if wanted |
| `docs/led-map.json` | Generated data | Output of `led_vernier.py`: the LED-to-row table | Must be regenerated by the rewritten script |
| `docs/img/*.svg` (4 files) | Generated figures | Station layout, cross-section, LED lanes, block diagram | Not copied; redraw if needed |
| `.gitignore` | Config | Ignores Python cache and PlatformIO build folders | This repository has its own |

**Never existed in the old repository, so it all starts from zero:**

- ESP32-S3 sensing firmware (scan, floating test, waveform, lights,
  calibration, self-test, serial protocol)
- ESP32-C3 safety firmware
- the laptop's serial driver for the real board
- 3D models (frame, plate, light grid, cover)
- any wiring diagram or pin assignment beyond a rough pin count
- any tests or measurements

The old repository's only code was two small design-helper scripts. All
of its hardware and firmware exists as plans only.
