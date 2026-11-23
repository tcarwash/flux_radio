# Flux FSK-09S-C — precommitted in-frame one-word FIFO append test

Date: 2026-11-23. Status: **planned, not yet a hardware finding**.
Branch: `research/fsk09s-single-word-append-20261123` in isolated `/storage5/dev/flux-fsk-oneword-lab` worktree. Shared issue [#9](https://github.com/tcarwash/flux_radio/issues/9).

## Hypothesis

The FD6818B native-FSK TX FIFO may accept **one extra 16-bit word after transmission starts**. Previous FSK-09 experiments sent 48 words before the projected 128-word frame ran out, but the older-FD6818 almost-empty interrupt assumption failed (`REG_02` did not indicate refill). The other agent's [FSK-09S status-only physical tests](https://github.com/tcarwash/flux_radio/blob/research/native-fsk-pdm/docs/k6-fsk09s-status-probe-physical-20261123.md) did not identify a reliable B-chip occupancy signal.

**Key safe discriminator:** We have already physically transmitted frames with **64 words preloaded**, so 49 *total* FIFO writes in one frame are within previously demonstrated FIFO capacity **even if no word drains before the append**. This test therefore needs neither an unknown IRQ nor an occupancy estimate. It attempts only **one** post-start `REG_5F` write, never reads REG5F (destructive), never writes speculative REG5E threshold or REG3F almost-empty source bits, and never requests a 128-word single-frame stream.

## Two-frame frozen comparison

| Frame | Programmed length | Initial FIFO writes | Active FIFO writes | Final word | Role |
| --- | ---: | ---: | ---: | --- | --- |
| Frame 0 | 49 words / 98 bytes | 49 | 0 | `0x3ca5` | Fully preloaded control |
| Frame 1 | 49 words / 98 bytes | 48 | **1 at ≥70ms after TX start** | `0xa53c` | Single in-flight append |

Both frames are finite, explicitly modem-rearmed and carried in a **single bounded TX lease**. First 48 words of frame 0 are deterministic, balanced test data from `fsk_lab_vector.h`. Frame 1 inverts each of those 48 words, so **the known first 256 training bits differ**. The extra last words have distinct patterns and are never used by the decoder for synchronization. The declared hardware frame length is `(49 × 2 - 1) << 8 = 0x6100` in `REG_5D`. A hardware-completion observation (`REG_0C & 1`) alone is *not* proof of the appended word; independent SDR heldout-bit decoding is required.

The 70ms delay starts at the `REG_59=0x08f8` frame-start write and executes **only after confirming TX has not ended**. At any instant, the number of FIFO words written since clear is at most 49, below the observed working 64-word capacity. Whether a write during active TX is supported by the RFIC is the unknown under test. The 1900ms firmware lease, 700ms per-frame completion timeout and power/key interruption checks still apply.

## Safety and coordination

- Only on the user-confirmed conducted dummy-load FD6818B K6, 433.500MHz, air-coupled RTL-SDR 433.750MHz center; no intended radiated signal; no RF power/spectrum compliance claims.
- The preexisting `tools/k6_fsk_boundary_bench.py` owns **canonical, process-scoped exclusive Flux serial flock** from read-only preflight through signed library backup, temporary module install, one guarded TX and SDR recording, original signed slot restoration/readback, VFO restore and TX idle. Busy lock means **abort**, not forced acquisition. The other research agent's uncommitted worktree is never modified.
- Expected radio firmware `95d6b260d4122287`, 0x4829, both VFOs initially 144.390MHz, known original slot30 PDM module. Verify all before touching radio. No main firmware flash, calibration changes or unbounded TX.
- Source compiled *before* test; frozen SHA-256 gates all execution. Inert form defaults to no TX. No automatic retry. Unexpected early frame finish, FIFO status, failed restore or failed safe check aborts and retains raw IQ/transaction receipt.

## Predeclared success and failure criteria

1. Module returns 98 words (49+49) with no errors and full signed original module + both VFO restoration.
2. Independent SDR receiver uses **only first 256 known payload bits** of each frame for timing/bit-level decisions; **last 528 bits** are held out, including the final **16 appended data bits** in frame 1.
3. A successful **append** requires the control frame and test frame to independently decode zero bit errors in their heldout tails, with observed last words `0x3ca5` and `0xa53c` respectively. A register write returning success without matching RF tail is **not a pass**.
4. A missing word, an early frame completion, or mismatched tail is a negative/inconclusive result to preserve. No secondary write or immediate retry to coerce success. A control-only failure invalidates the interpretation.
5. Even a positive result proves **one in-flight FIFO write under a finite 49-word frame**, **not** sustained refill, repeated high-rate streaming, FIFO headroom interrupts or any arbitrary RF waveform.

## Pinned source and reproduction

- Inert/armed C sources: `c/k6/mod/fsk_oneword_append.c`, `c/k6/mod/fsk_oneword_append_armed.c`.
- Expected armed source SHA256: `034700f2041ebbcfcfc1c1626320a1cce1240f26b3c93f2c1fde62b14a1dd9eb`.
- Expected compiled level-3 module SHA256: `1a57645904997c560eabad4917731012a0a0089d7df073e042ffb38b01518197`, 1294B, entry0.
- Bench preflight: `tools/k6_fsk_oneword_bench.py`; independent known-prefix heldout RF analyzer: `tools/k6_fsk_oneword_analyze.py`.

```bash
python3 -m pytest -q tests/test_k6_fsk_oneword_append.py
python3 tools/k6_fsk_oneword_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-oneword-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_oneword_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-oneword-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-oneword-analysis-run01.json
```

A prospective commit and tests must precede physical TX. This protocol does **not** authorize a second test if the first is negative.
