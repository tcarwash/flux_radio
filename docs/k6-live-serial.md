# Flux live serial control (experimental)

The normal loader is deliberately non-returning: it changes stack ownership,
stops RF, and resets after END or timeout. Live v1 is separate and runs one
bounded request per host-loop pass, using the existing returning UI arena
stack trampoline. No loader handshake, END, explicit reboot, UART reconfiguration,
or SPI write occurs in the new path. The legacy protocol and bootloader capture
remain unchanged.

## Build and use

The feature is opt-in during hardware validation:

    K6_LIVE_SERIAL=1 sh firmware/k6-aura/build-fd6818b.sh

Flash the resulting firmware with the normal installer when the radio is
available; do not interrupt another bench job. In normal VFO standby, use:

    python3 tools/k6_live.py --port /dev/ttyUSB0 status
    python3 tools/k6_live.py --port /dev/ttyUSB0 tune 146520000
    python3 tools/k6_live.py --port /dev/ttyUSB0 watch --interval 0.5

Use the actual K6 port (not an attached rig's port). The Catalog's My Radio
panel also exposes status and VFO tuning. Memories uses the live API by
default; its explicitly selected legacy checkbox restores the older rebooting
read path. No fallback is automatic, including on timeout. Older firmware
will not respond to live requests. The Catalog memory programming and recovery retain the loader; the live bank read preserves native decoding and pending-write identity. The Catalog library inventory and module
install/remove still use the old loader.

## Wire protocol

115200 baud; no handshake or persistent session. Each request is exactly 14
bytes: ASCII FLXLV1 (6), opcode (1), sequence (1), uint32 LE argument (4),
CRC16/XMODEM over the preceding 12 bytes (2, BE). The existing rolling UART
handshake detector recognizes the prefix and valid CRC, so legacy handshake
strings continue to work independently. The host sends one request at a time.
Responses use the existing CPS envelope: A6, opcode (1), sequence zero-extended
to 16 bits BE, length BE16, payload, CRC16/XMODEM BE excluding A6. First payload
byte is result: 0 success, 1 busy, 2 invalid, 3 SPI I/O error.

| Opcode | Argument | Success payload after result byte |
|---|---|---|
| 60 hex | 0 | FLIV, version 1, master/watch indices, flags, A/B Hz LE32, battery centivolts LE16, cached RSSI dBm signed LE32, A/B modulation IDs, A/B wide flags |
| 61 hex | 64-byte aligned physical SPI address 0..0xb000 or 0xf000..0xffc0 inclusive (calibration identity for recovery) | 64 bytes of checked SPI read |
| 62 hex | integer Hz | empty; updated state can be queried with 60 |

Flags: bit 0 TX, 1 squelch open, 2 standby/no PTT/CW TX, 3 power-save asleep,
4 A memory mode, 5 B memory mode. Modulations: FM=0, AM=1, USB=2, CW=3, CWF=4.
Status reflects cached UI RSSI and battery state, not an instantaneous ADC read.

Tuning intentionally changes only the active VFO in RAM, synchronizes the
watching side using the normal radio configuration path, and preserves its
existing offset, modulation and power. It requires standby, awake RX, no PTT,
VFO (not channel), no reverse, and no selected resident digital engine.
The initial conservative test range is 136–174 MHz or 400–480 MHz at 10 Hz
resolution; this is not a statement about RF capability or TX legality.
No remote PTT, write-memory, raw register, or reset command exists. Later local
VFO saves may persist the tuned frequency; a live tune does not itself save.

The controller uses the existing process port lock. A full memory read is
currently sequential (one 64-byte response per main-loop pass), and can fail
with a busy error if the local operator changes modes during the read; it
never switches to the disruptive loader on its own.

## Validation and next steps

- Run host tests: `python3 -m pytest -q tests/test_k6_live_control.py tests/test_k6_memories.py`.
- Compiled-image simulator: `K6_SIM_LIVE_IMAGE=/path/to/image.bin python3 -m pytest -q tests/test_k6_live_control_sim.py`.
- On hardware: use a dummy load or isolated RX bench, verify no boot banner and
  no unintended PTT during repeated status/reads, compare actual RF frequency
  to VFO display after tune, verify keypad interaction and unchanged stored
  memory, measure response latency and RX audio gaps while polling. Check
  sleep, open squelch, busy/TX, reverse, memory and resident-engine guards.
  Exercise disconnect/reconnect and legacy update bootloader entry separately.
- Then consider an opt-in rigctld-compatible local bridge, explicit mode
  control, active VFO selection, and channel selection. Remote PTT needs an
  independent firmware lease/timeout and physical interlocks before enabling.
  A live full SPI backup or library inventory needs an explicit expanded read
  map and throughput test.

Emulator and host results do not prove physical RF, cable/PTT electrical
behavior, timing, or receive continuity.

## Foreground module launch (experimental)

With the updated live-enabled firmware, run an already installed Catalog
module by physical library slot:

    python3 tools/k6_live.py --port /dev/ttyUSB0 run 24 --timeout 30

The client emits module `say` reports to stderr as they arrive and prints a
JSON result with slot, loader result, whether the entry returned, its signed
32-bit return code, and all reports. A negative module return is distinct
from failure to load or execute. `--allow-higher-api` explicitly permits
modules whose declared API level exceeds 2; those can use hardware-changing
and TX APIs. Native modules are trusted code, not sandboxed by the declared
level. Only run known modules appropriate for the bench and authorization.

- Request 0x66: low five bits of argument are the slot (0–31); bit 8 permits
  API levels above 2; other bits must be zero. Normal response status only.
- Report event 0x67, same sequence: kind byte 0 then up to 60 UTF-8 bytes.
- Completion event 0x67, same sequence: kind 1, slot byte, signed LE32 loader
  result, byte 0/1 indicating entry returned, signed LE32 entry result.
  Loader result -200 means the deferred dispatch was cancelled by its guard.
  The event is sent after the existing runner's cleanup, not at acceptance.

The command queues the existing normal Catalog dispatcher rather than
executing native code on the live request's stack. It requires awake, idle
standby, rejects resident/data/page/deck/empty slots, and repeats the mode
and module guards before dispatch. Loader verification remains unchanged.
The request stack returns before module execution; no legacy handshake or
END is sent. The host waits for reports without sending further requests,
keeps the existing serial lock, and never automatically retries execution.
A timeout or disconnect is an unknown execution state, not a cancellation.
Resume only after establishing whether the module finished. These are
foreground modules; long-running modules still need cooperative stop logic.
Framed reports work independently of debug logging. No remote PTT opcode
was added. Physical execution requires a compatible image and an installed
module; the existing legacy installer is unchanged.

Validation (2026-11-20): 12 focused Python tests passed; compiled firmware
emulator tests passed for both fixed-slot and packed-library builds (2 tests
each). They exercise module execution, framed reports, return code, slot/type
and integrity refusals, standby/live status recovery, SPI preservation and
absence of legacy loader traffic. The physical radio advertised legacy cable
protocol 5 (packed library), so use a compatible packed build for hardware
validation. No physical flash or live execution validated at this checkpoint.

## CAT control and feature discovery

The serial/Hamlib branch adds these v1 commands (existing wire format unchanged):

| Opcode | Argument | Success data after result byte |
|---|---|---|
| 65 | 0 | MCU temperature ADC sum, VREF sum, battery centivolts (three signed LE32); opt-in health build, awake idle RX only |
| 68 | 0 | FLCP, schema byte 1, LE32 feature bitmap |
| 69 | mode 0–4 in low byte; bit 8 wide; remaining bits zero | empty |
| 6a | 0=A, 1=B | empty |

Capabilities bits 0–6: status, memory read, tune, installed foreground
module execution, mode control, VFO selection, health. Unknown bits are
ignored by older clients. These bits do not advertise module ABI version,
RF capabilities, or TX authorization. CW research opcodes 63/64 remain
reserved (not ported, because they depend on research-only watchdog work).
Mode and VFO changes are volatile and reuse the normal App radio
configuration path. Both require idle awake standby and no resident engine,
PTT or CW keying; mode requires an unreversed VFO and VFO selection requires
an unreversed VFO target. Nothing writes SPI. The advertised wide/narrow
state is the radio configuration, not a calibrated filter bandwidth.

    python3 tools/k6_live.py --port /dev/ttyUSB0 caps
    python3 tools/k6_live.py --port /dev/ttyUSB0 vfo 1
    python3 tools/k6_live.py --port /dev/ttyUSB0 mode USB
    python3 tools/k6_live.py --port /dev/ttyUSB0 health

See [Hamlib](k6-hamlib.md) for the compatible NET rigctl bridge and
[protocol roadmap](k6-live-serial-roadmap.md) for the development transport.
