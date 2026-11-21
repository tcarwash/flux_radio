# Flux K6 live serial screenshot and virtual keypad

**Status:** tested on a physical Baofeng FD6818B (0x4829) K6,
November 21, 2026. Integrated into Flux main (live serial).
`K6_REMOTE_UI=1` is enabled automatically in standard `K6_LIVE_SERIAL=1`
builds; opt out with `K6_REMOTE_UI=0`. No MICIN or CW experiments
are included in this integration.

## Quick use

With a live-serial radio on the programming cable:

```sh
cd /storage5/dev/ag7su-blowfang
python3 tools/k6_live.py --port /dev/ttyUSB0 screenshot --out /tmp/k6.png
python3 tools/k6_live.py --port /dev/ttyUSB0 key MENU
python3 tools/k6_live.py --port /dev/ttyUSB0 key DOWN
python3 tools/k6_live.py --port /dev/ttyUSB0 screenshot --out /tmp/k6-menu.png
python3 tools/k6_live.py --port /dev/ttyUSB0 key EXIT
```

The screenshot command writes a **512×256 PNG by default** (the native
128×64 monochrome pixels scaled 4× with nearest-neighbor interpolation).
Adjust with `--scale 1..12` or `--invert` (white-on-black). An existing
output file is never overwritten. Requires Python Pillow on the PC.

Keys are the normal logical UI gestures: `0`–`9`, `STAR`,
`POUND`, `MENU`, `EXIT`, `UP`, `DOWN`, `VM`, `AB`,
`BAND`, `SIDE1`, `SIDE2`. For example:

```sh
python3 tools/k6_live.py --port /dev/ttyUSB0 key UP --long
```

Long presses obey the hardware key's own long-press support;
ordinary digit 1–9 long presses are not supported. No PTT key event
is synthesized: use the separate existing remote PTT lease API.

Both screenshot and key require capability bits from the radio,
so an older image fails with a clear error instead of using
the legacy loader or resetting the radio.

## How screen capture works

This particular Flux host image (`blowfang-host`, not `banded`
or `shared_lcd`) uses ST7565 v0.6.0
`GraphicsPageBuffer<128,8>`. The existing
`MODULE_PAGE_BUFFER_STORAGE` allocation is **1,152 bytes**;
on the 32-bit ARM target the actual graphics structure is
**1,120 bytes**, consisting of eight 140-byte pages.
On the pinned 32-bit Rust compiler, the 12-byte Option-based dirty
bookkeeping comes **first**, then the 128 pixel bytes. The original
screenshot prototype assumed the opposite order and erroneously shifted
each page 12 columns right, clipping the final 12 pixels. The capture
reads bytes `page*140 + 12 ... page*140 + 139`. The extra 32 allocated
bytes are unused headroom.

The build asserts the exact 1,120-byte structure size.
If a future ST7565 version changes the representation, the
compiler will reject the capture feature until it is
revalidated. We deliberately avoid allocating another 1 KB
framebuffer or making an unsupported read from the LCD controller.

Protocol opcodes are non-overlapping live-serial extensions:
`SCREENSHOT=0x82`, `VIRTUAL_KEY=0x83`,
`SCREEN_PAGE=0x84`, `SCREEN_END=0x85`.
The host requests one screenshot over normal framed live serial
and receives a geometry/version header, then 16 individually
CRC16/XMODEM-checked 64-byte chunks in page order, and a
terminal completion frame. Only a complete, uniquely indexed
16-chunk snapshot is accepted; there are **no automatic retries**,
no flash writes, no bootloader entry and no radio reboot.

Pixel encoding is one vertical byte per x column per eight-row
page: bit0 is the top pixel in its page, bit7 is the bottom.
The host reconstructs rows and renders a PNG. The image is
the last *rendered* frame, not an LCD glass readback; any
draw/flush or frame timing gap can show a slightly older
screen. Module screens using a separate direct-paged
render path are not guaranteed to be captured.

## Virtual buttons

