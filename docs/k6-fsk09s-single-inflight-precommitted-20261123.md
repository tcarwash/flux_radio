# Flux FSK-09S-D — prospective single-frame one-word append

Date: 2026-11-23; **commit before any physical RF execution**. Branch `research/fsk09s-single-word-append-20261123`; shared queue issue #9. This is a distinct experiment addressing the observed **inter-frame rearm artifact** in FSK-09S-C (see [negative report](k6-fsk09s-oneword-first-negative-physical-20261123.md)), not an automatic retry. Permission to use the user-confirmed dummy-loaded K6 bench is current; canonical Flux serial-port lock still required.

## One change to the prior experiment

The previous two-frame trial showed a **perfect fully preloaded control**, but its second frame contained one unexpected leading `0x00F8` word followed by exactly the 48 preloaded words, meaning the scheduled last marker was absent from the 49-word physical RF frame. This may be a **frame-rearm artifact** rather than proof of a TX FIFO append limitation.

To separate those hypotheses, this test sends **one frame only**, avoiding any preceding frame's completion/rearm. The sole frame preloads **48 words** and explicitly programs **49 words** total (`REG_5D=0x6100`, 98 bytes), then starts native 4800bps FSK, waits **at least 70ms after TX start**, and writes exactly **one** extra word (`0xA53C`, LE expected `3ca5`). No additional FIFO writes or automatic retries. With only 49 total writes, this is below the **64-word FIFO preload already proven** on the same radio, even in the worst case where zero FIFO words were drained when the last write occurs. No unknown headroom/AE interrupt, no REG5E threshold writes, no FIFO read.

## Predeclared pass/fail rule

- Radio module must report successful completion **49**, original signed slot30 module and both original RX VFOs must be independently read back/restored, and TX off. These safety conditions are necessary but **not sufficient**.
- Independent SDR IQ receiver (`tools/k6_fsk_oneword_single_analyze.py`) acquires timing exclusively via the **public 16-byte 0x55 preamble + 4-byte `07 6B 3E 36` sync**, **not** the expected payload. It independently decodes the entire **98-byte** frame using only marker-derived FSK levels. After decoding, compare all 784 payload bits to the precommitted expected sequence (inverted seeded pattern) and require **0 errors** and exact last 16-bit FIFO word **0xA53C**. This is the only condition that would demonstrate one in-flight append; return status alone is insufficient.
- If fewer than 49 words appear, sync fails, any payload/tail bit is wrong, or the radio watchdog/restore fails, declare **negative/inconclusive**, preserve SDR/transaction JSON and stop. A single passing frame still **does not establish continuous/gapless FIFO streaming**, repeatable writes, unknown-occupancy refill, or an arbitrary waveform generator.

## Hardware safety/fixture

Conducted/dummy-load FD6818B chip ID 0x4829 on firmware build `95d6b260d4122287`, 433.500MHz TX via radio and air-coupled RTL-SDR at 433.750MHz, 1.024Msps. Exclusive process-scoped Flux `serial_lock('/dev/ttyUSB0')` acquired nonblocking for complete verified original module backup/temp slot30 install/TX/SDR IQ/restoration, no main MCU flash, no calibration. Original VFOs must be 144.390MHz idle at preflight and restoration. Hard firmware TX lease 1900ms, per-frame timeout 700ms, safe key/power checks. Source is inert by default, and armed image/source SHA protected. No external RF leakage is intended or claimed calibrated.

## Frozen files

- `c/k6/mod/fsk_oneword_single.c` inert and `c/k6/mod/fsk_oneword_single_armed.c` intentionally enabled for conducted fixture.
- Frozen armed source SHA256: `7c6a90b2bb91f6304df7851741955df8a417f22b089479c4dfd221c9c21a7fea`.
- Frozen compiled module SHA256: `63c7b7597136d4c3d5c4d3b14851445edd8c968cd7ae52a3578e76e3f726b6e1`, 1208B.
- Safe bench and read-only independent RF decode: `tools/k6_fsk_oneword_single_bench.py`, `tools/k6_fsk_oneword_single_analyze.py`.

## Exactly one guarded hardware run

```bash
python3 -m pytest -q tests/test_k6_fsk_oneword_single.py tests/test_k6_fsk_oneword_append.py
python3 tools/k6_fsk_oneword_single_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-single-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_oneword_single_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-single-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-single-analysis-run01.json
```

No subsequent automatic RF retries. This frozen test isolates only whether a single FIFO word can enter an otherwise not-yet-empty 49-word frame while active.
