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
panel exposes status, VFO tuning and experimental API-25 cooperative
module Run/Stop controls. Memories uses the live API by default; its
explicitly selected legacy checkbox restores the older rebooting read path. No fallback is automatic, including on timeout. Older firmware
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

V2 control requests remain at 115200. The data-only fast upload described
below is separate from the bounded downlink speed negotiation. Next:
cooperative job management, windowed uploads and typed persistent
installation/removal.

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

## Negotiated fast upload (experimental)

Capability bit 12 (fast_upload) requires SPI library, the expanded-stack
layout and serial transport. Older images retain the baseline V2 exchange.

    python3 tools/k6_live.py --port /dev/ttyUSB0 upload-probe
    python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram c/k6/mod/hello.c --cable-only
    python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram c/k6/mod/hello.c --cable-only --baseline-upload

The supported candidate is 192000 baud, the previously bench-tested
legacy sustained uplink rate. Do not assume the live version has the same
electrical reliability or effective throughput until it is measured.
The probe sends 4096 deterministic bytes, checking every byte at the
target plus frame CRCs, sequences, offsets and all UART error counters.
It neither writes SPI nor arms a module. The host tries it once per
LiveLink, verifies a baseline STATUS afterward, and caches the chosen
rate. A failed data-only probe can fall back to 115200 only after
verified recovery. Explicit BUSY/INVALID refusals do not trigger fallback.

| Command | Arg | Payload | Response data after result |
|---|---|---|---|
| 76 UPLOAD_PROBE | 4096 | empty | actual baud:LE32 |
| 77 RAM_FAST_BEGIN | exact envelope bytes 25–4096 | empty | actual baud:LE32 |
| 72 CHUNK (fast phase) | next byte offset | exact min(128, remaining) | empty |
| 78 RAM_READY | exact envelope bytes | empty | empty |

BEGIN/probe is normal V2 at 115200. Radio suppresses debug and ACKs at
baseline, waits 30 ms, then switches to 192000. Host changes baud and
waits 50 ms before sending. During the fast phase only the encoded V2
body and NUL are sent, omitting the six ASCII magic bytes; CRC still
covers the conceptual FLXLV2 prefix. No control command is dispatched.
One frame (at most 159 bytes on wire) is outstanding at a time so it
fits the 320-byte RX ring. Sequence increments modulo 256; each
128-byte chunk is verified and ACKed before the next. The probe checks
byte j against (j*73+j/256) modulo 256.

The target automatically restores 115200 after the final chunk or on
timeout, CRC, sequence, length, offset, UART or probe-data failure.
One fast frame has a 100 ms receive deadline and the total excursion
is bounded by 3.5 s plus the current frame and 30 ms guard. On failure
the radio drains the outstanding tail before restoring baud. The host
stops sending, restores its baud, waits one second of silence, clears
input, and verifies a fresh STATUS before proceeding. No legacy
handshake or reboot. A failed real upload is not automatically replayed.
A successfully staged RAM module requires a separate normal V2 READY
acknowledgment at 115200 before the single COMMIT request. An attempted
COMMIT without READY is rejected; a missing READY acknowledgment
causes no COMMIT. Existing full envelope verification/replay handling
and job-result recovery still apply.

This is stop-and-wait rather than a windowed sustained transfer. A
4 KiB negotiation costs time, especially for small one-shot probes;
use --baseline-upload when speed negotiation is undesirable. The
upload-probe command reports the selected baud and negotiation elapsed
time, not measured effective application throughput. No radio or
electrical testing has been performed on this live implementation.

Fast upload checkpoint: packed cable-only firmware 113284 bytes, RAM
audit passed (11776-byte arena, 6304-byte stack, 280 bytes free in RAM0).
38 focused host regression tests passed (27 optional skipped), plus all
seven compiled-image live tests. The new emulator test verifies the
192000/115200 UART divisor transitions, full deterministic probe,
missing READY refusal, corrupt-chunk recovery, successful temporary
execution and unchanged SPI. Host tests separately cover fallback, lost
READY, failed recovery, explicit busy and no automatic commit/replay.

