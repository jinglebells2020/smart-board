# Genesis: Smart Breadboard: hardware summary

Every item is marked with exactly one status:

- **DECIDED**: decided and built. The source is either this repository
  (`CLAUDE.md`, `docs/LESSONS.md`) or **the station**,
  `jinglebells2020/smartboard`: the hardware, CAD and firmware Genesis now
  runs on (`docs/HARDWARE.md` has the details).
- **SUGGESTED**: proposed but never confirmed. Sources: *Claude* (written by
  Claude in this repository) or *Old design* (the earlier smartboard design
  summarised in `docs/TECH_PLAN.md`, never built in that form).
- **UNKNOWN**: not decided yet.

Short summary: the station decided almost everything. A 3.3 V breadboard
supply, every row measured, no current sensor, no power switch, a StickS3
screen and four coloured buttons. The old design's 5 V and adjustable rails,
ESP32-C3 supervisor, INA3221 current sensor, ADS1115 ADC and rail switches
are **not** in the station (`docs/TECH_PLAN.md` marks where).

---

## 1. Boards

| Item | Status | Value | Source |
|---|---|---|---|
| Microcontroller model | DECIDED | ESP32-S3-WROOM-1 N16R8 dev board | `jinglebells2020/smartboard` |
| Second microcontroller (safety supervisor) | DECIDED | None. The old design's ESP32-C3 supervisor is not built | `jinglebells2020/smartboard` |
| Screen board | DECIDED | M5Stack StickS3 in the front lid, own battery, reached by radio (ESP-NOW) | `jinglebells2020/smartboard` |
| FPGA | DECIDED | None | `jinglebells2020/smartboard` |
| Microcontroller GPIO assignment | DECIDED | SIG 1; leaf S0-S3 4-7; root S0-S2 11-13; float injector 14; pot 8; LED lanes 15 (T), 16 (B); buttons 17, 18, 21, 47; RGB 38, 39, 40 (`docs/HARDWARE.md`) | `jinglebells2020/smartboard` |
| Link to laptop: type | DECIDED | USB serial on the S3's native USB port, vendor id 0x303A (`--serial auto` prefers it) | `jinglebells2020/smartboard` |
| Link to laptop: baud rate | DECIDED | Does not matter on the native USB port; Genesis uses 115200 (`DEFAULT_BAUD`) | `jinglebells2020/smartboard`, `protocol.py` |
| Station firmware | DECIDED | `firmware/smartboard_s3`, Genesis mode at boot (protocol version 2), station JSON mode when a line starts with `{` | `jinglebells2020/smartboard` |

## 2. Power

| Item | Status | Value | Source |
|---|---|---|---|
| Breadboard supply voltage | DECIDED | **3.3 V only**, from the S3 board's regulator (the mux chips take nothing outside 0-3.3 V) | `jinglebells2020/smartboard` |
| Lesson circuit supply voltage | DECIDED | 3.3 V for every lesson (`SUPPLY_V` in `protocol.py`; was 5 V) | `docs/LESSONS.md` |
| Simulator supply voltage | DECIDED | 3.3 V by default, `--supply` for any other | `sim/fake_board.py` |
| What powers the station | DECIDED | The laptop's USB-C cable powers everything | `jinglebells2020/smartboard` |
| Power switch | DECIDED | None (`has_power_switch: false`). Unplugging USB is the only off switch; for the breadboard alone, pull the red wire out of the + line | `jinglebells2020/smartboard` |
| Current limit: hardware | DECIDED | None of its own. A hard short resets the S3 | `jinglebells2020/smartboard` |
| Current limit: simulator | SUGGESTED | 100 mA (`CURRENT_LIMIT_MA`, marked PLACEHOLDER). At 3.3 V an LED alone across the supply stays under it | Claude |
| Current limit: software safety cut | SUGGESTED | 30 mA (`SAFETY_MAX_MA` in `agent/lessons.py`), used only with a current sensor | Claude |
| Low supply rule | SUGGESTED | The + line below 0.85 × the supply (2.8 V at 3.3 V) means a big current (`LOW_SUPPLY_SHARE` in `protocol.py`) | Claude |
| Rail watch | DECIDED | The S3 watches the rail taps and warns (red blinking light, "Check for a short") when the + line drops | `jinglebells2020/smartboard` |

## 3. Sensing

### Current

| Item | Status | Value | Source |
|---|---|---|---|
| Current sensor | DECIDED | None (`has_current: false`, `current_ma` null). Genesis calculates the current from a lesson resistor's two rows | `jinglebells2020/smartboard` |

### Voltage

| Item | Status | Value | Source |
|---|---|---|---|
| Part that reads voltages | DECIDED | The S3's own ADC, attenuation 11 dB | `jinglebells2020/smartboard` |
| Multiplexers | DECIDED | 9 × CD74HC4067: 8 leaves and 1 root, all at 3.3 V | `jinglebells2020/smartboard` |
| Rows measured | DECIDED | Every row: 126 row nodes (T1-T63, B1-B63) plus 2 rail taps (PT+, PB+). PT- and PB- are ground, not measured | `jinglebells2020/smartboard` |
| Measurable range | DECIDED | 0 to about 2.9-3.1 V; at or above that the firmware sends the supply ("at the supply") | `jinglebells2020/smartboard` |
| Accuracy | DECIDED | About ±0.05 to 0.1 V after calibration. Lesson ranges allow ±0.1 V and saturation | `jinglebells2020/smartboard` |
| Floating-row detection | DECIDED | 1 MΩ injector (GPIO14): a floating row reads about 1.65 V with it on, a driven row barely moves | `jinglebells2020/smartboard` |
| Row → mux channel map | DECIDED | Made from the mux test's `as_built_map.json` by `tools/make_fw_maps.py`; until then the firmware is "unmapped" (no T/B names) | `jinglebells2020/smartboard` |

