# firmware/

The board's firmware does not live here. The real board is the station
(docs/HARDWARE.md), and its firmware is **`firmware/smartboard_s3`** in the
[jinglebells2020/smartboard](https://github.com/jinglebells2020/smartboard)
repository. It starts in **Genesis mode**: it speaks docs/SERIAL_PROTOCOL.md
(version 2, the same protocol sim/fake_board.py uses), adds
`"supply_v": 3.3` to its hello line, and must pass `tools/check_board.py`.

Order for the first time: the mux test (`tools/mux_test` there) saves
`as_built_map.json`, `tools/make_fw_maps.py` turns it into the row map,
then flash `firmware/smartboard_s3` from the Arduino IDE. Step by step:
docs/FIRMWARE_GUIDE.md part 0, and that repository's `firmware/README.md`.

Another board? docs/FIRMWARE_GUIDE.md says what its firmware must do.