## Live persistent foreground modules (packed KUL2)

Capability bit 13 (lib_mutate) requires the expanded-stack SPI library
and KUL2 user store. It does not appear in fixed-slot firmware. No
automatic storage migration, formatting, legacy handshake or reboot.
The first version accepted 4096 bytes; capability bit 15 (lib_large) raises the limit to 7168 bytes. The current version accepts exact K6M envelopes up to 7168 bytes
(including header/tag) in ordinary slots 0–8 and 16–31. Supported flags
are LIB (1) and LIB|REMOVE (9), with nonzero supported API level and
zero telemetry field; removal must contain the exact K6ERASE payload
and zero entry. Other objects (themes, pages, cards, resident engines,
unknown or structurally invalid data) are refused, including in user
slots. Existing destination classification uses the header, exact
packed extent and type; the incoming envelope is fully authenticated.
A new install never executes the module.

    python3 tools/k6_live.py --port /dev/ttyUSB0 install-module 24 c/k6/mod/hello.c --cable-only
    python3 tools/k6_live.py --port /dev/ttyUSB0 run 24
    python3 tools/k6_live.py --port /dev/ttyUSB0 remove-module 24 --cable-only

For keyed builds use --key-file PATH instead. Optional --envelope-out
PATH writes the exact signed transaction to a new local file before
sending; the CLI prints slot, serial and SHA256. To inspect the resulting
logical state later, including after a lost acknowledgment:

    python3 tools/k6_live.py --port /dev/ttyUSB0 verify-module PATH

This read-only command compares exact bytes/length or checks absence
using a freshly opened packed directory; it does not authenticate a
new request, consume a serial or prove which mutation caused the state.
A mismatch returns a nonzero exit status. --baseline-upload
disables speed negotiation. The user should select the intended slot
explicitly; installing into an occupied ordinary module slot replaces it.

| Command | Arg | Payload | Success data after result |
|---|---|---|---|
| 79 LIB_BEGIN | exact envelope bytes | empty | empty |
| 7a LIB_FAST_BEGIN | exact envelope bytes | empty | actual baud:LE32 |
| 7b LIB_COMMIT | exact envelope bytes | empty | signed LE32 result |

Reuse the existing RAM_CHUNK/READY/ABORT staging exchange and the 192k
data-only fast upload. The transaction type is locked at BEGIN: RAM
COMMIT cannot execute a library transaction, and LIB_COMMIT cannot
write a RAM execution transaction. COMMIT runs at baseline with the
staged data owned by the borrowed stack. The loader checks exact
length, permitted header, tag and boot-local replay serial, never
arms execution, then the packed library checks the destination type
and uses its normal copy-on-write allocator and verified directory
publication. Raw PTT and power switch are checked before each flash
erase/program in addition to the normal idle guard. RF operations are
not requested. Authentication failures return loader errors minus
100; storage failures use KL_* directly. A valid envelope can consume
its replay serial even if the subsequent storage operation refuses.

Positive commit result is the exact installed envelope length;
successful removal returns zero. The host invalidates the cached
directory, reopens it, and checks exact length and every installed
byte (or absence of the removed slot). The host allows at least ten
seconds for SPI commit; no retries, including on timeout, nor
automatic legacy fallback. A lost acknowledgment is an unknown
outcome: reopen inventory and compare exact bytes/serial/SHA256 with
the saved envelope, or check absence for removal, before deciding
on another operation. Readback failure after ACK also requires
inspection. The packed allocator retains the old extent until
publishing the new directory magic and verifies writes. Removed and
superseded bytes can persist in unallocated sectors until reuse:
removal is logical, not secure physical erasure.

