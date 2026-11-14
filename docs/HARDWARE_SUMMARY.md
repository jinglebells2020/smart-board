# Hardware summary

Every item is marked with exactly one status:

- **DECIDED**: you wrote or confirmed it. Sources: `CLAUDE.md`,
  `docs/HARDWARE.md`, `docs/LESSONS.md`.
- **SUGGESTED**: proposed but never confirmed by you. It comes from one of two
  sources, named in the Source column:
  - *Claude*: written by Claude in this repository. Code merged in PR #1
    counts here: merging was not a confirmation of each value.
  - *Old design*: the earlier `jinglebells2020/smartboard` design,
    summarised in `docs/TECH_PLAN.md`. Never built or measured.
- **UNKNOWN**: never discussed, or the template blank is still empty.

No values are guessed. Where a source names a part but gives no exact part
number, the part number is marked UNKNOWN.

Short summary: almost nothing about the hardware is DECIDED yet. Every blank
in `docs/HARDWARE.md` is still empty.

---

## 1. Boards

| Item | Status | Value | Source |
|---|---|---|---|
| Microcontroller model | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Microcontroller model | SUGGESTED | ESP32-S3-WROOM N16R8 board | Old design |
| Second microcontroller (safety supervisor) | SUGGESTED | ESP32-C3 board with small display; exact board model UNKNOWN | Old design |
| ESP32-S3 pins to avoid | SUGGESTED | GPIO 0, 3, 45, 46 (boot strapping); 19, 20 (USB); 35, 36, 37 (PSRAM); 43, 44 (UART behind a USB-C port); 2, 48 (on-board LEDs); 4, 5 (check for camera pull-ups first) | Old design |
| Microcontroller GPIO assignment | UNKNOWN | No pin was ever assigned to any function | — |
| FPGA model | UNKNOWN | `docs/HARDWARE.md` blank | — |
| FPGA job | UNKNOWN | `docs/HARDWARE.md` blank. The old design has no FPGA | — |
| FPGA ↔ microcontroller link (protocol, pins) | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Link to laptop: type | DECIDED | USB serial | `docs/HARDWARE.md`, `CLAUDE.md` |
| Link to laptop: baud rate | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Link to laptop: baud rate | SUGGESTED | 921600 baud | Old design |
| Which board talks to the laptop | UNKNOWN | Never stated in this repo | — |
| Which board talks to the laptop | SUGGESTED | The ESP32-S3, through the USB-C port wired to its UART (GPIO 43/44) | Old design |

## 2. Power

| Item | Status | Value | Source |
|---|---|---|---|
| Lesson circuit supply voltage | DECIDED | 5 V (both lessons in `docs/LESSONS.md` start from 5 V) | `docs/LESSONS.md` |
| Breadboard supply voltage (hardware) | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Simulator supply voltage | SUGGESTED | 5.0 V (`SUPPLY_V` in `sim/fake_board.py`, taken from the lessons) | Claude |
| Breadboard rails | SUGGESTED | 3.3 V and 5 V always; an adjustable rail only in bench mode with 12 V present | Old design |
| Input sources | SUGGESTED | USB-C 5 V; 12 V adapter behind a 3 A fuse; later one protected 3.7 V lithium cell (1000 or 1200 mAh). Joined through 3 A Schottky diodes (part number UNKNOWN) | Old design |
| Regulators | SUGGESTED | LM2596 (12 V → 5 V system rail); MP1584EN × 2 (3.3 V rail, adjustable rail) | Old design |
| Current limit value | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Current limit: simulator | SUGGESTED | 100 mA (`CURRENT_LIMIT_MA`, marked PLACEHOLDER in the code) | Claude |
| Current limit: software safety cut | SUGGESTED | 30 mA (`SAFETY_MAX_MA` in `agent/lessons.py`). The laptop sends `CUT` above this | Claude |
| Current limit: hardware | SUGGESTED | Kid mode about 200 mA per rail (default at power-up); bench mode up to 1 A per rail | Old design |
| Part that enforces the limit | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Part that enforces the limit | SUGGESTED | Resettable fuse per rail, 200 mA and 1 A ratings, exact part number UNKNOWN; plus an ESP32-C3 supervisor that trips the rail switch on overcurrent | Old design |
| Power cutoff: relay or MOSFET | UNKNOWN | `docs/HARDWARE.md` says "relay or MOSFET", not chosen | — |
| Power cutoff part | SUGGESTED | Logic-level P-channel MOSFET, one per rail (6 to buy). Exact part number UNKNOWN | Old design |
| Power cutoff control pin | UNKNOWN | Never assigned. Old design: driven by the ESP32-C3, pin not chosen | — |
| Bench-mode key | SUGGESTED | Physical jumper in a screwed compartment; without it no adjustable rail, no relay, low limits | Old design |
| Pre-power short check | SUGGESTED | Push a few mA into a rail and read the rows before switching it on | Old design |

## 3. Sensing

### Current

