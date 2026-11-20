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
will not respond to live requests. The Catalog memory programming and recovery retain the loader; the live bank read preserves native decoding and pending-write identity. The Catalog library inventory uses live reads by default in the primary-transport branch; module install/remove still use the old loader.

## Wire protocol

115200 baud for requests; no handshake or persistent session. Bulk data may use a bounded negotiated downlink excursion. Each request is exactly 14
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

## Primary transport: negotiated full SPI read / inventory

Build with K6_LIVE_SERIAL=1 K6_SERIAL_TRANSPORT=1 and the correct storage
layout. New capability bit 7 advertises full physical SPI bulk reads; bit 8
advertises live library INFO. The old limited 64-byte command remains
unchanged. Explicitly use --legacy for a stock radio or older firmware
without these features; failures never silently invoke the rebooting loader.

    python3 tools/k6_install.py --port /dev/ttyUSB0 backup -o radio.bin
    python3 tools/k6_install.py --port /dev/ttyUSB0 status
    python3 tools/k6_install.py --port /dev/ttyUSB0 backup --legacy -o radio.bin

0x6b takes a packed LE32 argument: bits 0–14 physical address / 64;
bits 15–19 frame count (1–16; max 4 at baseline); upper bits rate ID
(0=115200, 1=192000, 2=500000; no other values allowed). Read bounds
are the 2 MiB chip. ACK is result + actual MCU baud LE32 at 115200.
At a faster rate the receiver waits 30 ms, switches, and emits at most
16 frames, each 0x6c with the request sequence and LE32 absolute byte
address plus 256 bytes. 0x6d completion with same sequence contains
result, count sent, four LE32 UART error deltas (overrun/framing/noise/
ring drop). All frames use existing CPS CRC. The firmware waits for
the UART to drain and restores 115200 without needing an additional
request. Host sends no bytes until completion or silent recovery. Reads
are rejected outside standby, during TX/CW/PTT or resident engine use,
or during binary-mode ownership; RX power-save sleep alone is allowed.
Any incoming bytes during the burst are treated as an abort, not commands.
One bounded burst temporarily blocks normal UI servicing; physical latency
and audio continuity must be tested before enabling release defaults.

The host checks a 4 KiB reference at 115200, attempts 500000, then 192000,
then baseline. It compares bytes, sequence, address, length, CRC and UART
counters and confirms status at baseline. On failed bulk transfer it waits
silently for the bounded excursion to finish, restores its own baud, clears
stale input and confirms normal live status. Only reads may be retried, and
busy or explicit I/O refusal fails rather than being retried. Full backups
recheck calibration and persist atomically on the PC. A live backup is not
an instantaneous snapshot: keep the radio idle and don't modify settings
or library while reading. No RF transmission or SPI mutation is requested.
Normal CAT requests remain at 115200. Sustained uplink negotiation,
variable-length writes and IQ/FSK streaming are separate upcoming milestones.

0x6e, argument zero: returns the same BLWF metadata record as legacy INFO
(storage protocol, slots, arena size, optional build ID). This is metadata,
not an authenticated capability proof. The live inventory reuses the
legacy fixed-slot/packed directory and typed theme parsers over read-only
live SPI. The Catalog reader defaults to live and offers an explicit legacy
checkbox. Pack preflight/authentication, provisioning, flashing, memory
writes, installs/removals and other special mutations remain legacy for now.

## Binary-safe live V2 and temporary RAM execution

New capability bit 9 advertises V2 frames, bit 10 advertises RAM execution.
V1 remains unchanged. The V2 request starts with six ASCII bytes FLXLV2,
followed by the encoded body and one NUL terminator. The raw body is
cmd:u8, seq:u8, length:LE16, arg:LE32, payload[length], CRC:BE16.
CRC-16/XMODEM covers the prefix and unencoded body excluding CRC. Encode
the raw body LSB-first as consecutive seven-bit symbols, set the high bit
of every symbol, and zero-pad the final symbol. The decoder rejects excess
symbols, nonzero padding, invalid length/CRC and low-bit symbols within
the body. Maximum payload 128 bytes. No raw body fragment can match either
ASCII legacy handshake or the V1 prefix, even after a truncated request.
The request body expires after 100 ms; responses use the existing CPS
framing with cmd and seq matching the request. 0x70 with arg 0 echoes the
payload and is suitable for integrity testing. Malformed frames do not
dispatch or acknowledge. Unknown valid commands reply INVALID.