Ordinary Catalog and format-1 bundle installs/removals now default to live serial. Pack provisioning still uses the explicit legacy pipeline. Larger modules outside the advertised capacity,
system bundles, fixed-slot mutation, themes and resident providers
continue to use their existing explicit legacy workflows. Physical
flash timing, interrupted-power recovery and button/audio behavior
need bench validation before release.

Persistent module checkpoint: 55 focused host tests passed (27 optional
skipped), including keyed C verification, typed protection and injected
power cuts at every allocator write/erase for replace/remove. All eight
compiled-image tests passed on the corrected cable-only packed firmware
(113852 bytes, 11776-byte arena, 6304-byte stack, 280 bytes free RAM0).
Repeated builds exposed and fixed an accumulating watchdog guard in
apply-lean-txdog.py. Two corrected builds match byte-for-byte outside
their 8-byte GNU linker build IDs. No physical flash or radio tests.

### Live installer integration and expanded staging

Bit 14 (module_caps) adds V2 opcode 0x7c, arg zero, empty payload
for the 16-byte K6CP module capability record or 16-byte nonce for
the 25-byte record/status/SipHash proof. Legacy and live use the same
record and domain-separated K6CAPAU1 proof. This reports firmware
identity, not an independent measurement of its flash contents.
Live INFO now supplies the build ID for lean SPI-library hosts too.
Bit 15 (lib_large) allows persistent envelopes through 7168 bytes
while keeping 4608 bytes available in the 11776-byte arena. RAM
execution remains limited to 4096 bytes. Older bit-13-only live
firmware retains 4096-byte capacity.

The format-1 installer and Catalog preflight authentication, build
ID, API and required services, slot allocation, protected objects,
duplicate destinations and every selected envelope size before the
first write. Exact unchanged signed bundles are skipped. Each change
is committed once and verified; batches are not a single atomic
transaction. If any commit fails, subsequent entries are not sent.
The installer never falls back to the legacy loader implicitly.
Use load/remove-slot/remove-slots --legacy explicitly where needed.

### Physical acceptance test

Keep physical radio access exclusive; coordinate with the CW and
GP0 research agents. Test in awake RX standby, no PTT, stable supply,
and with an appropriate antenna or dummy load. First identify the
installed image and the current packed library, then preserve a
verified full SPI backup and an independently known-good firmware
image before any MCU flash. Never use the test to implicitly format
or migrate storage. Confirm actual build ID before mutation.

Read-only acceptance test (no loader, reset, tuning or TX command):

    python3 tools/k6_live_bench.py --port /dev/ttyUSB0 --output-dir ~/flux-bench/live-read --backup

The report contains status, identity, selected baud rates, elapsed
times, repeated calibration verification and backup hash. This works
only on live-enabled firmware with the negotiated transports.

Once the exact test image is installed and an ordinary empty slot
has been identified, explicitly permit a module lifecycle test:

    python3 tools/k6_live_bench.py --port /dev/ttyUSB0 --output-dir ~/flux-bench/live-lifecycle --backup --expect-build-id BUILD_ID --exercise-slot EMPTY_SLOT

The test executes a four-byte return-42 Thumb routine with no RF
calls, checks its RAM job cache, installs it into the empty slot,
runs it, removes it and verifies unchanged logical allocations,
calibration and VFO settings. Keyed hosts use --key-file. It saves
signed envelopes before sending and durable checkpoints throughout.
A failure stops immediately without automatic cleanup, retry or
legacy fallback. No intentional transmission or power cut. The
physical pass must check display responsiveness, no resets and
actual link reliability; emulation is not electrical validation.

Expanded-staging checkpoint: 73 host tests passed (27 optional tests
skipped), and all eight compiled-image emulator tests passed, including
the exact 7168-byte persistent replacement and full readback. The
bench script is tested with a mock for its read-only path. Firmware
114012 bytes, arena 11776, stack reservation 6304, 280 bytes free RAM0.
Candidate build ID 1c66cf6e9171a15f. Physical tests remain outstanding.