| Item | Status | Value | Source |
|---|---|---|---|
| Current sensor model | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Current sensor model | SUGGESTED | INA3221 module (3 channels: voltage and current on 3 rails) | Old design |
| Current sensor interface | SUGGESTED | Read by the ESP32-C3 supervisor. The old design puts its modules on a shared I²C bus; pins UNKNOWN | Old design |
| Current sensor pins | UNKNOWN | Never assigned | — |
| Shunt resistor value | UNKNOWN | Never discussed | — |
| What current is reported to the laptop | SUGGESTED | One value, `current_ma`: total current out of the supply | Claude (`docs/SERIAL_PROTOCOL.md`) |

### Voltage

| Item | Status | Value | Source |
|---|---|---|---|
| Part that reads voltages | UNKNOWN | Never stated in this repo | — |
| ADC (precise) | SUGGESTED | ADS1115 module, 16-bit, I²C. Under 1000 readings/s. About 1.5 mV resolution referred to the row (estimate) | Old design |
| ADC (fast) | SUGGESTED | ESP32-S3 internal ADC, for change detection and waveforms (up to about 10 kHz signal, estimate) | Old design |
| ADS1115 gain / input range setting | UNKNOWN | Never stated | — |
| Buffer amplifier | SUGGESTED | MCP6002 (DIP). One half buffers the measured signal, the other drives the bias line | Old design |
| Multiplexers | SUGGESTED | 11 × CD74HC4067 modules: 8 for rows, 1 for rails/references/probe leads, 1 for docked-board pins, 1 root. Run from 3.3 V | Old design |
| Measurable range at the row | SUGGESTED | 0 to +33 V (bias 0 V), or −15 to +18 V (bias 1.5 V). One range at a time | Old design (estimates) |
| Number of probe points | UNKNOWN | `docs/HARDWARE.md` template shows 2 lines, both blank | — |
| Number of probe points | SUGGESTED | 1 (`probe1`) in the protocol, simulator and lessons | Claude |
| Number of probe points | SUGGESTED | Every row node: 63 rows × 2 halves = 126, plus 4 or 8 rail nodes, ground and 3.3 V references, 2 probe leads, 16 docked-board pins | Old design |
| Which rows are probed | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Which rows are probed | SUGGESTED | Row 10 (lesson 1, LED anode) and row 15 (lesson 2, middle node). Marked as placeholders in `lessons/*.json` | Claude |
| Row → pin mapping | UNKNOWN | Never assigned in any source | — |
| Row → mux mapping | SUGGESTED | Mux leaves 0–3: rows 1–63, holes a–e. Leaves 4–7: rows 1–63, holes f–j. Exact channel per row UNKNOWN | Old design |
| Probe input divider / protection | UNKNOWN | Never discussed in this repo | — |
| Probe input divider / protection | SUGGESTED | 1 MΩ (1 %, 1/8 W preferred) in series at each clip, 100 kΩ (bussed 9-pin SIP network, 8 resistors) to a bias line: divide by 11. Each row sees about 1.1 MΩ | Old design |
| Floating-row detection | SUGGESTED | Step the bias line by about 1 V; floating rows follow it fully | Old design |
| Calibration | SUGGESTED | One-time, all rows tied to the 3.3 V rail with shorting combs | Old design |

## 4. Outputs

