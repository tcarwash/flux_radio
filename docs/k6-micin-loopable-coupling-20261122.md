# Flux TX-12B — loopable MICIN 4-FSK stimulus and DC-coupling feasibility

Date: 2026-11-22 local / 2026-11-23 UTC.
Worktree: `/storage5/dev/flux-native-fsk-pdm`, branch `research/native-fsk-pdm`.

## Status

**OFFLINE audio-reference generation + modeled analog paths only.**

No physical adapter was connected, no audio was played into the
transceiver, no radio or SDR was accessed, and no RF was transmitted.
**The experimental MICIN API-26 firmware is not verified installed.** Its
earlier emulator acceptance failure was resolved as a test-harness
issue in [TX-12C](k6-micin-api26-emulator-acceptance-20261122.md)
(8 passed, one optional packed-store test skipped). The **physical**
MICIN adapter, RF response and independent hardware watchdog are
still unverified; the earlier safety gate is not bypassed.

The objective is **not limited to FSK**. MICIN is a potential signed
analog frequency-modulation actuator for a broader waveform backend;
it is not an independent RF envelope or I/Q sample interface.

## Timebase improvement over previous MICIN reference

The original `tools/k6_micin_4fsk.py` created 128 symbols at 1200/s,
requiring ~106.67 ms of payload alone plus 80 ms of guards. The guarded
experimental `c/k6/mod/txmic4.c` has a **100-ms RF-on window**. A
whole original packet therefore cannot fit inside that window even
with perfect alignment. There is also no demonstrated external
source-to-RF synchronization output from the module.

`tools/k6_micin_burst_lab.py` now exports a short **repeating**
known-voltage sequence:

- 48 shaped 4-FSK symbols / 40.0 ms, BT 0.5,
  nominal WAV peak <=0.03 of full scale (not measured MICIN volts).
- 16 known pilot symbols, then 32 held-out symbols.
- 2-ms zero guard on each end of one period; **44.0 ms per cycle**.
- 200 complete periods by default, written as a single 8.8-second
  48-kHz 16-bit mono WAV file, without attempting playback.
- Mathematical ideal timing: a 100-ms RF gate contains one complete
  44-ms period for arbitrary starting phase *if the source is already
  running seamlessly* and if RF attack, key-up/down and audio
  interruptions consume **less than 12 ms in aggregate**.

That 12-ms budget is a feasibility calculation, **not a measured RF
latency guarantee**. The current module is not wired to trigger the
audio source; a source that starts late or runs out of audio will break
the assumption. Directly driving MICIN remains prohibited without
electrical mapping and a reversible isolated, calibrated adapter.

## Why two patterns?

- **stress:** 16 pilot, then 8 consecutive symbols each at levels
  0/1/2/3, deliberately testing sustained signed frequency control.
- **balanced:** same pilot and same held-out symbol counts, but
  reordered in a repeating pattern with short-term DC balance.

Both use the existing signed normalized levels
`[-1, -1/3, +1/3, +1]`, Gaussian frequency-pulse shaping,
not AFSK audio tone frequencies. Four constant-frequency plateaus
would require a baseband modulation path with adequate DC response.

## Hypothetical analog AC-coupling results

The tool models a **first-order causal high-pass filter**, operated
in steady state across repeated cycles, at 0, 20, 100, and 300 Hz.
At symbol centers, it fits only the **16 known pilot symbols** to a
single affine voltage-to-level mapping. The 32 held-out symbols are
then classified using those frozen coefficients.

| Hypothetical HPF | Stress held-out symbol errors | Balanced held-out errors |
|---|---:|---:|
| No HPF | 0 / 32 | 0 / 32 |
| 20 Hz | 18 / 32 | 0 / 32 |
| 100 Hz | 23 / 32 | 0 / 32 |
| 300 Hz | 23 / 32 | 7 / 32 |

Normalized level RMSE in the 20-Hz case is ~0.450 for the stress
pattern and ~0.058 for the balanced pattern. **Even when pilot
classification is perfect, the held-out data can be severely
distorted.** The no-HPF stress case already has ~0.09 normalized
RMSE from Gaussian edge shaping, although it makes no symbol errors.

These are **NOT physical K6 receiver results** and do not include
RF modulation response, actual MIC bias, input headroom, ALC,
pre-emphasis, DC cancellation, soundcard AC coupling, deviation,
frequency drift, phase noise, PA, receiver EQ or emissions.
The source WAV's 0.03 full-scale level has no known millivolt
interpretation at the chip. The selected cutoff values are **models**,
not measurements of the K6's actual analog filters.

An audio high-pass filter nominally set to 300 Hz would make sustained
levels particularly unsuitable. The existing B driver has TX
pre-emphasis/LPF/300-Hz HPF controls in REG_2B, but disabling them
does **not** automatically establish a DC-coupled MICIN signal.
The candidate older-chip DC-cancel register REG_7E remains unverified
for the FD6818B; do not write speculative values.

## Reproduction (safe offline)

```sh
cd /storage5/dev/flux-native-fsk-pdm

python3 tools/k6_micin_burst_lab.py \
  --pattern stress \
  --out-dir /tmp/flux-micin-stress --repeats 200

python3 tools/k6_micin_burst_lab.py \
  --pattern balanced \
  --out-dir /tmp/flux-micin-balanced --repeats 200

python3 -m pytest -q \
  tests/test_k6_micin_burst_lab.py \
  tests/test_k6_micin_4fsk.py \
  tests/test_k6_micin_tx_patch.py
```

Both directories contain `micin-4fsk-loop.wav`,
`micin-4fsk-cycle.npy` and `micin-4fsk-loop.json` with exact
symbols, modeled error cases, hashes and explicit hardware status.
The tool refuses to overwrite existing files.

Actual test artifacts from this checkpoint are at
`/tmp/flux-micin-loopable-20261122` (stress, initial version)
and `/tmp/flux-micin-loopable-balanced-20261122` (balanced).
They are host output and not tracked in Git. The reproducible
generator and tests are the source of truth.

## Physical entry criteria before the next real MICIN RF experiment

1. Match the actual K6 PCB revision and identify MIC jack pinout,
   impedance, bias, AC coupling, input swing and adapter ground
   isolation. Do not connect a PC soundcard directly based on a
   guessed MIC pin or on the older non-B chip datasheet.
2. Scope the output voltage at the **isolated adapter** and verify
   loop continuity and absolute level without connecting the radio.
3. The emulator acceptance failure has been resolved; independently
   validate an actual hardware TX watchdog/recovery before real RF.
4. Establish a safe external timing/provenance record between
   continuously playing audio, the guarded RF gate, and received IQ.
   Demonstrate total relevant startup/truncation under the modeled
   12-ms budget rather than assuming it.
5. For any *authorized* conducted single-shot RF study, use a
   rated dummy load, verified coupler/attenuation, fixed known
   center frequency and one-shot cleanup. Score training-only
   acquisition and held-out signed frequency levels separately
   for stress and balanced patterns. Measure RF spectrum and
   recover the exact original firmware/module state.
6. If DC-heavy stress fails but balanced passes, prioritize
   AC-compatible modulations or a **verified** flat modulator.
   If actual MICIN remains high-pass, use a board-level
   reversible, properly designed alternative to recover true DC.

See the [prior MICIN bypass lab](k6-micin-bypass-lab.md) and the
[complete TX-10..TX-23 roadmap](k6-tx-next-experiments.md).
