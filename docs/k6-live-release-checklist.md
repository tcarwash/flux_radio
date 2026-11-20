# Flux live serial / Hamlib release hardening

## Operational status

The **mainline source** includes non-rebooting live status and CAT, negotiated
SPI backups, verified packed-library install/remove, foreground module runs,
API-25 opt-in cooperative cancellation, and a loopback-only Hamlib NET rigctl
bridge. Firmware builds must explicitly enable `K6_LIVE_SERIAL=1` to expose
the live protocol. The Python/Catalog clients never silently fall back to the
rebooting loader; explicit legacy mode remains available for recovery and
firmware flashing.

The Catalog's **My radio → Run and stop a module** panel is an experimental
feature **disabled by default**. To enable it on a trusted single-worker
local Catalog server, set `FLUX_CATALOG_COOP_ENABLED=1` in the server's
environment. The Run and Stop endpoints accept only loopback requests
(`127.0.0.1` / `::1`) unless the administrator also explicitly sets
`FLUX_CATALOG_COOP_REMOTE=1`. The default loopback check also refuses
nonlocal Host headers to guard against typical local reverse proxies.
The remote opt-in is only suitable behind your own separately
authenticated gateway; the Catalog's CSRF token **is not user
authentication**. Do not expose these endpoints on an untrusted LAN
or publicly accessible unauthenticated server.

With a configured serial port, first read the radio inventory, select
an installed *module*, acknowledge the native-code risk, then Run.
Only a **packed-library API-25 module** whose complete
envelope passes its integrity tag check can start. Keyed firmware also needs
the configured signing key and live proof. Cable-only CRC tags are for
integrity, **not authentication**. Neither level 25 nor an integrity tag
guarantees that code is safe, RX-only, or polls for cancellation.

- Only one Catalog job is allowed per server process. The normal OS serial
  port lock prevents another process from using the cable simultaneously.
- Run opens a live session in a worker and keeps that lock until the job ends.
  Reloading the page reconnects to that worker through the session token; a
  page close does not stop it. Multiworker/reloading web-server processes
  do **not** persist in-memory job state: keep the Catalog single-worker.
- Request stop sends **one** nonce-bound command via the existing module
  polling callback. The radio alone decides when to return. A request, an
  ACK and a completed module exit are separately reported.
- An ambiguous timeout/disconnect is shown as **Execution unknown**, and
  a second launch is blocked. **Recheck idle radio** performs a new read-only
  live status/capability query to prove the single-core main loop is
  responding in idle RX before allowing another launch. This does not
  retroactively prove a successful module cleanup.
- Never run untrusted native code. The module API includes transmitter,
  hardware-register and storage operations even though the live serial
  protocol has **no remote PTT command**. No RF capability is implied.

### Hamlib

`python3 tools/k6_rigctld.py --port /dev/ttyUSB0` listens only on
`127.0.0.1:4532`. Existing Hamlib programs use radio model **2 (NET
rigctl)**. Status, RX meter/squelch, VFO, mode and constrained frequency
tuning are supported subject to live feature bits and radio standby guards.
No Hamlib source patches are required; this is **not** an upstream Hamlib
radio model. Remote PTT, TX power, CW send and memory writes are deliberately
unsupported. Unsupported CAT setters return an error and are not advertised
as supported in the `dump_state` discovery.

### Physical radio evidence (2026-11-20)

Firmware build `ce46aac935c96e97` was physically flashed and exercised
earlier. The slot-26 210-byte demo was installed, cooperatively cancelled
with ACK and a -125 return after 0.635 s, then relaunched without a stop
and returned 42 in 3.201 s. Its removal restored the original 26 logical
objects. Factory calibration and VFO settings matched the baseline.
The same radio subsequently passed read-only Hamlib NET rigctl checks on
147.790 MHz FM / 15 kHz, VFOA, PTT=0, RSSI and DCD without any TX or
flash operation. The live 32 KiB download completed a 2 MiB snapshot
in ~60.7 s, versus ~102 s for the previous 4 KiB live implementation.

Simulator-only safety hardening (same date): with virtual PA10/PTT
held low, the compiled firmware reported TX/non-idle and refused a
cooperative module launch; virtual SPI was unchanged and RX standby
returned upon release. During virtual PA15 power-off, an earlier
firmware image temporarily reported idle despite the switch being
logically open. The new source checks the physical power switch
in live status/launch/CAT/bulk-idle guards, and compiled-firmware
tests confirmed non-idle status during power-off with unchanged SPI.
The power-guard firmware build `f693147e70527a58` was later flashed and
accepted on the physical K6 in RX-only testing. See the
[physical acceptance record](k6-live-physical-acceptance-20261120.md).
The manual PTT, power-switch and USB-disconnect tests remain open.
Virtual GPIO tests do not certify emitted RF or real power sequencing.

This evidence does **not** prove operation with the physical PTT pressed,
power switch opened during a byte stream, UART unplugged during TX,
different USB cables/Windows systems, or transmit safety of third-party
modules.

### Pre-release manual electrical verification (not yet performed)

These tests require the operator physically at the radio with an appropriate
dummy load or isolated RF test setup, an independent RF power indicator, and
a reliable rollback path. Do **not** trigger local PTT, interrupt radio
power, or flash the unit automatically merely to complete this checklist.

1. Capture the exact live firmware ID, stored calibration sector,
   module directory and full SPI backup. Confirm RX/standby.
2. Establish the idle status and CAT baseline. Record screen, key
   responsiveness, squelch/RX audio, temperature and VFOs.
3. With an independently monitored RF path, hold and release *local* PTT
   while attempting read-only status, download and module preflight.
   Verify no module launch or SPI mutation when not in idle RX.
   Pay particular attention to the PA10 shared UART/PTT wiring.
4. Toggle local power **off** only in a controlled isolation setup while
   a read-only burst or a cancellation-capable RX-only test is in flight.
   Record whether the transport aborts/returns to baseline, how the MCU
   delays shutdown, and whether a subsequent cable session can recover.
5. Disconnect/reconnect USB during read-only and cooperative-run
   acknowledgments; confirm uncertain executions remain marked unknown
   and are not automatically retried.
6. Validate Win32 COM-port selection, port lock interoperability and
   multiple Hamlib clients on the actual hardware.
7. Repeat with a keyed firmware image and verified signing-key recovery
   procedure before advertising keyed workflows as physically accepted.

Until these pass, keep **remote PTT disabled** and the Catalog foreground
runner labeled experimental. Do not call cancellation a hardware kill switch.

## Automated regression entry points

```sh
python3 -m pytest -q tests/test_k6_catalog_coop.py tests/test_k6_rigctld.py
python3 -m pytest -q tests/test_k6_live_control.py tests/test_k6_live_v2.py
# with a compatible built firmware image; simulator is not RF evidence
K6_SIM_LIVE_IMAGE=/path/to/flux-live.bin K6_SIM_LIVE_PACKED=1 \
  python3 -m pytest -q tests/test_k6_live_control_sim.py tests/test_k6_live_power_sim.py
```
