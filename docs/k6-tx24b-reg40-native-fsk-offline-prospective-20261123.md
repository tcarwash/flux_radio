# TX-24B — native FSK / REG40 deviation coexistence, frozen offline qualification

**2026-11-23, ~15:21 PT. Status: OFFLINE VERIFIED; NO TX24B HARDWARE RF YET.** Independent new queue [issue #26](https://github.com/tcarwash/flux_radio/issues/26), coordinated by [issue #11](https://github.com/tcarwash/flux_radio/issues/11). P0 [FIFO IRQ qualification #9](https://github.com/tcarwash/flux_radio/issues/9) has a separately requested hardware lease and takes priority. Antigravity [TX-26 #19](https://github.com/tcarwash/flux_radio/issues/19) has a different offline 4FSK project. Worktree `/storage5/dev/flux-tx24b-fsk-reg40-offline`, branch `research/tx24b-fsk-reg40-offline-20261123`.

## Source-backed question, prior physical evidence, and deliberate scope separation

On this B-chip, TX-24 physically measured repeatable **REG40 low-byte gain scaling of a REG71-generated 1091-Hz FM tone**, with four measured normalized levels 0.92123, 1.00023, 1.15125, 1.38039. See [original precommitted first physical report](k6-tx24-reg40-physical-positive-20261123.md). But the native FSK modem has a separate FSK-gain register `REG70=0x0080|21`, `REG72=0xC194` for **4800 baud** (not the DTMF tone2 function), `REG3F=0x8000` and a FIFO at `REG5F`. No prior physical experiment varied REG40 while this native modem was active. The B silicon might ignore REG40 in this work mode, latch it only at TX start, or scale both signs. The old tone result is not proof of anything about the native FSK path.

**This experiment asks only whether REG40 lowbyte changes a fixed native **binary** FSK ±deviation at the inter-frame control rate**. It does not implement or claim true four-state 4800-baud modulation, QPSK/QAM, RF power modulation, independent IQ, phase continuity, sustained transparent FIFO refill, new DV throughput, or TX26's per-symbol REG70 gain mechanism. If a physical run eventually qualifies, the result will only establish **slow frame-to-frame** FSK deviation scaling.

## Source and registered physical candidate (NOT run)

The module `c/k6/mod/tx24b_fsk_reg40_probe.c` is **inert by default**. The separate `tx24b_fsk_reg40_armed.c` has explicit source enable, PA enable, independently acknowledged fixture and watchdog flags. No radio actions are invoked by tests. The source only accepts FD6818B chip ID `0x4829`, 433.500 MHz dummy-load frequency, clear-channel RSSI, default `REG40=0x3516`, and no conflicting pre-existing native-FSK work mode. It uses an independent **1900-ms** firmware TX lease and one PA-on episode. The source backs up **REG3F/59/58/70/72/5A/5B/5C/5D/40**, checks the REG40 write/readback after switching workmode, and restores all ten original registers after PA off and before `tx_off`. No mid-frame `REG40` change, no destructive FIFO reads, no in-flight FIFO refill.

Exactly **8 finite frames × 33 FIFO words** under one bounded module request. Each frame has normal hardware preamble `16×0x55` and sync `076b3e36`, followed by **32 identical known data words** and a disposable final `0xA55A` guard word. The data pattern is **four words 0x0000 / four words 0xFFFF** alternated four times, yielding **eight 64-bit long FSK sign plateaus per 512-bit packet**. Long plateaus provide a steady ±frequency discriminator measure without timing pressure. One possible B-chip leading extra `f8 00` 16-bit word displaces only the sacrificial guard; the original 512 data bits survive. FSK control states are `REG59=00F8`, `80F8` FIFO clear, `00F8`, and `08F8` enable; `REG70=0x0095` and `REG72=0xC194` are configured **once** for the whole TX. Between completed frames and *only* when native FSK is stopped, REG40 is changed then read back before the next 33-word preload.

| Frame | REG40 | Role |
|---:|---|---|
| 0 | 0x3516 | pilot baseline |
| 1 | 0x3540 | heldout medium |
| 2 | 0x3516 | pilot baseline |
| 3 | 0x3580 | heldout high |
| 4 | 0x3516 | heldout reference drift control |
| 5 | 0x3500 | heldout low |
| 6 | 0x3540 | heldout medium replay |
| 7 | 0x3580 | heldout high replay |

Source `c/k6/mod/tx24b_fsk_reg40_probe.c` SHA256 **e51954e321335c93597104a79c9e6d7b9d3edce113c3f472ad23b2013dc8071a**. Armed wrapper `c/k6/mod/tx24b_fsk_reg40_armed.c` SHA256 **9f695c38ce388011f8dd0bc3d6cfbc7f9dee47b3993b7a341c3852e998a68944**. Cortex-M0 ARM API3 module **1080 bytes**, SHA256 **d5ea9320db0a57bfec20c04dd7b29d37caf25d5c55e5cebe1e17003e79b98f18**. Rebuild and verify with `frozen_build()` before any physical run. These are source/code hashes, not physical results or RF authorization.

## Frozen public-sync receiver and pass/fail criteria

`tools/k6_tx24b_fsk_reg40_analyze.py` is **READ ONLY**. It accepts archived complex RTL-SDR U8 IQ (center 433.750MHz at 1.024MS/s, downmixed at the established Flux 280500Hz compensation and to 128kHz processed IQ), applies 8-kHz complex-baseband lowpass before frequency discrimination, and derives the independent 4800-baud timebase, sign polarity, receiver frequency offset and symbol-center phase from only the public **160-bit preamble + sync**. It does *not* use the known payload, register level, packet FSK sign, expected transmit timing, or old RF capture to lock symbol timing.

For each of the eight independently acquired frames, the immutable guard selector accepts exactly one of two *structurally predeclared* states: (A) first source word is all 0 and last word `5a a5`; (B) first word is `f8 00` and final word is all ones, signifying a leading extra 16-bit word displaced the guard. These conditions use only the start/end control words; the known heldout payload is not searched to determine timing or RF frequency. An alternative wrong offset, unexpected prefix, missing frame or ambiguous guard is **FAIL/INCONCLUSIVE**.

For all eight 64-bit runs, score only bits **16..47** within each run, never the transition/edge or the shifted prefix. This yields **256 known steady sign samples per frame**, **2048 total**. The receiver measures `median(f+ | expected mark bits)-median(f− | expected space bits)` without assuming the deviation transfer law. Only frames **0/2** baseline REG40=0x3516 train the source's reference separation. Frame 4 is **heldout**; frames 1/3/5/6/7 assess four REG40 low-byte choices.

**Prospective positive gate (all mandatory; negative and raw evidence always retained):**

1. Exactly eight independent public-sync frames acquired at fixed 4800 baud, each absolute correlation ≥**0.92** and zero sync marker errors; exactly one documented guard/prefix offset of 0 or 16 bits in each.
2. **0/2048** known heldout central FSK sign-bit errors (after marker-only trained sign and threshold).
3. Two reference pilots (0/2) agree in absolute mark/space separation to ≤**12%**; heldout baseline (frame4) and all three 0x16 controls total spread ≤**12%**.
4. Replayed medium and high levels each agree within **12%** of their respective paired measurements.
5. Recovered separation strictly ordered `0x3500 < 0x3516 < 0x3540 < 0x3580`, with low ≤**−3%**, medium ≥**+8%**, high ≥**+20%** relative to baseline.
6. One and only one firmware module run returns **264 (=8×33 words)**, within a 1900-ms independent TX watchdog; no in-flight FIFO writes beyond fixed preload, no unknown register mutation; full signed original module and both watched VFOs verified after TX off; one separate operator-recognized lease released.

The older-family arithmetic scaling law is a **hypothesis to compare after** fixed pass/fail, not a fitter or guarantee. A no-response / flat REG40 control, or a positive tone-only response with negative native FSK, is valuable evidence. A failure in public-sync decoding does not prove REG40 negative; label **inconclusive framing** separately. **Passing simulation is not a physical B-chip result.**

### Offline test evidence (prior to any TX-24B RF)

**Independent archived real-RF receiver preflight:** TX24B's unchanged public-sync acquisition was run READ ONLY against the previous FSK-13 eight-frame 4800-baud real FD6818B SDR file `~/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-run01.npy`, frozen SHA256 **71113f0fdebf8927a1c78bc340b74de1c95e316e3cb78bcbb8dbf296681349e8**. It independently found **8 of 8 frames**, with **0 sync errors**, public-marker correlations **0.9935–0.9961**, and observed FSK frequency separation **1,988–2,094 Hz**. The archived FSK-13 transmission used a different source payload and constant REG40, so its contents CANNOT be scored as TX-24B REG40-modulated RF or count as a new emission. This validates only the public marker receiver against real RF; the TX24B gain effect remains unknown.

`tests/test_k6_tx24b_fsk_reg40_probe.py` builds the inert and PA-off/PA-on source with compile-time ACK guards, emulates exact 8×33 known words, *no in-flight REG40 adjustment*, fixed REG70/72 values, original snapshot/restore, timeout/key-abort and chip/channel refusal under Cortex-M0 Unicorn. `tests/test_k6_tx24b_fsk_reg40_analyze.py` creates independent 4800-baud synthetic frequency data with public marker and alternated normal/F800-prefix frames, qualifies four monotone deviation scales, rejects flat/reversed/drifted/unstable controls and bad guard, and proves that expected payload is absent from the frame-acquisition implementation. `tests/test_k6_tx24b_fsk_reg40_rf_bench.py` requires both operator-lease and dummy-load flags before any device access, pins source/compiler hashes, insists on original watched dual VFOs and signed module, never overwrites evidence, uses the canonical nonblocking Flux `serial_lock` across all operation and restoration. No tests use radio/SDR hardware.

## Future physical gate — NOT CURRENTLY LEASED

Higher-ranked P0 [#9 FSK-09S-H](https://github.com/tcarwash/flux_radio/issues/9) has already posted an exclusive bench **REQUEST**. Do not preempt it, assume a free USB process means safe, or claim a reservation simply from this issue. TX-26 [#19](https://github.com/tcarwash/flux_radio/issues/19) is separately owned and must not be overwritten. The queue dashboard has **no durable Claim feature**. Future TX-24B physical trial needs an explicitly recorded, independent **OPERATOR APPROVED TX24B LEASE START** after all other resource owners ACK idle and previous leases RELEASED, together with user-confirmed rated dummy-load fixture. GitHub comments are coordination, not the physical lock.

The one-shot tool `tools/k6_tx24b_fsk_reg40_rf_bench.py` additionally requires:

- Exact port `/dev/ttyUSB0` and canonical nonblocking Flux `serial_lock('/dev/ttyUSB0')` for **entire** serial, independent SDR, signed-slot SPI backup/install/restore, one TX and radio restoration. The IC-7100 `k6_bench.py` lock is irrelevant.
- Expected Flux build `95d6b260d4122287`, B-chip `0x4829`, API3 cable-only module, read-only original VFO A **440.850MHz watched**, B **144.390MHz**, both wide FM/no memory/TX idle. Abort any altered operator state or unknown signed slot30.
- Known signed original code SHA256 **6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522**, original slot30 envelope backed up to new file BEFORE mutation, monotonic signed module serial, only temporary compiled 1080-byte hash above. No permanent firmware/FRAM calibration writes.
- Single conducted 433.500MHz dummy-load TX, RSSI clear and RTL-SDR no overload, 5s raw complex IQ; fixed watchdog 1900ms; one module RUN request, NO retry if completion unclear.
- Save full transaction JSON, original signed module, raw IQ SHA256 and independent frozen receive report (new filename, no overwrite). Confirm TX off; only restore a positively identified temporary module (never overwrite an unknown third-party write); check original signed image readback, both VFOs exact, receiver idle, and explicit lease release.
- If any restoration step fails, halt further radio work and mark reconciliation REQUIRED.

**Documented invocation for a FUTURE specifically approved lease, not an instruction to run now:**

```sh
cd /storage5/dev/flux-tx24b-fsk-reg40-offline
BASE=~/flux-bench/tx-injection-20261122/tx24b-reg40-native-fsk
python3 tools/k6_tx24b_fsk_reg40_rf_bench.py \
  --prefix "$BASE/tx24b-fsk-run01" \
  --dummy-load-confirmed --operator-lease-confirmed
python3 tools/k6_tx24b_fsk_reg40_analyze.py \
  --iq "$BASE/tx24b-fsk-run01.npy" \
  --out "$BASE/tx24b-fsk-run01-locked-v1.json"
```

**Scientific state:** REG40's independent **tone-FM** modulation remains proven in TX24, but REG40's response **while native FSK work mode is enabled is not yet known**. TX24B's separate offline source/protocol are prepared for one finite-frame physical trial, without advancing TX26's untested per-symbol 4FSK claim.