Temporary module execution requires K6_RAM0=stack, K6_LIVE_SERIAL=1 and
SPI-library support. The stack layout retains the original 11776-byte
arena and relocates App/RX/screen into RAM0; the tested build reserves
6320 bytes for the main stack. Use the correct storage layout for the
radio. The initial experimental limit is 4096 bytes including the existing
K6M header and tag, API levels 1–2 only. A trusted native module is not
sandboxed; code authors must obey the module API contract. A keyed firmware
requires the existing SipHash envelope. The default physical-cable-only
firmware uses CRC32/complement rather than authentication; use the explicit
cable-only CLI switch. Higher API levels are not yet available in RAM mode.

    python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram c/k6/mod/hello.c --cable-only
    python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram c/k6/mod/hello.c --key-file PATH

V1 0x6f arg 0 returns the current boot-local replay watermark (LE32).
The host chooses a newer serial and never resets that counter. V2:

| Command | Arg | Payload | Success data after result byte |
|---|---|---|---|
| 71 BEGIN | exact envelope bytes 25–4096 | empty | empty |
| 72 CHUNK | next byte offset | 1–128 contiguous bytes | empty |
| 73 COMMIT | exact envelope bytes | empty | signed LE32 verification result (positive code length, negative loader error) |
| 74 ABORT | 0 | empty | empty |

The accepted BEGIN enters one bounded exchange; no normal UI or pager
code accesses the staging buffer while it is owned. It discards any old
volatile pager message (as the existing module runner does). PTT/power
off, wrong prefix, bad CRC/length, unexpected command, out-of-order chunk,
500 ms frame-prefix timeout or 3.5 s total staging deadline abort it. The
100 ms frame body read is additional maximum latency. Invalid/incomplete
uploads clear the arena's staged state and restore normal control. No SPI
write, radio reset, or RF operation is requested by the upload itself.
During the upload the UI/RX scheduler is briefly blocked; physical
latency must be assessed before release. On COMMIT, the existing C loader
checks the exact standalone header and tag and rejects replays. The
verified image executes only after returning from the borrowed arena stack
to the audited main stack, through the regular module runner, with another
idle/power check. It is discarded after the run. RUN_EVENT 0x67 reports
use the COMMIT sequence and completion slot 255 to identify RAM. Do not
retry a commit if its response is lost; host timeout is not cancellation.
Long-running modules retain synchronous behavior and require cooperative
stop handling. Job status is now available for the most recent verified RAM
module; installed-module tracking and cancellation are still forthcoming.

Capability bit 11 (ram_status) advertises V1 command 0x75, arg = nonzero
signed-envelope serial (job ID). The CLI prints the ID before upload:

    python3 tools/k6_live.py --port /dev/ttyUSB0 ram-status JOB_ID

After the result byte, return state:u8 (0 unknown, 1 accepted, 2 completed),
load:LEi32, returned:u8, result:LEi32. Unknown has all remaining fields
zero. An accepted job has verified positive code length, no result yet.
Completed means the runner has finished, not necessarily that the module
returned (returned=0 means it did not). The single most recent verified
job is cached before emitting the completion event, independent of the
8-bit request sequence. Rejected uploads do not replace it. A newer
verified job replaces it; reboot clears it. Unknown is not permission to
retry; neither reports nor persistent history are cached. The synchronous
runner cannot service status mid-execution; a timeout is not cancellation.

V2 requests currently remain at 115200. The bounded downlink speed
negotiation is separate. Next: negotiated/validated sustained uploads,
cooperative job management, and typed persistent installation/removal.

Verified 2026-11-20: cable-only packed image 112316 bytes, stack layout
audit passed, six compiled-image emulator tests passed (status/tune,
installed module run, CAT, full-range bulk read, V2, RAM module). RAM test
executes hello.c with its two say lines and result 0x6b, rejects corrupted
tag, partial upload timeout, and replay in target, preserves SPI bytes
and VFO state. Independent host C builds verify signed, unkeyed and
CRC-only modes, exact envelope bounds and replay handling. No radio
flash, physical speed or RF validation in this checkpoint.

RAM status checkpoint: packed cable-only build 112548 bytes, 11776-byte
arena, 6304-byte stack, 280 bytes free in RAM0. All six compiled-image
emulator tests passed, including fresh host sequence recovery, unknown
IDs, rejection/replay preserving cached completion, and cache replacement.
No physical validation or firmware flashing was performed.