### Physical bench result — 2026-11-20

Device: K6, PL2303 cable (/dev/ttyUSB0), ~7.41 V, idle RX.
Original CW sidetone build ed66a934196bcefc, packed KUL2, cable-only.
Exact rollback image preserved locally, along with full SPI backup.
Legacy negotiated 500 kbaud backup of 2097152 bytes: 53 s.
Candidate 1c66cf6e9171a15f flashed with no format/migration. Live
backup negotiated 500 kbaud and completed in 102.004 s; byte-for-byte
identical to the legacy backup (SHA256
6b895b94ab1218a8f47ae3074f5999b2f9db90b6e07af39b96d7ad6d0001cb5f).
Synthetic 4096-byte uplink negotiated 192 kbaud in 0.469 s.
Live downlink is currently ~20.1 KiB/s, legacy ~38.6 KiB/s:
the bounded 4 KiB bursts, baud guards and turnaround add overhead.
Larger bounded sustained read windows are a future optimization.

First high-speed RAM upload failed at baseline READY; recovered
115200 and status without COMMIT or serial consumption. A separate
baseline BEGIN/CHUNK test reproduced the failure. Diagnosis: PA10
is both RX and PTT; the waiting-for-prefix loop sampled instantaneous
raw PTT during incoming UART start/data bits. Corrected prefix wait
to abort only after 10 ms continuously low, retaining immediate
power-off checks and raw PTT checks at frame/commit boundaries.
This does not enable remote PTT. Independent held-PTT, power switch
and deliberate fault-injection hardware testing remains outstanding.

Corrected build ffd8ca7e1b5f0db7 (114076 bytes; 11776-byte arena,
6304-byte main stack, 280 free RAM0) flashed after checking prior
backup, identity, calibration and directory. Three targeted compiled
emulator regressions passed. Subsequent physical live-only lifecycle:
- 115200 and 192000 RAM return-42: ~0.273 and ~0.370 s respectively,
  including job-status recovery. Small image baseline faster.
- Empty slot 26: 28-byte signed envelope installed/read back in
  ~1.300 s, executed with return 42.
- Replaced with exactly 7168 bytes, installed/read back in ~2.406 s,
  executed with return 42.
- Authenticated removal verified; original logical directory and all
  26 pre-existing packed objects byte-for-byte identical to backup.
- Original VFO settings and calibration unchanged, idle RX, replay
  watermark equals last removal serial; no intervening reboot.
- Actual Hamlib rigctl 4.6.5 NET backend (rig model 2), local loopback:
  f/m/v/t/l STRENGTH passed, DCD via bridge=0, frequency changed
  147790000 -> 147800000 -> 147790000, FM 15000 -> 8000 -> 15000
  restored. Final status idle RX; health ADC sums 29906/24047,
  battery 741 cV. RSSI estimate not calibrated; bandwidth values
  are nominal. No intentional TX commands or RF tests.

Private local bench artifacts:
~/flux-bench/live-preflash-20261120T200108Z (SPI backup, rollback)
~/flux-bench/live-candidate-20261120 (equal SPI backup, failed READY)
~/flux-bench/live-pttfix-20261120 (successful lifecycle and Hamlib JSON,
signed envelopes). No raw dumps or keys committed. Radio left running
corrected live candidate, not the experimental CW sidetone firmware.
Physical display/button observations, independent PTT/power-off holds,
keyed firmware bench and Windows/other cable validation remain open.

## 32 KiB live downlink window (extended read capability)

For long read-only dumps, capability bit 16 (`bulk_stream`) introduces
V1 opcode `0x7d`. It has a different wire encoding from the original
`0x6b`, which remains bit-for-bit compatible with earlier firmware:

- LE32 argument bits 0–14: physical SPI address / 64; bits 15–22:
  count of 256-byte frames, 1–128; bits 23–24: rate ID, **2 only**
  (500000 baud); bits 25–31 must be zero.
