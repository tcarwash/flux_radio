# Live serial / Hamlib roadmap

Branch dev/live-serial-hamlib, based on origin/main d3ddd6b.
CW checkpoint 746e2f1 on research/tx-injection; GP0 module-run port 592cb36
comes from f734ad1, without importing the experimental RF code. Preserve
the legacy loader and the fixed-width FLXLV1 wire protocol. No radio flash
or RF test without coordination with the research agents.

## Milestones

1. Installed foreground modules with framed reports; CAT get/set frequency,
   mode, VFO, capability query, MCU health; local NET rigctl server.
2. Variable-length v2 request transport. Preserve v1 decoder. Explicit
   capability/schema negotiation at 115200. A proposed new magic separates
   v2 from cable and v1. Bounded frame length, LE job/transaction IDs,
   sequence numbers and CRC; input parsing incremental with timeout and
   deterministic resync. Select payload limit only after measuring ring
   capacity and stack; malformed/truncated frames must never dispatch.
   Use existing CPS response framing where possible. Reserve v1 opcodes
   0x63–0x65 for research diagnostics/health and 0x66–0x6a for current APIs.
3. Read-only live inventory and typed module metadata; signed temporary
   RAM image staging and execution. Reuse km_packet/verification and normal
   dispatcher; validate actual arena/code/scratch/stack ownership. No
   arbitrary memory writes, unsigned-code default, or replay bypass.
   Stage chunks in a bounded transaction; verify commit before executing.
4. Persistent module install/removal via the same signed, typed packed
   library routines. Atomic commit/readback/recovery, no flash writes while
   transmitting, no legacy loader fallback on a busy/timeout response.
5. Job IDs, bounded completion cache, reconnect/status recovery and
   cooperative cancellation. The synchronous module runner cannot service
   arbitrary App operations from its stack. Poll only a narrowly scoped
   stop/status channel at existing safe module yield points, keep signed
   module semantics, return pending/cancelled/completed separately. A host
   timeout is not a cancellation. Do not blindly retry execution.
6. Binary result frames with stream/job IDs, chunk sequence, gap and drop
   counters, size/credit limits. No unbounded allocation on radio/host.
   Integrate existing negotiated high-rate transport and IF/IQ/FSK services.
7. CAT controls: calibrated RSSI, squelch, power, offsets, split/tones,
   memory select/write, telemetry subscriptions; Hamlib client compatibility
   tests. Native asynchronous CWID, remote PTT only with independently
   verified lease and TX watchdog, local abort/override, band/lock/busy
   restrictions and release on disconnect. No general raw register write
   CAT operation. Bridge local-only initially; no default remote access.

## Verification gates

Host tests, actual Hamlib NET client, fixed-slot and packed compiled-image
emulation, repeated overlay builds, exact flash/RAM size budgets, malformed
frames/timeout/reconnect/corrupted module tests, no SPI modifications on
read/volatile CAT paths. Bench use: verify no unintended PTT or reboot,
mode/frequency changes, local control and audio continuity, safety checks
with properly rated RF load. Keep observations separate from claims.
Consider a native upstream Hamlib backend once wire ABI is stable.

## Direction: primary live interface

Normal operation, readback, inventory, and development should use live serial.
Retain the legacy transport for bootloader entry, MCU flash, stock/factory
provisioning and explicit recovery; migrate other mutations once their live
transactions have been verified. No automatic legacy fallback.

Transport-first checkpoint: v1 bounded downlink bursts and auto-probe at
500/192/115.2 kbaud, full SPI backup and library INFO/inventory. Separate
uplink negotiation is still required for staged variable-length module
transfer. Keep release feature enablement gated on complete profile size/
stack audits and physical verification. Do not claim the prior legacy
40 kB/s measurement as a live result.

### V2/RAM checkpoint (2026-11-20)

Initial V2 and temporary execution implemented: binary-safe 128-byte
payloads, bounded staging in arena and explicit verified commit, replay
watermark, structured reports/return code. Require K6_RAM0=stack for
temporary execution; the previous small-stack emulator build corrupted
the UART binary-mode flag, so full relocation with the original arena
and boot constructor fix were ported from CW research. Expanded-stack
image passed all compiled-image tests. The initial 4 KiB/API 1–2 limits
are deliberate; no arbitrary unsigned execution on keyed firmware.
Cable-only firmware retains its existing CRC-only physical-trust model.
Remaining: physical validation, windowed sustained uplink, cooperative
cancel/job IDs and reconnect, larger module streaming/arena sizing,
live persistent mutations and full CAT/remote TX safety.

### RAM job recovery

The signed-envelope serial identifies the most recent verified RAM job.
A new read-only V1 status request (0x75, capability bit 11) recovers its
completion and return code after a dropped completion frame/reconnect.
History is one entry, boot-local; unknown does not prove non-execution.
Still needed: installed-job IDs, bounded multi-job history, cooperative
status/cancel during execution and binary/report replay.

### Fast uplink checkpoint

