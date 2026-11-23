# Flux TX-12D2 — strict spectral RF gate + pilot-frozen MICIN 4-FSK scoring

Date: November 22, 2026 local.
Worktree: `/storage5/dev/flux-native-fsk-pdm`.
Status: **offline archived RF / synthetic IQ analysis only**;
no audio playback, radio command, SDR capture, TX or flashing.

## Why a second independent MICIN receiver?

The parallel [TX-12D hardware fixture preflight]
(k6-micin-physical-fixture-preflight-20261122.md) documents the
probable K1 jack contacts, electrical safety gates and a
software-only 48-kHz/RTL-IQ observer
(`tools/k6_micin_rf_observe.py`).

This additional independent tool
(`tools/k6_micin_rf_decode.py`) emphasizes **separate RF-event
detection and fail-closed source qualification**, rather than
reusing a phase or frequency trajectory selected by eye:

- Reads **only saved** 1.024-MS/s interleaved U8 RTL-SDR IQ.
  No hardware I/O / TX.
- Imports the prior physical-lab
  `tools/k6_micin_rf_edge_analyze.py`, independently
  regressed against the earlier PA-off RF startup event. This
  detector uses **2-ms FFT windows** and compares energy
  around the expected carrier with local off-channel flanks.
- Resamples the expected carrier to 48 ksample/s complex IQ
  and reports the regular decimated power gate **separately**.
  Weak short pulses can be visible in narrow spectral bins
  even when their total passband power is low.
- Requires a spectral RF gate **≥90 ms**, i.e. two complete
  44-ms cycles plus 1-ms margins at each end, to ensure that
  one whole reference cycle is present under any arbitrary
  timing offset between a continuously looping audio source
  and a bounded radio RF gate.
- Uses only the known **16-symbol Gaussian-shaped pilot** to
  select a source-cycle start and fit one RF frequency offset,
  one scale and frequency polarity. Requires
  `abs(pilot_correlation) ≥ 0.93` and measured deviation
  inside a conservative, explicit acceptance range.
- Freezes all parameters before evaluating the 32 held-out
  4-FSK symbol-center measurements, normalized frequency
  residual and decoded four-level decisions.
- Intentionally **does not use held-out labels** to select
  stimulus pattern, time, gain or RF offset.
  Both references share the same 16-symbol pilot.
  A deliberately wrong stress-vs-balanced pattern can still
  acquire the pilot, but fails the heldout symbols.

Neither a positive pilot nor low symbol error on an unspecified
RF capture establishes **MICIN physical source provenance**,
correct source cabling, coherent complex-IQ EVM, RF frequency
calibration or spectral compliance. These require independent
source/fixture evidence and test equipment.

The initial known LO relative offset of -280500 Hz comes from
earlier **uncalibrated** RTL-SDR reception at 433.750 MHz while
the radio's VFO was nominally 433.500 MHz. This offset is not a
universal correction for all SDRs.

## Real RF *negative control* (no analog 4-FSK transmitted)

This independent physical recording was taken **before** the
present software changes:

`~/flux-bench/modulation-micin-20261121/txmic-paoff-rfcheck-sdr-iq.npy`

The older physical MICIN feasibility checkpoint at
`/storage5/dev/flux-tx-waveform-superset/docs/k6-micin-70cm-api26-rf-feasibility-20261121.md`
reports a short RF event while entering PA-off state; it
explicitly reports **no analog microphone source connected**.

The newly imported 2-ms FFT detector recovered the event as:

| Measurement | From archived SDR |
|---|---:|
| Event interval | **3.040–3.066 s** |
| Event duration | **26.00 ms** |
| Peak spectral rise over local flanks | **+22.55 dB** |
| Complete arbitrarily aligned 44-ms source cycle? | **No** |
| Four-level pilot qualification? | **No** |

A time-average baseband power detector alone saw only ~4.46 dB
peak contrast and missed this short event under an 11-dB
threshold. The narrow spectral detector **finds the RF event**
but still refuses to decode a whole 4-FSK loop.

This separates the statements **“RF was seen”** and
**“the commanded four-level frequency trajectory was carried.”**

Saved new negative-control JSON:

`~/flux-bench/tx-injection-20261122/micin-api26-rebuild/paoff-negative-decode-v2.json`

This is air-coupled, uncalibrated receiver evidence; neither
the +22.55 dB value nor the offset measures absolute TX
power, spurious compliance or radio-side MICIN voltage.

## Reproduction — NO LIVE HARDWARE ACCESS

```sh
cd /storage5/dev/flux-native-fsk-pdm

# Prior physical PA-off observation (negative control)
python3 tools/k6_micin_rf_decode.py \
  --iq ~/flux-bench/modulation-micin-20261121/txmic-paoff-rfcheck-sdr-iq.npy \
  --pattern balanced --rf-offset-hz -280500 \
  --out /tmp/flux-micin-paoff-recheck-unique.json

# Synthetic positive/negative, arbitrary source phases, inverted
# frequency polarity, wrong heldout message, no-RF and real PA-off data
python3 -m pytest -q \
  tests/test_k6_micin_rf_decode.py \
  tests/test_k6_micin_rf_edge_analyze.py
```

Observed: **21 tests passed**. This includes both the original
independent 2-ms RF edge detector's regression tests and the
new strict qualification suite. A deliberately wrong data
pattern shares the same pilot, so acquisition alone cannot
make a bad heldout message appear correct.

If a real **post-fixture** capture is ever obtained, run
`tools/k6_micin_rf_decode.py` with its original *predeclared*
reference pattern and the independently measured receiver
carrier offset. The tool refuses to overwrite existing reports.

## Physical prerequisites remain open

This check does not alter the [TX-12C experimental MICIN
firmware acceptance](k6-micin-api26-emulator-acceptance-20261122.md).
The full compiled-image emulator passed 8 and skipped one optional
packed-library test, but the physical B-chip MICIN voltage
transfer is still unknown.

The [separate detailed preflight]
(k6-micin-physical-fixture-preflight-20261122.md) covers
uncertain Kenwood K1 contact mapping, shared external PTT/
serial wiring and isolated breakout construction. Until those
actual measurements exist, **do not inject PC audio into the
radio jack**, presume the WAV 0.03 amplitude is a safe mV
level, or claim that MICIN is a flat, independent RF
frequency-control input.

The broader [TX-10..TX-23 roadmap](k6-tx-next-experiments.md)
continues to track MICIN, native-FSK, APC/envelope, tone,
TCXO/synthesizer and digital-baseband RF waveform routes.
