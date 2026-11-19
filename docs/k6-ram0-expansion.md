# Experimental expanded arena (Flux)

Based on upstream baitisj/blowfang d84accf. Opt in with `K6_RAM0=1`;
`K6_RAM0=small` relocates only the screen and RX ring without growing the arena.
Default remains unchanged. Do not expose to production until hardware validated.

RAM0 0x20000100..0x20001000 is 3840 bytes, retaining both vector regions.
Full layout uses 3560 bytes: App 3008, screen 200, RX ring 352. Arena 14976
(previously 11776), stack reservation 3144 (previously 2780), RAM0 spare 280.
Diagnostic uses 552 bytes RAM0, original arena and 3380 bytes stack reservation.
No API renumbering; old host catalog limits remain conservative. Modules explicitly
writing low SRAM are not safe on these images; do not run loram/ram0soak/etc.

## Regression and correction

First experimental full image 1f41aa21 accepted by physical bootloader but did
not reconnect after flash. SPI backup succeeded and is kept locally, not in git.
Simulator initially passed boot and handshake, but diagnostic build exposed a
write through 0xa5a5a5cd at read_battery_raw. Root cause: inserting .ram0 after
.uninit moved the linker's location counter used by PROVIDE(__sheap = .) and
_stack_end into low RAM. Stack watermark painted live SRAM, including protected
bootloader trampolines. Explicitly bind both symbols to __euninit. The simulator
bypasses actual bootloader interrupt forwarding, explaining why initial full
image could appear to work there. Hardware cause remains to be confirmed.

Corrected full image 40c8f044: linker audit, simulated main loop, keypad/menu,
CPS handshake, CRC-checked INFO (14976 bytes), CAPS (API24, cable-only, scan,
data, designer), and calibration block read matching actual SPI backup pass.
Corrected diagnostic 7f56476d: simulated boot, menu, handshake pass.
Use tools/k6_ram0_audit.py (now required on opt-in builds) and
 tools/k6_ram0_smoke.py IMAGE ELF SPI to repeat. Never commit physical SPI dumps.
No physical test of corrected image yet; radio needs power cycle and bootloader
recovery. Use a known-good firmware first if necessary. No transmission tests.
