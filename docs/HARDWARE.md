# Genesis: Smart Breadboard: hardware

The board Genesis runs on is **the station**, built in the
[jinglebells2020/smartboard](https://github.com/jinglebells2020/smartboard)
repository (hardware, CAD and its firmware). Everything below is decided and
built there; this page is what Genesis needs to know about it.
`docs/HARDWARE_SUMMARY.md` lists each item with its status and source.

## Boards
- Microcontroller: an **ESP32-S3-WROOM-1 N16R8** dev board (16 MB flash,
  8 MB PSRAM).
- FPGA: none. The S3 does everything on the board.
- Screen: an **M5Stack StickS3** in the front lid, with its own battery. The
  S3 talks to it by radio (ESP-NOW), not by wire.
- Connection to the laptop: USB serial on the S3's own (native) USB port,
  USB vendor id **0x303A** (it shows up as Espressif "USB JTAG/serial debug
  unit"). The baud rate does not matter on that port; Genesis uses 115200.
  `--serial auto` picks that port by itself (`agent/serial_board.py`). The
  dev board's other USB port (a WCH chip, vendor id 0x1A86) is not used.
- Firmware: `firmware/smartboard_s3` in jinglebells2020/smartboard. It starts
  in **Genesis mode** (serial protocol version 2, `docs/SERIAL_PROTOCOL.md`)
  and must pass `tools/check_board.py`. See `docs/FIRMWARE_GUIDE.md`.

## Power
- The laptop's USB-C cable powers everything.
- **Breadboard supply: 3.3 V only**, from the S3 board's regulator. This is
  forced: the mux chips take nothing outside 0 to 3.3 V. Every lesson is
  written for 3.3 V (`SUPPLY_V` in `protocol.py`), and the station's hello
  line says `"supply_v": 3.3`.
- Current limit: none of its own. A hard short (a wire from + straight to -)
  pulls the regulator down and **resets the S3**, again and again while the
  short is there: the laptop loses the board, and the regulator runs at its
  limit and **gets hot until the USB cable is pulled**. So when the station
  stops all at once, Genesis says on the laptop (the board screen cannot be
  reached then): "The board stopped. If you just added a wire or part, + may
  be joined straight to -: unplug the USB cable now, then find that wire."
  **If the row lights freeze, or anything on the breadboard gets warm: pull
  the USB cable first, then look for the fault.**
- Power cutoff part: **none** (`has_power_switch: false`). **Unplugging the
  USB cable is the only off switch** for the whole station. To take the power
  off the breadboard only, the student **pulls every wire out of the + line**
  (the board keeps measuring): every wire or leg the circuit has there (one
  red wire per LED circuit in lesson 3; any wire or leg in free build). That
  is what Genesis tells the student on the laptop and on the screen when a
  circuit is dangerous, and when the red button is pressed. The + line itself
  stays at 3.3 V and its rail stays `up`: the rail feeds are wired inside the
  station (soldered under the core's + lines, plugged into the logic board)
  and are not the student's to pull. So Genesis sees the switch-off as the
  circuit's rows going empty, starting with the plan's + rows.
- **The feed unplugged** (a teacher unplugged the feed header, or a feed wire
  came loose): the + line has no power feed. The tap reads `off`, the station
  sends `"supply_v": 0` and lists `PT+`/`PB+` as floating, and Genesis says
  "The + line has no power. Ask a grown-up to check the station's power plug
  inside." That is not the student's switch-off, and never a short. (A tap far
  below 3.3 V while the S3 still runs cannot be a sag: the feed is the S3's
  own 3V3, so the S3 would have browned out. The feed is not connected.)
- The S3 watches its two rail taps. A **sag** (the + line driven between
  2 V and its limit, about 2.8 V) is a short: the station warns ("Short
  circuit! / Pull every wire / out of the + line / right now!" on the
  screen). A tap that is **off** (below 2 V, floating or driven: the + line
  has no power feed) is not a short: no warning. In Genesis mode the
  firmware never changes Genesis's row lights or screen by itself: Genesis's
  own SHOW replaces the warning.

## Sensors
- Current sensor: **none** (`has_current: false`, `current_ma` is null).
  Genesis works the current out from the rows on both sides of a lesson's
  resistor ("calculated", `docs/LESSONS.md`). That also means **an LED with
  no resistor is not always caught.** The regulator usually holds 3.3 V
  with one LED across it (about 90 mA), so the rows read like a backwards
  LED or an open circuit. Genesis cannot tell, so on this board those
  hints start with a check the student can see: "First look at the LED. If
  it is very bright or feels warm, pull every wire out of the + line right
  now: the resistor may be missing." Only a + line that really sags (below
  about 2.6 V in the lessons) makes Genesis say "switch the power off by
  hand" at once.
- Voltage: the S3's own ADC (`ADC_11db`), through **9 × CD74HC4067**
  multiplexers: 8 leaf muxes and 1 root, all running at 3.3 V.
  - **Every breadboard row** is wired straight to a mux pad: 126 row nodes
    (`T1`-`T63` for holes a-e, `B1`-`B63` for holes f-j) plus 2 rail taps
    (`PT+`, `PB+`). `PT-` and `PB-` are ground and are not measured.
  - Range: 0 to about 2.8-3.1 V. At or above its clip point (the rail tap's
    saturated reading minus 0.1 V; 2.85 V before calibration) the firmware
    sends the value as the supply (`supply_v`, "at the supply").
  - Accuracy after calibration: about ±0.05 to 0.1 V. The lessons' accept
    ranges allow ±0.1 V and the saturation (`tests/test_station.py`).
- Empty ("floating") rows: a **1 MΩ injector** on GPIO14. With it on, a
  floating row reads about 1.65 V, while a driven row barely moves. The
  board sends those rows in `floating`; Genesis never uses their number.
- Which wire is which row: the **mux test**
  (`tools/mux_test` in jinglebells2020/smartboard) walks every row and saves
  `as_built_map.json`; `tools/make_fw_maps.py` turns it into the firmware's
  row map. Until that is flashed the firmware is "unmapped": its hello line
  has `"rows": []` and Genesis cannot check a lesson yet.

## Outputs
- Row lights: one **WS2812B strip per side**, 26 LEDs each (52 in all), on
  GPIO15 (top, `T`) and GPIO16 (bottom, `B`).
  - 25 of the 63 rows have their own light: 2 5 7 10 12 15 17 20 22 25 27 29
    32 34 37 39 42 44 47 49 52 54 57 59 61.
  - A row without its own light is shown by lighting the nearest lit rows on
    both sides, which are never more than 3 rows apart.
  - Total LED current is capped at 150 mA in the firmware.
- Screen: the StickS3 shows a big row name plus text. It holds 4 lines of 20
  characters easily, so `SCREEN_LINES` x `SCREEN_CHARS` stays 4 x 20.
- A small RGB status light (GPIO38, 39, 40) and a knob (16 mm B10K pot on
  GPIO8). Genesis does not use either yet.

## Buttons
Four buttons, `INPUT_PULLUP`, active low. They have **colours, not numbers**,
so every text a student reads names them by colour (`BUTTON_COLORS` in
`protocol.py`), and the pretend board on the web page draws them in the same
colours.

| Button | GPIO | Genesis `id` | Meaning |
|---|---|---|---|
| Yellow | 17 | 1 | Check my circuit |
| White | 18 | 2 | I need a hint |
| Blue | 21 | 3 | Next step |
| Red | 47 | 4 | Power off / on (no switch: the screen says to pull every wire out of the + line, or push them back) |

The StickS3's orange key also sends `id` 1 (check). Each press sends
`{"type": "button", "id": N}` once.

## Pin map (do not change)

| GPIO | What |
|---|---|
| 1 | SIG (root mux output to the ADC) |
| 4-7 | Leaf S0-S3 |
| 11-13 | Root S0-S2 |
| 14 | Float injector (1 MΩ) |
| 8 | Knob (pot) |
| 15, 16 | LED lanes T and B |
| 17, 18, 21, 47 | Buttons yellow, white, blue, red |
| 38, 39, 40 | RGB status light |

## Parts available for lessons
- Resistors: 220 Ω (lessons 1 and 4), 330 Ω (lesson 3), 10 kΩ (lesson 2),
  and 100 Ω, 1 kΩ for free build and the practice mistakes.
- LEDs: **red, yellow or yellow-green only in the lessons.** At 3.3 V a
  blue or white LED (about 3.0 V) barely lights, and so does a bright "pure
  green" one (about 3 V, like blue). Two LEDs in a row cannot light at all.
  Free build and the random judge circuits may still use any colour.
- Other: one push button (lesson 4), red and black wires.