The host translates a canonical key name into a small V1
argument `key_index | (long ? 0x100 : 0)`. The device
validates it and queues **one logical Single or Long event**
into the existing keypad ring; the normal UI dispatcher later
handles it, including menus, key lock, and applications.

No GPIO is driven and no PTT can be represented. To avoid
colliding with an operator, requests are refused if a physical
key is pressed, if PTT/CW is active, during TX, while asleep,
or after the power switch is off. Synthetic side-key
Press/Release events are not created, so programmable TX-tone
press behavior cannot be driven by this command.
Some programmable single/long key actions may nonetheless
initiate ordinary radio features; they remain subject to
the UI's existing permissions. Keep physical access during
unattended testing and avoid blind key macros.

This runs over the connected *cable*, not an authenticated
Internet remote-control service. Any Web/Catalog integration
must use its own authorization and CSRF protections.

## Building and testing

```sh
# Standard live-serial build: remote LCD/keypad and remote-PTT lease
# are included; no MICIN/CW lab or high-API RAM privileges required.
K6_LIVE_SERIAL=1 K6_LEAN_TXDOG=1 \
K6_RAM0=stack K6_SERIAL_TRANSPORT=1 \
K6_OUT=/tmp/flux-main-live-remote-ui \
sh firmware/k6-aura/build-fd6818b.sh

python3 -m pytest -q \
  tests/test_k6_live_remote_ui.py \
  tests/test_k6_live_control.py \
  tests/test_k6_tx_tail_timing.py

K6_SIM_REMOTE_UI=1 \
K6_SIM_LIVE_IMAGE=/tmp/flux-main-live-remote-ui/flux-fd6818b.bin \
python3 -m pytest -q tests/test_k6_live_remote_ui_sim.py
```

### Physical device checkpoint (research firmware)

The real-radio test below was performed before integration on the
remote-UI research build, not on a main build. It established the
hardware framebuffer offset and the MENU/DOWN/EXIT behavior; the main
image is additionally checked with the compiled-firmware emulator.

New image SHA256:
`532f57ad9ffc4c91e3c96431ef97ce77272dab7e937d888d97971d137b1c713c`.
Radio build ID **`4ffc133f70799f41`** matched the image on
readback. Original API26 MICIN image is saved for rollback:

```text
~/flux-bench/live-remote-ui-20261121/rollback-micin-api26.bin
```

Physical test at 146.560 MHz FM, battery 7.65–7.66 V:
`screenshot` captured 1,024 pixel bytes into a valid PNG,
with both black and white pixels. Short **MENU** changed
28,896 pixels at 4× scale vs standby; **DOWN**
changed 37,312 vs first menu screen; **EXIT**
returned to standby with only 96 changed pixels
(blinking/dynamic UI). Radio was idle RX and
not transmitting after each test.

### 12-pixel crop bugfix, physical confirmation

The first screenshot release incorrectly began each page at offset 0,
reading the 12-byte dirty flags as pixels and omitting the last 12 real
pixels. Its selected MENU row had no black pixels at column 0 for
rows 10–19, although column 127 was black. Fixed firmware begins at
`page*140+12`, verified on physical K6 image build
**`f5c33782022b4016`** (SHA256
`aed8c389a3ec8820b9981a36bb1428367e7d3a19938c059ba9d1810c461d2411`).
On the new screenshot, the same row has black pixels at **both**
columns 0 and 127 for all ten rows, proving 128-pixel edge-to-edge
capture. Full-width frequency labels, battery indicator and mode
status are now visible. No protocol/host CLI change was needed.
The radio was left at 146.560 MHz FM, idle RX, battery 7.66 V.

Corrected images: `standby-fixed.png` and `menu-fixed.png` in
`~/flux-bench/live-remote-ui-20261121/`. The previous images remain
for regression comparison; both firmware images are saved in that folder.

The four PNGs are stored on the developer PC:

```text
~/flux-bench/live-remote-ui-20261121/
    standby.png
    menu.png
    menu-down.png
    standby-after.png
```

Protocol/host regression suite: **70 passed**; compiled-firmware
UI emulator: **1 passed**. This feature does not affect
ongoing CW work or change SPI calibration/memories.
