# Flux live DV: Cortex-M0 guarded native-FSK packer and bounded two-packet queue

Date: 2026-11-23. This work is intentionally **offline until a separate
queue-approved FSK-15 inert on-target module test**. It builds upon
[FSK-13 8/8 guarded RF PASS](k6-fsk13-guard33-sotto-physical-pass-20261123.md)
and [FSK-14 4/4 K6 microphone RF and host audio PASS](k6-fsk14-real-mic-guard-physical-pass-20261123.md).
FSK-13/14 used **pre-encoded** Sotto packets; they did not prove a live
resident K6 speech encoder or continuous FIFO refill.

## Actual implemented code

- `c/k6/mod/dv_guard33_packet.h` is a freestanding C11/Cortex-M0
  **packet builder**, not a register driver. It takes 1..8 existing,
  completed 6-byte Sotto-1200 superframes and emits exactly **33
  little-endian FIFO words**: one 64-byte Flux L64 packet (magic
  `0xA9`, msg_id, sequence 0, depth 1, total 1..8, 48-byte
  voice slots with zero padding, 8 reserved zero bytes,
  CRC16-CCITT-FALSE) whitened with PN9 initial state `0x1FF`;
  followed by the proven sacrificial last `0xA55A` guard word.
  The implementation writes bytes directly into the 66-byte
  destination with streaming CRC/PN9; **no second 64-byte scratch
  copy, malloc, printf or I/O**.
- Critical protocol correction from byte-for-byte cross-validation:
  the Flux **depth-1** L64 profile **never sets the short-group
  marker 0x2000**, even if total is 1..7, because its group contains
  exactly one packet. A naive implementation that sets it on a
  short packet produces a byte-for-byte wire mismatch despite a
  superficially plausible header. The 1..8 total field alone
  indicates how many 40ms superframes are real. This is tested.
- `c/k6/mod/dv_guard33_stream.h` is a bounded, **caller-owned
  190-byte** packet assembly/queue object with **two 33-word
  preloaded-FIFO packets** plus a **48-byte** partial 8-frame speech
  segment. It accepts incremental six-byte Sotto completions, assigns
  monotonically increasing message IDs, stages every full 320ms
  segment, and closes a partial 1..7-frame segment on PTT release.
- If both queue slots are full, it **does not consume the eighth frame**
  or forget the final partial segment. It returns explicit
  `DV_STREAM_BACKPRESSURE` so the outer controller can drain a
  confirmed FSK TX completion or record an overrun and abort;
  silently dropping completed audio is not permitted. The frame
  must be retried unchanged after space is available.
- It forbids message-ID reuse after ID 255; the typical start ID
  112 allows 144 segments (~46 seconds). The higher protocol
  must define call/session identity, end-of-PTT, erasures,
  sequence rollover and any retries or forward error correction.
  **Those wire semantics are not invented or silently assumed here.**
- `c/k6/mod/dv_guard33_selftest.c` is an **inert** Cortex-M0 API3
  module: creates two **synthetic** data segments in stack RAM,
  computes full and partial 33-word L64+guard packets, compares
  every word to two independent Python `modem.pager` golden vectors,
  and reports PASS with return value **33**. It does **not** call
  `tx_on`, `tx_pa`, `fd_write`, `fd_read`, GPIO, ADC, timer, or
  flash operations. Host build yields an actual **1684-byte** API3
  radio module (entry 0).
  Source SHA256
  `d7793ebb887cc13c4d1079ec9db2055250e501da54f1ac9e75c9cd413fd88bb3`;
  compiled code SHA256
  `650c3493580b5e53102b46715eb78cc38e6faa79c21632f89b41f60bc1a560ea`.
  Hashes are frozen in the bench wrapper.
- The separately guarded `tools/k6_dv_guard33_selftest_bench.py`
  reuses the already exercised signed-slot/VFO restore transaction.
  Even though the module **does not TX**, this serial/SPI workflow
  must claim an exclusive queue/SDR lease and restore the trusted
  signed module. The wrapper requires both operator-approved lease
  and dummy-load flags, module source/code hash, and returns fail
  closed. The shared legacy harness incorrectly calls any matching
  module return `transmission_confirmed_by_firmware`; for this
  self-test, that flag **does not mean RF happened**.
