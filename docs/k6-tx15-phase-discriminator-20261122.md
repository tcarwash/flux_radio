# Flux TX-15P — phase-discriminating programmable RFIC tone vector

Checkpoint: November 22, 2026, local time. Branch
`research/native-fsk-pdm`. 100% offline design and compiled
Cortex-M0 emulator. **No new RF TX, flashing, audio connection or
serial command in this checkpoint.**

## Finding: the previous TX8 waveform was phase-ambiguous

Our prior physical TX-15 (REG_70/71) test used 64 programmed
**audio** tones, 450, 750, ..., 2550 Hz, at 150 updates per second.
It decoded **47/48 held-out audio-tone IDs**, confirming that this
path works as an *FM audio tone source* through the RF chip.

But the vector is not suitable for distinguishing audio oscillator
phase continuity from a tone-phase reset:

```
f_k = 450 + 300*k Hz
T = 1/150 s
f_k*T = 3 + 2*k   (integer number of audio cycles!)
```

Every ideal slot finishes at its initial phase modulo 2π.
Continuing the phase accumulator and restarting it at 0 on every
REG_71 write are thus indistinguishable in the ideal model.
Quantized REG_71 frequency words are close enough that the
three ideal models all have >0.999 model coherence over both pilot
and held-out segments.

**This does not disprove** the older independent 0x4819 experiments
that reported REG_71 phase continuity
(`docs/k6-range.md`, `docs/k6-characteristics.md`), but they
do not settle this separate **0x4829** physical trial.

## New discriminating v2 waveform

`c/k6/mod/tx8_phase_probe.h` includes 64 symbol IDs and
eight B-compatible REG_71 tone words **without integer cycles
per slot**, with both changed-register and same-word rewrites.
All frequencies are in 450–2550 Hz:

| Tone index | Nominal audio Hz | REG_71 word |
|---|---:|---:|
| 0 | 502 | 5183 |
| 1 | 782 | 8074 |
| 2 | 1091 | 11264 |
| 3 | 1424 | 14702 |
| 4 | 1693 | 17479 |
| 5 | 1987 | 20515 |
| 6 | 2273 | 23467 |
| 7 | 2519 | 26007 |

The first **16 predeclared pilot slots** contain all eight
frequencies and same-word rewrites. The remaining **48 heldout
slots** deliberately include repeated runs of 2–3 identical
words, making three phase-state policies identifiable:

1. `continuous`: oscillator phase never restarts.
2. `reset_on_change`: phase restarts only on a different word.
3. `reset_on_every_write`: even rewriting an unchanged word
   restarts phase.

`tools/k6_tx15_phase_vector.py` generates ideal audio oscillator
phase traces (not physical RF IQ) from the actual C register words
and reports absolute analytic phasor coherence between hypotheses.
This measure permits an unknown **constant global phase offset**
to be fitted, making it a more conservative separation metric
than simply comparing phases at t=0.

### Ideal model separation, 150 word updates/s

| Competing models | Pilot analytic coherence | Heldout analytic coherence |
|---|---:|---:|
| Continuous vs reset only on change | 0.435 | 0.218 |
| Continuous vs reset on *every* write | 0.285 | 0.021 |
| Reset on change vs reset on every write | 0.701 | 0.332 |

Higher = more difficult to distinguish; 1.0 = identical up to
one global complex phase. These low values make the *prospective*
vector suitable for testing hypotheses that the original
integer-cycle tones could not distinguish.

**These numbers are entirely simulated, not observed over RF.**
No physical phase-continuity finding for this B-chip yet.
A real decoder must account for PA attack, TX FM filtering,
RF/SDR LO offsets, oscillator clock drift (the MCU and RFIC do
not share the same clock), gain-by-tone, latency and frequency
response. In particular, this comparison is on **audio
oscillator phase**, not direct RF carrier phase or arbitrary IQ.

### Firmware compatibility and guard

The only production-source change is an optional compile-time
header selector in `c/k6/mod/tx8probe.c`:

```c
#define TX8_BENCH_ENABLE 1
#define TX8_PHASE_VECTOR_V2 1
#define TX8_SYMBOL_RATE 150u
#include "/storage5/dev/flux-native-fsk-pdm/c/k6/mod/tx8probe.c"
```

This is a **source example, not a ready-to-run flashing command**.
By default, `TX8_BENCH_ENABLE` is zero, and no RF is enabled.
Selection of the v2 vector at any other baud causes a compile
error. An *RF-on* build additionally demands both independent
explicit RF fixture and watchdog acknowledgments, and is
restricted to the guarded 433.500-MHz conducted configuration.
The normal old TX8 vector is unchanged.

The v2 host emulator validates the 64 exact REG_71 words, 150 Hz
scheduler, no-late-events, chip ID guard, PA-off default, TX
unkeying and register/timer restoration. The original TX8
emulator coverage remains unchanged. Physical RF was not keyed.

## Reproduce without touching the radio

```sh
cd /storage5/dev/flux-native-fsk-pdm
python3 tools/k6_tx15_phase_vector.py \
  --out /tmp/tx15-phase-design-unique.json

# Includes compile/emulation if the optional Unicorn package is installed
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
  tests/test_k6_tx15_phase_vector.py \
  tests/test_k6_tx_tone_rf_bench.py \
  tests/test_k6_tx8probe.py \
  tests/test_k6_tx_tone_rf_analyze.py
```

For a real v2 capture: record the RF IQ with the same 1.024 MS/s
SDR settings and signed source manifest, reserve pilot slots
for acquiring timing and analog frequency response, then compare
**heldout** discriminator phase and symbol traces with the
three predeclared hypotheses. Do not choose the best vector
by inspecting heldout labels before freezing the model.

Before any new bench TX, independently verify dummy-load/
coupler/SDR attenuation, independent hardware watchdog,
RF startup/shutdown, expected firmware build ID, radio
idle status, and backup/restoration of the overwritten module
slot. The currently connected serial adapter was in use by
another process when this branch was inspected; we did not
interrupt that session or open a serial device.

## What this cannot achieve alone

Tone-oscillator register control synthesizes a **single audio
sinusoid of selectable frequency**, whose instantaneous
amplitude drives FM modulation of the RF carrier. Rapidly
switching oscillator frequency does not automatically yield
arbitrary *signed RF frequency offset*. Both carrier phase
and occupied spectrum depend on how the oscillator restarts,
the TX deviation and filtering, register timing and the
receiver response. It provides no independent RF envelope
control. The native-FSK, subaudio, direct-synthesizer, APC and
external-modulator candidates remain live.

See [all-path queue](k6-tx-next-experiments.md),
[TX architecture](k6-arbitrary-rf-waveform-engine.md), and
[TX-17 source audit](k6-tx17-direct-fm-register-audit-20261122.md).
