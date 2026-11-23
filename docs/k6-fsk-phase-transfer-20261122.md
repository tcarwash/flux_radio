# Flux FSK-25 — leave-one-recording-out native-FSK phase transfer

Date: 2026-11-22 local / 2026-11-23 UTC.
Worktree: `/storage5/dev/flux-native-fsk-pdm`.
Status: **offline historical IQ analysis; no new RF transmission**.

## Research question

The earlier [FSK-24 full-IQ holdout]
(k6-fsk-physical-iq-holdout-20261122.md) found ~107–171%
held-out complex EVM from four native-PDM FSK frames against
an *independently specified instantaneous binary-FSK waveform*.
But another [FSK-23 phase-response FIR experiment]
(k6-fsk-pdm-repeats-and-phase-20261122.md) showed that short
filter-memory coefficients trained on one recording's pilot
predicted that same recording's held-out chip-boundary phase
far better than a memoryless bit model.

Can we transfer a **finite-memory phase impulse response** from
*other RF captures*, without fitting the target capture's pilot
taps or cheating by correcting its heldout phase?

## Training contract

`tools/k6_fsk_phase_transfer.py` loads the same four archived real
RF captures plus the fixed, independently checksum-verified PDM
manifest. It reuses validated native-chip pilot acquisition timing
for each capture. It filters received complex IQ identically
to the previous phase-FIR tool, samples unwrapped carrier phase
at the 1025 nominal native chip boundaries and forms 1024
phase increments.

For each test capture, it:

1. Excludes that entire recording when learning the tap weights.
   Uses only chips **3..575 of the other three captures**.
2. Removes each donor recording's own training mean before fitting
   tap coefficients, to avoid pooling different carrier offsets.
3. On the **target recording**, uses only its chips **3..575**
   to estimate one static per-chip phase offset/intercept while
   keeping the transferred taps frozen.
4. Predicts target phase increments on chips **576..1020**
   from the already-known commanded input bits and the frozen
   model. Computes RMSE and accumulated phase coherence *without
   heldout alignment, CFO fitting, phase resets or tap updates*.
5. Scores memoryless single-bit, adjacent 3-tap and adjacent
   7-tap architectures. An apparent future-neighbor tap in
   offline DSP is **not evidence of noncausal RF electronics**.

All four transmissions use **the same known 1024-bit vector**.
This is transfer across receive captures, **not transfer to
new message contents**, nor a standalone transmitter model.

## Actual archived physical RF results

The metric here is absolute accumulated phase coherence
at **chip boundaries**, not all-sample complex IQ EVM.

| Target capture | Memoryless | Three-tap transferred | Seven-tap transferred |
|---|---:|---:|---:|
| Original | 0.0809 | 0.7019 | **0.7697** |
| Repeat 01 | 0.0921 | 0.6356 | **0.6994** |
| Repeat 02 | 0.0652 | 0.7824 | **0.8287** |
| Repeat 03 | 0.0680 | 0.2791 | **0.2369** |

Crucially, the transferred 7-tap model remains useful on the
first three captures, so a significant component of the response
is reproducible beyond a single recording. Repeat 03 is
still an outlier under this stricter design.

Seven-tap donor coefficient estimates are tightly grouped:
the center coefficient is ~1.209–1.211 rad/chip, and adjacent
coefficients are around 0.10–0.21 rad/chip. This is consistent
with the prior ~1.55-radian total phase deviation per 4800-baud
chip, but is **not a calibrated device-only frequency law**.

### More specific evidence about the fourth recording

| Run | 7-tap target-pilot increment RMSE | Heldout increment RMSE | Heldout mean residual |
|---|---:|---:|---:|
| Original | 0.114 rad | 0.103 rad | +1.73 Hz |
| Repeat 01 | 0.112 rad | 0.095 rad | +2.81 Hz |
| Repeat 02 | 0.113 rad | 0.104 rad | +1.35 Hz |
| Repeat 03 | 0.116 rad | **0.091 rad** | **−8.13 Hz** |

Repeat 03's per-chip held-out prediction noise is *not* worse,
but its **small systematic signed phase-increment bias** accumulates
into a much larger phase error across 445 held-out chips.
This supports investigating pilot-to-heldout carrier-frequency
drift or timebase behavior before declaring a different
modulation impulse response.

A further scratch study fitted a linear per-chip frequency trend
using **only target pilot samples** and extrapolated it into
the holdout. It **reduced** coherence across all four runs:
7-tap original 0.770→0.288, repeat01 0.699→0.257,
repeat02 0.829→0.273, repeat03 0.237→0.075.
Therefore **do not deploy that linear correction**. The pilot
trend can represent sequence-dependent filter effects, not a
valid prediction of sustained oscillator drift.

Neither the drift bias nor the transfer experiment establishes
which fraction is due to the transmitting chip, the receiver
clock, analog filters, burst timing or demodulation.

## Reproduce

```sh
cd /storage5/dev/flux-native-fsk-pdm

python3 tools/k6_fsk_phase_transfer.py \
  --manifest docs/k6-fsk-pdm300-vector.json \
  --chip-report \
    /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/native-chip-holdout-v1.json \
  --out /tmp/flux-phase-transfer-check.json

python3 -m pytest -q \
  tests/test_k6_fsk_phase_transfer.py \
  tests/test_k6_fsk_phase_fir.py
```

Saved real archival analysis:
`~/flux-bench/tx-injection-20261122/phase-repeat/phase-transfer-loco-v1.json`

The report preserves source .npy SHA-256 hashes, acquired pilot
times, source manifest digest, tap vectors and source-capture
separation. The tool never accesses a radio or active SDR.

## Practical implications / next discriminator

1. Collect a **different known bitstream** with equivalent training
   diversity before treating this finite-memory model as reusable
   arbitrary-phase predistortion. Same-vector repeatability alone
   is not sufficient.
2. Examine SDR oscillator and sample-clock drift with an independent
   frequency/time reference. Add physically grounded receiver
   calibration before attributing an 8-Hz holdout bias to the RFIC.
3. Score full-sample IQ separately, with an independently commanded
   target. Chip-boundary coherence may be moderately good while
   [whole-sample ideal-FSK EVM](k6-fsk-physical-iq-holdout-20261122.md)
   remains unacceptable.
4. The native-FSK PDM data-mode backend may be useful regardless of
   imperfect coherent reconstruction. Do not confuse valid symbol
   decoding with a general-purpose IQ transmitter.
5. Continue physically independent MICIN, APC, tone oscillator, TCXO
   and other [TX roadmap](k6-tx-next-experiments.md) studies.

All steps here read only saved historical RF captures. The prior
experimental combined-FSK/APC safety gate remains in force.