- ACK at 115200: status byte + actual hardware baud LE32. Target
  guards for 30 ms before switching to 500000, then sends up to
  128 CPS frames with `0x6c`, matching 8-bit sequence, absolute
  physical address LE32 and 256 data bytes. Each carries independent
  CRC16/XMODEM and gets no host response.
- Completion: `0x6d` with status byte, number of frames sent LE16,
  then four LE32 deltas for UART overrun/framing/noise/ring overflow.
  The old `0x6b` completion retains its original 8-bit count.
- The firmware checks physical PTT, power-off and spurious incoming
  bytes between frames and aborts without writing NOR on refusal.
  A single 32 KiB window is a bounded foreground pause of roughly
  700 ms at 500000; the host must not transmit until the radio has
  restored 115200.

The host first negotiates 500000 with the original 4 KiB data check,
then probes the new protocol by comparing an **8 KiB long burst** with
two independently verified 4 KiB reads. Only exact agreement enables
32 KiB windows. If a long frame is missing, corrupt, out of sequence
or accompanied by nonzero UART errors, the host waits at least
1.2 seconds without transmitting, restores 115200 and confirms STATUS.
It then retries **only the read** through validated short bursts. It
never retries module execution or SPI mutations or silently enters the
legacy loader. Requests near the chip end adjust the probe location so
the 8 KiB comparison remains in bounds.

Test status at this checkpoint: 77 host regressions passed, 27
optional tests skipped; all 8 compiled-firmware emulator regressions
passed, including exact 32 KiB read, terminal-chip boundary, invalid
count/rate/bounds, unchanged SPI and UART baseline recovery.

Physical bench, same radio and saved flash content as the 2026-11-20
initial acceptance: the first 256 KiB verified byte-for-byte in
14.441 s through 4 KiB bursts versus 7.560 s using 32 KiB streams,
a 1.91x improvement (17.7 versus 33.9 KiB/s). These are full host
read times, not theoretical baud throughput.

The full physical 2 MiB scan took **60.704 seconds** (33.74 KiB/s),
compared with 102.004 seconds for the previous live 4 KiB implementation
and 53 seconds using the rebooting legacy cable transport. This is
1.68x faster than previous live, with no reboot during download.

A SHA256 comparison to the November 20 pre-module-test backup initially
reported a mismatch. This was expected physical KUL2 copy-on-write
history: tests installed, replaced and logically removed slot 26 *after*
that backup, updating two directory sectors and free allocation sectors.
An independently completed full 2 MiB long-stream read located every
difference inside exactly five library sectors:

    0x110000  0x111000  0x156000  0x15c000  0x15d000

Every byte outside 0x110000–0x190000 matches the original backup,
and all 26 existing logical library objects match the original image
byte-for-byte. The current physical flash capture has SHA256
6430e693ab31c46714a9dae5141d95778087f65b99e2326813505bcbbbe0bc99.
The changed KUL2 directory was additionally read through the old 4 KiB
transport and agreed exactly with the 32 KiB read; a repeated 32 KiB
window also matched. The radio reported 500 kbaud and stayed in
RX standby with the previous VFOs and calibration.

Candidate firmware build ID 807d1b7f53286cbc, 114252 bytes,
physical image read-only test passed with no flash writes after
the firmware replacement. The previous live build
ffd8ca7e1b5f0db7 and the private pre-flash backup are preserved
under ~/flux-bench/live-stream-20261120 and earlier benchmark folders.

To repeat a controlled, read-only physical throughput comparison from
the bench utility (does not flash, format, run modules or transmit):

    python3 tools/k6_live_bench.py --port /dev/ttyUSB0 --output-dir ~/flux-bench/next-read-comparison --expect-build-id 807d1b7f53286cbc --compare-download

