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

## Successful stock UPDATE-mode flashing and postflash serial check

After operator **explicitly confirmed `UPDATE` was visible**, ran
`python3 tools/k6_install.py --port /dev/ttyUSB0 flash --already-update
/storage5/dev/flux-gp0-phase-probe-20261125-release/flux-fd6818b-gp0-uart-probe.bin`
under bench owner `sotto-gp0-uart-reflash`. Bootloader accepted the whole
**119/119KiB** stream and reported `flashed; the radio restarts`. This is
first successful physical installation of the **121,644B** IRQ/RX LCD
instrumentation; no module/SPI changes.

Postflash read-only `k6_live.py caps` and `status` both returned
`No live response` (exit 1). Thus UART is still not operational from the
PC's perspective. The last stage must be determined from the **physical
LCD**, not the missing serial handshake. Ask operator to report `IRQ YES/NO`
and `RX YES/NO`; any `YES` status tells us which interrupt/receive stage
is alive. No RF transmission has been attempted.

## Operator's physical LCD observation after successful UART probe flash

The operator reported that the screen shows **`PHASE 3 / MAIN LOOP`**,
not the expected `IRQ YES/NO` and `RX YES/NO` screen. Source inspection
shows `PHASE 3` is displayed at the *start of the first main-loop pass*,
whereas the UART summary is drawn only inside the normal 50ms refresh
much later in that loop, after scheduler/timer, serial PTT polling,
watchdog service, handshake, keypad and module service, plus several
normal RX housekeeping calls. This is evidence that the new image enters
`main`'s scheduler loop, **not** evidence that it ever completes an
iteration. The screen alone cannot distinguish a first-pass deadlock,
interrupt fault, broken uptime/scheduler or a permanently false 50ms
refresh condition. Likewise, it provides **no UART IRQ/RX counter verdict**.

Next targeted offline/hardware probe: insert one-shot display checkpoints
after scheduler, serial/txdog/handshake dispatch, module/key service, and
just ahead of the first UI refresh. Reuse existing phase probe state; keep
SRAM0/stack and flash audits enabled. This should be done before risking
any new Sotto module flash or RF transmission. Existing recovery image
and full 2MiB SPI backup are unchanged.

## Confirmed hard freeze and physical battery removal (operator report)

The operator reports **`PHASE 3 / MAIN LOOP` stayed on the screen, keypad
buttons did not respond, and the radio remained frozen until the battery
was physically removed**. This is stronger evidence than an unchanging LCD
alone: the first main-loop iteration is not providing normal input service.
Do **not** call the phase-three image a successful VFO boot or treat the
missing UART response as an isolated parser bug. The operator removed the
battery; current powered-on/UPDATE status is not established.

Code inspection identifies this first-pass execution order following the
`PHASE 3` display: `scheduler.tick()` (TIM14 uptime), optional live
`poll_ptt`, `txdog::poll`/timer accounting, `host_handshake.poll()` (serial
RX ring), `poll_keys_on_arena()` (switches stack to 14,976B module arena),
`app.poll_pager`, then the remainder of power/battery/audio/scan/squelch
housekeeping, and finally a scheduled 50ms screen refresh which would
replace the display with `IRQ YES/NO / RX YES/NO`. The hang is **somewhere
before that refresh** or the refresh clock never becomes due. We do not
yet know the exact instruction or whether there was a HardFault, a clock
stall, an infinite loop, or stack/arena overlap. Original protected SRAM0
is nearly full (3836/3840 bytes in this debug image); no root-cause proof.

For the next experiment, avoid testing another entire production-sized
Sotto build blindly. Create a bounded diagnostic that emits checkpoints
immediately following each early-loop step (or a smaller binary-search
instrumentation) and preserves the known-good recovery image. Don't flash,
run RF or modify SPI until the operator has explicitly placed the radio in
UPDATE again, and verify the build has a safe main-stack and module-arena
budget. A successful display of `PHASE 3` is **not** qualification of main
loop, UART ISR, RX/TX or Sotto.
