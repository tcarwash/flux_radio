# Sotto GP0 live-serial diagnostic follow-up (2026-11-25 PST)

Physical GP0 phase-probe firmware reached the VFO screen, but live `caps`,
`status`, and the library query all timed out without response. Sending
read-only CAPS packets at 115200/192000/500000 baud yielded **zero incoming
bytes**. No process held `/dev/ttyUSB0`. This does not establish whether
USART1 IRQ, physical serial input, or the live parser is at fault.

The new opt-in `K6_BOOT_PHASE_PROBE=1` instrumentation adds volatile counters
of USART1 IRQ entries and received RXNE bytes, and draws `IRQ YES/NO` and
`RX YES/NO` in the normal UI redraw. Only diagnostic builds have these
counters. Neither transmits RF nor reads/writes SPI. The two counters are placed
in protected RAM0 so the main stack reservation isn't reduced.

**Built and validated, NOT flashed:**
`/storage5/dev/flux-gp0-phase-probe-20261125-release/flux-fd6818b-gp0-uart-probe.bin`
SHA256 `bc063e85bf59cfb4419b98e155b4ae8431fbbd944efc455e34fa7eeca2e2f4c2`
Size 121644/122880 bytes. RAM0 3836/3840 (4 free). Stack reservation 2780.
31 tests passed, 3 skipped; instrumented labels present in final binary.
All three iterative firmware overlay passes reported the probe installed.

When the radio enters UPDATE using both side buttons at power-on, this image
can be flashed with `python3 tools/k6_install.py --port /dev/ttyUSB0 flash
--already-update /storage5/dev/flux-gp0-phase-probe-20261125-release/flux-fd6818b-gp0-uart-probe.bin`.
Observe startup: screen should show `IRQ NO/RX NO` before traffic, and
possibly change after a PC CAPS query. `IRQ NO` after traffic suggests no
USART interrupts. `IRQ YES/RX NO` suggests interrupt without RX byte. If RX
is `YES` but live control still fails, investigate protocol dispatch.

The original recovery image is
`/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-dma-v1/flux-gp0-mic-route-only.bin`
SHA256 `751995d2dd346a2cfbf7503f8b4d722076cec9164a61efa495b3ca884dc67ccb`.
The full 2MB SPI backup was already preserved as documented in
`docs/k6-gp0-startup-phase-diagnostic-20261125.md`. Avoid altering module
slots while live serial is unresponsive.

## Actual attempted bootloader flash (2026-11-25 PST, after image built)

After the operator said "hit it", the bench was claimed and the staged
UART diagnostic SHA256 was checked against the on-disk image. The installer
opened `/dev/ttyUSB0` with `flash --already-update` but the stock bootloader
**did not respond to command `0x01`**. It reported `stopped: no answer from
the bootloader to command 0x01`, **before any firmware image bytes were
flashed**. Post-attempt, read-only live status still timed out (the prior
phase-diagnostic image had already exhibited that behavior). The device
node existed, was not held by another process, and system logs were not
accessible as an unprivileged user. **No assertion that the radio is in
UPDATE mode can be made until the operator confirms the screen.** A new
flash requires the operator to enter UPDATE using both side buttons and
confirm `UPDATE` visibly, then rerun a safe bootloader command/handshake.
No module library writes, SPI restore or RF transmissions were attempted.