## 4. Outputs

| Item | Status | Value | Source |
|---|---|---|---|
| Row lights | DECIDED | WS2812B, one strip per side, 26 LEDs each (52); 25 of 63 rows have their own light (2 5 7 10 12 15 17 20 22 25 27 29 32 34 37 39 42 44 47 49 52 54 57 59 61) | `jinglebells2020/smartboard` |
| A row with no light of its own | DECIDED | The nearest lit rows on both sides light (never more than 3 rows apart) | `jinglebells2020/smartboard` |
| LED current cap | DECIDED | 150 mA total, in the firmware | `jinglebells2020/smartboard` |
| Light colours and meanings | DECIDED | blue, green, red, yellow, off (`COLORS` in `protocol.py`) | `CLAUDE.md` |
| Board screen | DECIDED | StickS3: a big row name plus text; 4 × 20 fits easily (`SCREEN_LINES`, `SCREEN_CHARS`) | `jinglebells2020/smartboard` |
| Buttons | DECIDED | Yellow 1 check (GPIO17), white 2 hint (18), blue 3 next step (21), red 4 power (47); StickS3 orange key = 1 | `jinglebells2020/smartboard` |
| Knob and RGB status light | DECIDED | Exist (pot on GPIO8, RGB on 38-40); Genesis has no use for them yet | `jinglebells2020/smartboard` |
| Laptop screen | DECIDED | One-page web UI `web/index.html` | `CLAUDE.md` |

## 5. Parts available for lessons

| Item | Status | Value | Source |
|---|---|---|---|
| Lesson 1 parts | DECIDED | 220 Ω resistor, 1 red LED: (3.3 - 2) / 220 = 5.9 mA | `docs/LESSONS.md` |
| Lesson 2 parts | DECIDED | 2 × 10 kΩ: the middle reads 1.65 V. The listed mistakes use a 1 kΩ | `docs/LESSONS.md` |
| Lesson 3 parts | DECIDED | 330 Ω resistor and a red LED, 1 to 3 copies: 3.9 mA each | `docs/LESSONS.md` |
| Lesson 4 parts | DECIDED | Push button, 220 Ω resistor, red LED | `docs/LESSONS.md` |
| Red LED forward voltage used in lesson math | DECIDED | 2 V | `docs/LESSONS.md` |
| LED colours in lessons | DECIDED | Red, yellow or green only: at 3.3 V a blue or white LED barely lights | `docs/LESSONS.md` |
| Inventory beyond the lessons | UNKNOWN | Not listed | — |

## 6. Division of logic

| Where | Status | What runs there | Source |
|---|---|---|---|
| ESP32-S3 | DECIDED | Row scanning, floating-row test, rail watch, row lights, buttons, radio to the StickS3, Genesis protocol mode and station JSON mode | `jinglebells2020/smartboard` |
| StickS3 | DECIDED | Shows the screen text and sends its orange key | `jinglebells2020/smartboard` |
| Laptop | DECIDED | Genesis: the deepagents tutor, lessons, mistake checks and safety rules in plain Python, the web page | `CLAUDE.md` |

## 7. Data format between board and laptop

| Item | Status | Value | Source |
|---|---|---|---|
| Genesis protocol | DECIDED | Serial protocol version 2 (`docs/SERIAL_PROTOCOL.md`), checked by `tools/check_board.py`. The station adds `"supply_v": 3.3` to its hello line | this repository, `jinglebells2020/smartboard` |
| Station's own protocol | DECIDED | Request/answer JSON with an `id` (`docs/PROTOCOL.md` in the smartboard repo), used by its own laptop tools; the firmware switches to it when a line starts with `{` | `jinglebells2020/smartboard` |

## 8. Open questions and known risks

### Open questions

| # | Question |
|---|---|
| 1 | The real row map: it exists once the mux test is done and `make_fw_maps.py` has been run and flashed |
| 2 | Demo date, and which lessons must work by then |

### Known risks

| Risk | Status | Source |
|---|---|---|
| With no current sensor, a missing resistor reads like a backwards LED unless the + line sags; the station's regulator may hold 3.3 V (`docs/LESSONS.md`, "What the station cannot catch") | SUGGESTED | Claude |
| Lesson 2's "top resistor is 1k" puts the middle at 3.0 V, near the meter's top: on a station that saturates there it reads like "bottom resistor missing" | SUGGESTED | Claude |
| The 30 mA cut and the simulator's 100 mA limit are not checked against the real station (it has no sensor and no switch) | SUGGESTED | Claude |
| A hard short resets the S3, so the laptop loses the board until it restarts (`SerialBoard` keeps trying to reconnect) | SUGGESTED | Claude |