The option stages two 256 KiB reads on the same live link, first with
short 4 KiB windows and then with capability-verified long 32 KiB
windows. It requires exact byte agreement, retains the live serial
lock, and writes only a local JSON report. A failed stream probe,
data mismatch or fallback is not reported as a speedup. Another full
physical read-only run on 2026-11-20 reproduced **14.406 s vs
7.583 s (1.90x)** with successful final RX status and unchanged VFOs.

A post-update actual Hamlib 4.6.5 rigctl NET smoke test also passed
`f/m/v/t/l STRENGTH` on the streaming build: 147790000 Hz,
FM 15000, VFOA, RX/PTT 0 and S-meter +6 dB relative to nominal S9,
with unchanged radio status before/after. No TX command was sent.

## Cooperative foreground cancellation (API 25, experimental)

**Scope:** The new `run-coop` launch is opt-in and serial-cable-only.
It does not interrupt arbitrary native code, reset the MCU, retune,
key the transmitter, or change the existing `run` and `run-ram`
interfaces. **A module must explicitly call `api->cancel_poll()`
and return by itself.** Module completion, not an on-wire request or
a timeout, is the definitive evidence that module code has stopped.

### API

The append-only C `k6_api_t` adds
`int (*cancel_poll)(void)` at level 25. The module must check
`api->level >= 25` before calling it. Returns:

- `-1`: not an opt-in live launch, or exclusive KISS UART ownership.
- `0`: active, no verified cancellation request.
- `1`: exact active token accepted; latched until module completion.

A module should voluntarily unwind its own state, call TX-off if
applicable, and return (our strictly RX-only smoke module returns
`-125`). **The host does not automatically call TX-off for a
noncooperating module, nor is this a substitute for local hardware
TX/power watchdogs.** An application using level-14 raw KISS mode
must not simultaneously use this polling interface.

### Cable protocol

Bit 17 `coop_cancel` in FLCP advertises support; K6CP module API
ceiling reports 25 for live+SPI-library builds. Existing V1 opcode
`0x66` is unchanged.

- `0x7e RUN_COOP` is normal V2 with arg bits 0–4 library slot,
  bit 8 explicitly allowing a higher module API, all others zero;
  payload is an 8-byte nonzero cryptographically random run token.
  The normal idle, module-signature, level and library guards run
  before the token is armed. Accepted status is an empty success
  payload. The familiar RUN_EVENT 0x67 reports and completion still
  use the original launch request's 8-bit sequence.
- `0x7f RUN_CANCEL` uses the **same 8-bit launch sequence**, arg 0,
  and the exact 8-byte run token, in one canonical V2-framed command.
  While a live module is running, normal serial dispatch is unavailable;
  instead `cancel_poll()` incrementally reads at most 64 buffered
  UART bytes per call, checks framing, canonical padding, length,
  sequence, token, argument and CRC, then latches the request.
  A matching request produces a one-byte status-0 acknowledgment on
  0x7f; wrong tokens and malformed frames are ignored. Ordinary
  main-loop dispatch cannot cancel a running native module.
- The host sends a cancellation **once**, without changing the
  request sequence, never falling back to the legacy loader or
  retrying a mutation. It reports `cancel_requested` separately
  from `cancel_acknowledged`. Only a normal completion with
  acknowledgment and module return `-125` is marked
  `cancelled=true`. If a deadline expires, the native execution
  state is unknown; do not relaunch blindly.

The token is a session-scoped 64-bit bearer capability on the
**physically exclusive programming cable**, not a proof of the
firmware signing key or a network authentication mechanism. It is
cleared at module completion, and each new launch uses a new random
value. Remote PTT remains disabled.

For a cooperative already-installed module:

    python3 tools/k6_live.py --port /dev/ttyUSB0 run-coop 26 --allow-higher-api --cancel-after 0.5 --timeout 10

The `--allow-higher-api` switch is mandatory for a level-25
module; it is a separate acknowledgment that higher-level module
APIs may include RF/TX and persistent writes. Never use this switch
on untrusted module code. The test-only
`c/k6/mod/live_cancel_demo.c` is level 25, sends brief status lines,
does **no RF or SPI writes**, checks PTT/power-off through its
existing keys and polls cancellation every 10 ms. It returns 42 on
its natural 3-second completion, -125 on cancellation.

