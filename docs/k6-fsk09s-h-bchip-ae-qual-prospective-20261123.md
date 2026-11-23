# Flux FSK-09S-H — FD6818B almost-empty IRQ bitfield qualification

**Date** 2026-11-23. **State:** OFFLINE SOURCE + ANALYZER PREPARED, NO PHYSICAL BENCH LEASE CLAIMED, NO TX. Isolated worktree `/storage5/dev/flux-fsk-bchip-irq-qual-20261123` branch `research/fsk09s-irq-bchip-20261123`. [P0 FIFO issue #9](https://github.com/tcarwash/flux_radio/issues/9), [shared bench queue #11](https://github.com/tcarwash/flux_radio/issues/11). The Antigravity agent **already owns TX-26 4800-baud 4-FSK offline development issue #19**; no code/branch collisions or duplicate 4-FSK probe. A separate transmit lease must be negotiated after the other agent's current/queued work.

## Why this is worth one small physical test

The first FD6818B 128-word FSK refill attempt (`research/native-fsk-pdm:c/k6/mod/fsk_refill.c`) made three older-family assumptions now contradicted by source and physical results on **original FD6818 (0x4819)** in `upstream/main` (`baitisj/blowfang`, commit `6bb97ac`, `c/k6/mod/c4fifo.c`, `c4lock.c`, `docs/k6-c4fm.md`):

1. **Threshold:** old test writes `(24u << 6)` to `REG_5E[11:6]` for 24 words. Old-chip physically timed FIFO AE uses **`REG_5E[9:3]`**. With the corrected field mask `~0x03F8`, the 24-word threshold becomes `24 << 3 = 0x00C0`; preserving an illustrative original register `0x3044` yields **`0x30C4`**. The old write `0x0600` inadvertently encodes **64 words in `[9:3]`**, higher than its initial 48-word preload, and sets bit10.
2. **Interrupt sampling:** the old test directly reads `REG_02` each loop, whereas `c4fifo.c` and the older driver **write `0` to `REG_02` before each read**, which latches pending interrupt status for the subsequent read. Older documentation also describes an event-code `14` in low nibble instead of a bit14 event; our code records **both** formats without assuming which applies to the B chip.
3. **Enable:** `REG_3F` must include `0x4000` for FIFO AE in addition to `0x8000` TX finished; original `fsk_refill.c` already uses `0xC000`, but `fsk_status_probe.c` uses only `0x8000`. **All three aspects need to be evaluated together on the B chip.**

Earlier observed `REG_02=0x0800` is **not** evidence of an actual almost-empty event under either interpretation. A negative physical telemetry result is valid, and it must not be bypassed by a timer-only refill. No known `REG_5F` FIFO occupancy read is safe.

## Exact physical protocol — prospective and NON-refilling

**Module:** `c/k6/mod/fsk_bchip_ae_qual.c`, default `FSK_AE_QUAL_ENABLE=0` (inert result `-9`). Separate committed `fsk_bchip_ae_qual_armed.c` has both fixture and watchdog compile acknowledgments. Host bench refuses to access radio without three explicit runtime switches (`--dummy-load-confirmed --operator-lease-confirmed --queue-lease-acknowledged`) and checks frozen source and compiled binary SHA before launching the proven reversible transaction harness.

- **Radio** Quansheng K6/FD6818B chip ID **0x4829**, expected firmware build `95d6b260d4122287`. Dummy load confirmed by operator, RTL-SDR air-coupled to dummy-loaded RF (TX **433.500 MHz** / SDR **433.750 MHz**, fs=1.024 MS/s). Bench must be exclusively leased on #11, and canonical nonblocking `serial_lock('/dev/ttyUSB0')` held across signed slot30 backup/install, IQ acquisition, one TX and restoration. NO forced locks, automatic retries or unreviewed firmware flashes.
- **Original hardware baseline** operator watched VFO A **440.850MHz**, B **144.390MHz**, both wide FM/nonmemory, standby RX/TX off. Bench rejects any new frequency/configuration change rather than overwriting third-party state. Original trusted signed slot30 PDM module is backed up and restored with a fresh signed replay serial and readback/ABI/source-code fingerprint verification. Module snapshot/restores original REG3F/59/58/70/72/5A/5B/5C/5D/**5E**, IRQ cleared before/after TX. External firmware TX lease at **1900ms**, per frame deadline at **450ms**, channel idle RSSI and physical key/power abort preserved.
- **Payload:** exactly **two** 49-word/98-byte standalone 4800-bps hardware FSK frames. Each one **fully preloads** the *same* first 48 deterministic `fsk_lab_vector.h` data words plus a 49th sacrificial guard `0xA55A`. No more than 49 FIFO writes/one frame and **ZERO** mid-TX `REG_5F` appends. `REG_5D=0x6100` (98 bytes, 49 words), preamble16×0x55 +4B sync `076b3e36`, gain21, signed NRZ FSK. One TX session returns **98** on success. This short fixture accommodates the known random extra first `0x00F8` word: without prefix, full 48 data+guard arrive; with prefix, `f8 00`+48 data arrive and guard is displaced. This is an *independent prospective* allowed pair, not post-hoc salvage.
- **IRQ experiment:** Frame 0 sets readback-verified `REG_5E[9:3] = 24`, frame 1 sets it to **8**, preserving other bits from the saved original. `REG_3F=0xC000` enables TX-finished plus almost-empty. **Every ~1ms poll while TX active**: write 0 to `REG_02`, then read that register and `REG_0C` TX status; log first time raw bit14 appears, first time low-nibble event14 appears, cumulative/status flags, poll count and frame-done time. No predicate is used as a refill grant. All compact raw telemetry emitted via serial **only after PA/TX off and module registers restored**.
- **Nominal physical IRQ timing if the threshold field operates as in upstream:** a fully loaded 49-word FIFO at 4800bps consumes 300 words/sec. The 24-word threshold arrives after ~25 consumed words (**83.3ms**), the 8-word threshold after ~41 words (**136.7ms**), so the two AE onset times should differ by **53.3ms**. A possible prefixed first control word does not increase the 49-word declared frame length and could affect the measured phase at the word level. Our deliberately broad, predeclared first-event gates are frame0 **55–125ms**, frame1 **110–190ms**, threshold-change difference **40–67ms**, with events occurring before measured TX done. A bit14 event or event-code14 may provisionally qualify; otherwise record negative (including all `0x0800`, early t=0, stuck or absent flags). **One positive trial does NOT yet prove headroom safe for full-sized 128-word refill**; require independent repeat/occupancy confirmation and a separately frozen refill protocol.

## Independent offline RF scoring — no test-vector-assisted acquisition

`tools/k6_fsk_bchip_ae_analyze.py` uses only hardware public preamble+sync and **fixed programmed 4800bps** to acquire IQ frames. Expected first 48 words and the explicit optional `0x00F8` prefix are compared **only after RF symbol decisions**. Strict RF payload qualification needs exactly 2 independently acquired frames with each having all **48 intended data words bit-perfect**, either in the normal `[48 data][guard]` layout or the `[0x00F8][48 data]` displaced-guard layout; no other word/bit shift qualifies. Exclude the guard from data scoring, and do not count the prefix as delivered data. If both independent frames pass this exact RF criterion and the real raw reported `AE` events follow one of the allowed timing rules, the outcome is **RF_PAYLOAD_PASS + AE_CANDIDATE**, not >64-word streaming proof. A partial/negative outcome is documented, with no automatic retest.

This deliberately tests two source-level register fixes **before** modifying the actual refill module. Do **not** deploy a pretested 128-word refill just because its Python simulator returns COMPLETE. The older `REG_02[3:0]=0xE` hypothesis and upstream `REG_02[14]` mask may both be wrong on B.

## Pinned source, code and bench requirements

| Item | Frozen property |
| --- | --- |
| Radio module source | `c/k6/mod/fsk_bchip_ae_qual_armed.c` SHA256 `7ebd2a4ac62314abee6781c8ca194a39607c667d1b1e0582a60fc6e5a65812cf` |
| Compiled API3 Cortex-M0 module | **1666 bytes**, SHA256 `a14bf5a7538c6510de54b85a0cc69580ae86fa06453e53efd7ca802c9f046879` |
| Host benchmark | `tools/k6_fsk_bchip_ae_bench.py`, refuses without 3 operator/lease ACK flags |
| Lab source | `c/k6/mod/fsk_bchip_ae_qual.c`, default disabled, 49+49 words |
| Receiver and IRQ scorer | `tools/k6_fsk_bchip_ae_analyze.py`, fixed-rate public-sync-only, raw telemetry separate from RF |
| Restoration | `tools/k6_fsk_boundary_bench.py` with latest watched two-VFO baseline and signed module fingerprint+readback |
| Source privacy | test vectors are public; no personal voice, mic audio or private compiled artifacts included |

**This protocol is frozen before ANY physical B-chip test.** Sources or acceptance rules changed after physical evidence require a new prospective commit and new queue entry. The results must preserve original raw IQ, complete signed slot restoration/readback evidence, VFO state, raw AE telemetry and strict offline scoring. If initial state, radio/SDR occupancy, source hash, port lock or new queue owner deviates: **ABORT without TX**.

### Commands — offline only unless a NEW explicit exclusive queue lease is ACKed

```bash
python3 -m pytest -q tests/test_k6_fsk_bchip_ae_qual.py
python3 -c 'from tools.k6_fsk_bchip_ae_bench import preflight; print(preflight())'

# DO NOT RUN until #11 exclusive hardware ACK + dummy load / operator approval:
python3 tools/k6_fsk_bchip_ae_bench.py \
  --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-h-ae/fsk09s-h-ae-run01 \
  --dummy-load-confirmed --operator-lease-confirmed --queue-lease-acknowledged

# Analyze archived IQ after guarded TX, no hardware involved:
python3 tools/k6_fsk_bchip_ae_analyze.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-h-ae/fsk09s-h-ae-run01.npy \
  --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-h-ae/fsk09s-h-ae-scored.json
```

**Current result is strictly OFFLINE PASS for source compile, preflight and synthetic acquisition tests; B-chip AE has NOT been observed and NO hardware lease is claimed.**