- Tests independently compile the packetizer and queue for
  host clang **and actual ARMv6-M**, check 1..8 frame segments,
  6 start IDs including 255, synthetic randomized payloads, actual
  private 26-frame recorded K6 Sotto codec data, complete CRC/PN9
  parity with Python and exact normal/prefix-displaced guard
  recovery. Separate queue tests assert backpressure, no lost final
  partial, two-slot rotation, no message-ID wrap, and zero audio
  bytes committed to Git.

## Memory/CPU: still a real blocking problem

The compiled Cortex-M0 **queue test harness** has `.text=768B`
(including test wrapper entrypoints) and `.bss=190B` as a test-only
global. **A signed K6 RAM module may NOT rely on `.bss` at all**:
the real on-target API3 selftest declares the 190B object on the
stack/caller-provided arena. The 768B test object is *not* a linked
incremental Sotto encoder size and does not account for integrated
scheduler and ADC; treat as a rough byte budget only.

The parallel [full codec budget](https://github.com/tcarwash/flux_radio/blob/research/offline-sotto-fsk-dv-20261123/docs/k6-sotto-slim-mcu-static-budget-20261123.md)
is maintained on `research/offline-sotto-fsk-dv-20261123`.
It found ~10,490B of resident codec code/tables after the 30,720B
codebook is moved to SPI; 3,148B encoder state; 704B
emulator-estimated stack. The K6 module arena is **15,408B**.
For an 8kHz W90 capture with **four 10ms/160B PCM buffers** and the
present 190B guarded packet queue, **even before adding packet-builder
code** the optimistic projection is
`10490+3148+704+640+190=15172`, leaving only **236B**
for module integration, ISR, SPI scratch, runtime overhead and
alignment. Adding the stand-alone queue harness's measured 768B
`.text` *as a rough proxy*, the simple model exceeds the arena
by **532B**. This is **not an integrated binary measurement**.
Moving at least 1–2KB more static tables out of RAM, or materially
reducing codec state/stack is needed before combining live sampling,
Sotto encode, queue, and radio in one module.

The upstream offline Cortex emulator also showed the old 200-turn
PA11 software converter takes essentially the entire MCU budget and
the 90-turn candidate needs hardware sampling/jitter quality checks.
No realistic MCU timing was measured by the new packetizer tests.
Actual ISR ownership has to be decided in firmware, not by a
module unilaterally remapping M0 exception vectors.

## Reproduce offline without the radio

```sh
python3 -m pytest -q \
 tests/test_k6_dv_guard33_packet.py \
 tests/test_k6_dv_guard33_stream.py \
 tests/test_k6_dv_guard33_selftest.py

python3 - <<'PY'
from modem import k6mod
code,entry=k6mod.build('c/k6/mod/dv_guard33_selftest.c')
print('K6 real API3 code bytes',len(code),'entry',entry)
PY
```

No serial, SDR, SPI or PTT needed for that test set.

## Prospective exclusive *NO RF* on-device self-test (FSK-15)

**HARDWARE STATUS: NOT YET RUN on writing this prospective protocol.**
Verify shared issue #11 for prior operator RELEASE and fresh exclusive
ACK before claiming the radio/SDR bench, and only if the radio still has
the original trusted slot30 and watched VFO A440.850MHz/B144.390MHz.
The trial is **one** hash-frozen signed module invocation returning 33
and report `dv guard33 packet selftest PASS`, with no TX and signed
module restored/readback verified; the SDR capture should show
only its baseline. No automatic retry. Do not treat the generic
harness's completion flag as evidence of RF.

```sh
python3 tools/k6_dv_guard33_selftest_bench.py \
  --prefix /home/tyler/flux-bench/tx-injection-20261122/dv-guard33-selftest/fsk15-run01 \
  --operator-lease-confirmed --dummy-load-confirmed
```

**Next separate goals after this:** MCU-resident slim Sotto
codebook placement + memory headroom; firmware-controlled sample timer
and PA11 W90/80 analog timing characterization; actual live
`slim_encode10` 10ms buffered scheduling, then guarded native-FSK
packet TX only after on-radio 8kHz capture and encoder coexistence
are proven. Not to be mistaken for gapless FIFO refill or two-way DV.
