# Offline Sotto Modem Optimization — measured DCT candidate

2026-11-24. All results are from **offline CPU/SPI timing models**, not real
RF or measured FD6818B peripheral timings. No device was flashed or keyed.

## Reproducible comparison

The actual pinned `Sotto` slim code and its actual compiled Cortex-M0 module
were exercised through API29 Unicorn hooks, virtual native 4800-baud 2FSK IQ,
C packet decoder and an independent native C codec reference. The original
encoder was compared with the exact reciprocal-phase DCT change
(`0e4f885b`; original research source `a21765a7`). The DCT test checks all
380 non-DC entries against the existing table. Native C, RF-session and
packet validation are required; a completed emulation alone does not pass.

Use `tools/k6_sotto_opt_bench.py` with a full API29 Sotto development checkout
as **both** `--baseline` and `--candidate` (the main checkout by itself does
not contain the attended experimental `sotto_live_tx.c` module). The paths
below are illustrative; the comparison does not alter either checkout:

```sh
python3 tools/k6_sotto_opt_bench.py \
  --baseline /path/to/sotto-live-original \
  --candidate /path/to/sotto-live-with-dct \
  --include-mixed --strict --json /tmp/sotto-opt-matrix.json
```

The tool compiles both development trees independently, runs an identical
parameter matrix and records each module's SHA/size, timing assumptions,
returned codec, PCM loss, RF delivery, strict acceptance and CPU/SPI metrics.
The explicit `--case ADC_CYCLES:SPI_US` parameter can be repeated; `--wav`
accepts 8-kHz mono PCM16 clips of 0.1–3 seconds in 10-ms increments. The
built-in triangle and mixed-frequency stimuli are **not natural speech** and
are not evidence of perceptual audio quality.

## First optimization: DCT phase reduction

The original DCT computes a wrapped phase through repeated subtraction. The
replacement uses the exact `m -= ((m*205)>>14)*80` reduction across the
entire 0..741 DCT input domain, preserving the 20x20 non-DC DCT table
exactly while eliminating costly repetition on the Cortex-M0.

At modeled CPI 1.5, with 1.04 seconds of triangular PCM and 1.20 seconds of
silence/harmonic/multitone PCM, the measured matrix was:

| ADC cycles/sample | SPI per read | Triangle before → after | Mixed before → after |
|---:|---:|---|---|
| 0 | 34 µs | Pass → Pass | Pass → Pass |
| 4,800 | 10 µs | Pass → Pass | Pass → Pass |
| 4,800 | 34 µs | Fail → Pass | Fail → Pass |
| 5,745 | 10 µs | Fail → Pass | Fail → Pass |
| 5,745 | 34 µs | Fail → Fail | Fail → Fail |

**Aggregate: 4 baseline passes, 8 candidate passes, 4 recoveries, 0
regressions.** Candidate successful trials recovered every PCM sample and
all expected codec superframes exactly; all C and Python wire-session
oracles agreed. Live module code size was 10,192 → 10,200 B, consuming 8 B
of a tight 10,240-B bundle limit (including a 24-byte envelope: 16 B spare).

## Next optimization gates

- Attack the remaining high-ADC/high-SPI case without sacrificing codec
  fidelity. Independent review suggests avoiding zero-imaginary FFT swaps,
  reducing recurrent DCT phase math and, if permitted by the **actual**
  resource service, batching IVF centroid reads. Each is a new candidate,
  not a proven improvement.
- Require byte-exact codec output and complete IQ/FSK C/Python recovery for
  at least two fixtures; measure actual module size, stack and arena.
- Confirm the resource API's maximum read size before considering 256-byte
  centroid reads; a virtual API that accepts a read isn't enough evidence.
- The 4-byte-headroom winner-vector candidate (`3ddaf662`) remains separate
  and is **not** included in this change.
- Natural speech clips and bench-calibrated peripheral latencies are required
  before calling the modem real-time-qualified on physical hardware.