### Physical acceptance — 2026-11-20

The bounded cancellation implementation was exercised with the
physical radio on the PL2303 cable and current KUL2 packed library.
RX-only candidate build ID ce46aac935c96e97, 115008 firmware
bytes, 11776-byte arena, 6256-byte main stack and 280 RAM0 bytes
free. The prior verified live-stream build 807d1b7f53286cbc
and complete private physical SPI snapshot were preserved before
flashing. Capability API ceiling 25, calibration and original
26-object directory checks passed.

The API-25 demo compiled to 186 bytes (210-byte cable-only
envelope) and installed in confirmed empty slot 26 with live readback:
- Token-bound stop requested after 500 ms: completed in 0.635 s,
  returned -125, with cancel_requested, cancel_acknowledged and
  cancelled all true. Reports: COOP: started, COOP: cancelled.
- Second fresh-token run with no cancellation returned 42 after
  3.201 s (COOP: completed), cancelled=false, confirming no stale
  cancellation latch.
- The module was removed. All 26 original library objects matched
  the pre-test SPI snapshot byte-for-byte. Factory calibration
  and VFOs remained unchanged; final state idle RX, no intentional TX.
- No reboot between install, two runs and removal. Replay watermark
  ended at the removal serial 1795211529.

The CLI first Ctrl+C requests a token-bound cooperative stop, not
a forced unwind or serial abandonment. It distinguishes requested,
acknowledged and module-completed states; timeouts mean unknown
execution. Ctrl+C UI was unit-tested; the physical run used
--cancel-after. Source: c/k6/mod/live_cancel_demo.c. Private test
envelopes and report: ~/flux-bench/live-coop-20261120/.

### Reproducible build gating

The default C module loader remains at API ceiling 23. The C build
enables ceiling 24 for the dedicated raw-FSK build and 25 only when
K6_LIVE_SERIAL=1. The generated Aura overlay's level initializer
is normalized before reapplying the older API overlays on repeated
builds; this avoids incorrectly broadening non-live module acceptance.

A clean repeated build after these gating corrections reported
build ID 21a68dd562c601bd. It is exactly 115008 bytes like the
physically tested ce46aac935c96e97 image; a byte-by-byte comparison
found **only the eight embedded build-ID bytes** differ, at
0x1c0e0–0x1c0e7. The runtime machine code is identical. The
new build passed the compiled-FW cooperative cancellation test.
The physical radio was deliberately left on the fully tested
ce46aac935c96e97 build rather than flashed again just to change
its self-reported ID.


## Catalog cooperative runner acceptance

The Catalog's My radio panel now has an API-25 module runner, disabled
until the server administrator explicitly sets `FLUX_CATALOG_COOP_ENABLED=1`
(loopback only) or additionally `FLUX_CATALOG_COOP_REMOTE=1` with external
authentication. It uses single-job serial-port ownership, CSRF/session ownership checks,
a separate foreground worker, streamed reports, and one-shot stop
requests. An uncertain result blocks further launches until a successful
read-only idle-radio recheck. A button press is **not** proof the module
stopped; only a returned, acknowledged -125 job is reported cancelled.
The Catalog never automatically uploads, executes, or flashes modules.

See [release hardening and remaining physical safety tests](k6-live-release-checklist.md).


## Later physical acceptance

The subsequent power-guard build `f693147e70527a58` was flashed on the physical K6 and passed RX-only live and Hamlib readback, library and calibration preservation, and abandoned-stream recovery. The earlier statement that the radio remained on build `ce46aac935c96e97` refers to the previous checkpoint. See the [2026-11-20 physical acceptance report](k6-live-physical-acceptance-20261120.md). Local PTT/power-switch and physical unplug tests are still pending.