Capability bit 12: bounded 192 kbaud data-only upload, 4 KiB read-only
integrity probe, per-chunk CRC/sequence/offset/length/UART checks, baseline
READY before execution and automatic baseline restoration. Failed probe
can fall back after verified recovery; actual uploads/commits are never
automatically retried. Stop-and-wait fits the existing RX ring. Physical
baud/timing and effective throughput remain to be tested. Future
persistent writes can reuse the same staging exchange but need a separate
typed authenticated mutation transaction and readback/recovery contract.

### Persistent ordinary modules

Packed KUL2-only, exact authenticated foreground module install/replace
and removal in user slots, up to 4096 bytes per envelope. Shared
baseline/fast data-only staging, distinct transaction and commit opcodes,
reuse kl_install/kl_remove, raw PTT/power checks on mutation callbacks,
copy-on-write and complete readback. No automatic mutation retry or
legacy fallback. CLI can save exact envelope for recovery. Remaining:
large-object staging, Catalog integration, typed management of system
objects and physical validation. Fixed-slot upgrades remain explicit
legacy operations to avoid promising atomicity they do not have.

### Installer integration / larger uploads

Live capability/key proof shares the legacy K6CP record; lean INFO
reports the linker build ID. Persistent staging up to 7168 bytes,
RAM execution unchanged. Format-1 load and module removals default
to live; Catalog inherits this and offers explicit legacy mode.
System/pack provisioning still uses the legacy path. A guarded
read-only bench tool and explicitly authorized empty-slot lifecycle
test document the next physical acceptance pass. Coordinate access
with all RF research agents before contacting or flashing hardware.

### First physical acceptance

Physical packed cable-only K6: live 500k downlink/192k uplink and
exact 2 MiB backup, baseline and fast temporary RAM, job recovery,
28- and 7168-byte persistent install/execute/replace/remove, all
26 original library objects verified intact; actual Hamlib 4.6.5
NET read/tune/mode restore. Fixed PA10 UART/PTT shared-pin false
abort at staged command prefix (10 ms sustained low). Live backup
102 s vs legacy 53 s; sustained bounded download windows are next.
No intentional TX. Physical held-PTT/power interruption, keypad
responsiveness, keyed firmware, and broader cable coverage remain.

### 32 KiB downlink streaming

New capability bit 16 and V1 0x7d allow 128 x 256-byte framed
physical SPI reads per bounded 500kbaud excursion, preserving old
0x6b as a fallback. Host verifies an 8 KiB long-window probe against
two short windows and uses 1.2 s silent recovery; only reads can retry.
Simulated full suite: 8/8, host suite 76 passed. First physical 256 KiB
read: 14.441 s old 4 KiB versus 7.560 s long 32 KiB, identical bytes,
a 1.91x improvement. Full physical 2 MiB read completed in 60.704 s at 33.74 KiB/s,
versus previous live 102.004 s and legacy 53 s. The old pre-lifecycle
flash hash differed only inside five KUL2 sectors changed by earlier
install/replace/delete transactions. All 26 original logical objects
and every byte outside KUL2 are unchanged; both short and long read
methods agree on the updated physical directory. Firmware remains
RX-only with no remote PTT.

The optional `k6_live_bench.py --compare-download` command now
automates identical-range 256 KiB throughput checks without writes.
Second physical run: 14.406 s old vs 7.583 s extended (1.90x),
both exactly equal, no resets and unchanged RX/VFO status.
Cooperative module stop remains separate: ordinary native code runs
synchronously, so serial commands cannot preempt it safely. A future
stop protocol must involve an explicit module yield/check API, an
authenticated per-job cancellation token and an independently verified
hardware watchdog for physical PTT and power-off; timeout by itself
must never be described as cancellation.

### Token-bound cooperative module exit

Added optional V2 0x7e run launch and 0x7f stop request with a random
8-byte bearer token scoped to the launch sequence. The append-only
module API level 25 exposes `cancel_poll()`: only the native module
can decide to return. Wrong tokens/CRC never set the stop flag;
cancellation ACK and returned -125 are tracked separately from request
intent. No RX/TX transceiver forced halt, loader call, reset, or
automatic retry. Standard V1 installed launch and API 1–2 temporary
RAM launching are unchanged. Do not confuse this cooperative contract
with preemptive or safety-critical cancellation. Test with a
no-RF 3-second module before any experiment with TX code.

Physical validation 2026-11-20: RX-only API-25 smoke module
installed into empty KUL2 slot 26 and returned -125 with an
acknowledged matching token in 0.635 s (stop requested at 0.5 s).
A second fresh-token launch ran its full 3.201 s and returned 42,
demonstrating a cleared stop state. Live removal verified; all 26
original library objects and factory calibration matched the
snapshot, and radio remained idle RX with original VFOs.
No RF transmission intentionally generated. Details:
~/flux-bench/live-coop-20261120/.

Build compatibility gate: default C verifier ceiling remains 23,
raw-FSK non-live remains 24, live serial + SPI module ABI is 25.
The reproducibility build changes only eight build-ID metadata bytes
relative to the physical acceptance image. No unnecessary second
flash was performed.