| Item | Status | Value | Source |
|---|---|---|---|
| Row indicator LEDs exist, addressed by row | DECIDED | Firmware command `LED <row>` | `CLAUDE.md` |
| How many row LEDs | UNKNOWN | `docs/HARDWARE.md` template shows 1 line, blank | — |
| Row LED → pin mapping | UNKNOWN | `docs/HARDWARE.md` blank | — |
| Row LED type and count | SUGGESTED | WS2812B strip, 160 LEDs/m (6.25 mm pitch). 3 staggered strips per side, 26 LEDs each: 156 LEDs. Simpler option: 1 strip per side | Old design |
| Row LED data pin | UNKNOWN | Never assigned. Old design: one data line through a 74AHCT125 level shifter, pin not chosen | — |
| How to turn a row LED off | UNKNOWN | No command defined | — |
| Display | UNKNOWN | Never discussed in this repo | — |
| Display | SUGGESTED | HW-937 module (believed to be a 0.96 inch 128×64 I²C OLED with 4 buttons; not confirmed). Option: 2.42 inch OLED | Old design |
| Speaker | SUGGESTED | DFRobot speaker module v2.0, driven from a controller pin | Old design |
| Buzzer | SUGGESTED | Passive buzzer (kit #18), for students' circuits | Old design |
| Voice input | SUGGESTED | DFRobot DF2301QG offline voice module, I²C | Old design |
| Laptop screen | DECIDED | One-page web UI `web/index.html` showing lesson, readings, hints | `CLAUDE.md` |

## 5. Parts available for lessons

| Item | Status | Value | Source |
|---|---|---|---|
| Inventory of resistors, LEDs, other parts | UNKNOWN | `docs/HARDWARE.md` blanks | — |
| Lesson 1 parts | DECIDED | 220 Ω resistor, 1 red LED | `docs/LESSONS.md` |
| Red LED forward voltage used in lesson math | DECIDED | 2 V | `docs/LESSONS.md` |
| Lesson 2 parts | DECIDED | 2 × 10 kΩ resistors. The listed mistake uses a 1 kΩ | `docs/LESSONS.md` |
| Lesson 3 | UNKNOWN | `docs/LESSONS.md` blank | — |
| Capacitors | UNKNOWN | Never discussed for lessons | — |
| Other parts owned | SUGGESTED | Old design lists kit items as owned: button modules (kit #29), rotary and slide potentiometers, loose LEDs, passive buzzer (kit #18), logic level shifter modules, SRD-05VDC relays, DS18B20 × 2 (kit #19), SD card reader (kit #41). Not confirmed in this project | Old design |

## 6. Division of logic

| Where | Status | What runs there | Source |
|---|---|---|---|
| FPGA | UNKNOWN | Never stated | — |
| Microcontroller | DECIDED | Print JSON readings on serial; accept `CUT`, `RESTORE`, `LED <row>` | `CLAUDE.md` |
| Microcontroller | SUGGESTED | Row scanning (fast and precise passes), floating-row test, waveform capture, row LEDs with brightness table, self-test, calibration | Old design |
| Safety supervisor (separate MCU) | SUGGESTED | ESP32-C3: rail switches, current limits, temperatures, pre-power check | Old design |
| Laptop | DECIDED | AI agent built with LangChain deepagents (`create_deep_agent`), tools `read_voltages`, `read_current`, `cut_power`, `light_row_led`, `log_mistake`, `next_lesson`; hints before answers; next lesson from mistake history; web UI | `CLAUDE.md` |
| Laptop | SUGGESTED | Extra tool `restore_power`; mistake detection and the 30 mA cut in plain Python (`agent/session.py`); model `claude-opus-5-5`; local web server `web/server.py` | Claude |

## 7. Data format between board and laptop

| Item | Status | Value | Source |
|---|---|---|---|
| Encoding | DECIDED | JSON readings over serial | `CLAUDE.md` |
| Commands | DECIDED | `CUT`, `RESTORE`, `LED <row>` | `CLAUDE.md` |
| Reading layout | SUGGESTED | One JSON object per line, every 100 ms: `type`, `t_ms`, `power_on`, `supply_v`, `current_ma`, `voltages.probe1`, `led_row`. Each command answered with an `ack` or `error` line | Claude (`docs/SERIAL_PROTOCOL.md`) |
| Alternative layout | SUGGESTED | Request/answer JSON with an `id`; commands such as `info`, `scan`, `range`, `scope`, `led`, `stream`; node names `T12` (row 12, a–e), `B12` (row 12, f–j), rails `PT+` `PT-` `PB+` `PB-`, dock `D0`–`D15` | Old design |

The two suggested layouts do not match. One must be chosen.

## 8. Open questions and known risks

### Open questions (all UNKNOWN until you answer)

| # | Question |
|---|---|
| 1 | Is there an FPGA? If yes: exact model, its job, and how it connects to the microcontroller (protocol and pins) |
| 2 | Exact microcontroller model |
| 3 | Baud rate, and which board connects to the laptop |
| 4 | Breadboard supply voltage(s) in hardware |
| 5 | Current limit value, and which part enforces it |
| 6 | Relay or MOSFET for the cutoff; exact part number; control pin |
| 7 | Current sensor model, interface and pins; shunt value |
| 8 | ADC model and input range; how many probe points; which rows; row-to-pin mapping; input protection |
| 9 | Row LED count, type and pins; how to turn them off |
| 10 | Display, buzzer or other outputs, if any |
| 11 | Parts on hand for lessons (resistor values, LEDs, capacitors), and lesson 3 |
| 12 | Which data layout to use (section 7) |
| 13 | Demo date, and which hardware must work by then |

### Known risks

| Risk | Status | Source |
|---|---|---|
| Simulator and lessons assume one probe point; hardware with per-row sensing needs the protocol, simulator, lessons, agent and web page changed | SUGGESTED | Claude |
| Simulator uses a 100 mA limit and the agent a 30 mA cut, neither checked against real hardware | SUGGESTED | Claude |
| Solder may not stick to breadboard clips | SUGGESTED | Old design |
| Mux modules may be too long to sit edge to edge (must be at most 40.64 mm) | SUGGESTED | Old design |
| Light grid over the LED strips may leak light or print badly at 0.5 mm walls | SUGGESTED | Old design |
| 1.1 MΩ on every row may disturb high-impedance circuits | SUGGESTED | Old design |
| Mux leakage may add an offset | SUGGESTED | Old design |
| ADS1115 precise scan may feel slow | SUGGESTED | Old design |
| A 4S racing LiPo can deliver over 100 A into a short; kept out of the station | SUGGESTED | Old design |
