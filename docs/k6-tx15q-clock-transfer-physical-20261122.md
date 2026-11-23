# Flux TX-15Q — physical audio-phase errors were mostly clock/timing mismatch

**Date:** 2026-11-22 local / 2026-11-23 UTC.
**Hardware:** K6 FD6818B/0x4829, two **already archived** 433.500-MHz
dummy-load RF / air-coupled RTL-SDR I/Q captures from TX-15P.
**This checkpoint:** offline IQ reanalysis only; **no new RF TX, flash,
SPI writes, or module installs**. TX-17 receiver-only NOAA reception
preflight is described separately below.

The [prior physical TX-15P](k6-tx15p-physical-phase-evidence-20261122.md)
found 48/48 heldout audio-tone IDs in both captures, <0.5° recovered
same-word audio-phase RMS, but ~100–110° apparent audio-phase RMS
on changing the REG_71 frequency word. That earlier analysis
assumed its nominal scheduler ran at **exactly 150 updates/s**.

## The main overlooked variable: the transmit slot timebase

The probe's own MCU timer counts firmware cycles, but the SDR sample
clock, MCU TIM14/TIM17 and RFIC audio oscillator have **different clock
domains**. Firmware-reported `late=0` only establishes that the
scheduler met its own timer deadlines. It does not calibrate
1/150 second against the SDR clock.

`tools/k6_tx15_transition_clock.py` performs two independent
training-only measurements using the **same saved RF captures**:

1. Acquire nominal source start using **the first 16 known audio-tone
   symbols only**, as in the prior analyzer.
2. Frequency-demodulate the saved RF I/Q to recovered audio, bandpass
   200–3500 Hz, Hilbert-transform that audio to instantaneous
   frequency, and smooth over 65/64,000 second.
3. Find strong predeclared frequency-step midpoints (absolute
   tone difference >450 Hz) only in the **pilot at indices
   12, 13 and 15**, then fit a linear clock
   `time(index)=start + index * period`.
4. Freeze both parameters and compare the remaining **17 strong
   heldout edge times** without fitting to their timing.
5. Refit the 64 received audio sinusoid phases using the
   **pilot-calibrated timestamp** and the observed period.
6. Fit one effective tone-frequency-relative phase lag from
   only **nine qualified changed-word pilot phase edges**.
   Freeze it and score all qualified changed-word **heldout** phase
   residuals. Same-word heldout phase observations are preserved.
   The model is *not* a full learned transfer function.

### Physical SDR result, two independent emissions

| Measurement | RF 01 | RF 02 |
|---|---:|---:|
| Archived known-tone heldout decode | 48/48 | 48/48 |
| Pilot strong transition indices (clock) | 12, 13, 15 | 12, 13, 15 |
| Pilot-fit received tone-update rate | **149.558/s** | **149.683/s** |
| Apparent schedule skew vs RTL sampling clock | +2,958 ppm | +2,121 ppm |
| Heldout strong transition count | 17 | 17 |
| Heldout midpoint error with fixed 150/s, pilot offset only (median abs.) | **634 µs** | **467 µs** |
| Heldout midpoint error after pilot clock fit (median abs.) | **17.9 µs** | **20.1 µs** |
| Pilot-fit effective audio phase/transfer delay | −19 µs | −11.5 µs |
| Prior naive changed-word heldout phase RMS (uncorrected nominal clock) | **110.2°** | **100.7°** |
| Changed-word phase RMS with pilot clock correction | **17.7°** (19 edges) | **17.9°** (19 edges) |
| Changed-word phase RMS with pilot clock **and pilot-only phase-delay** correction | **9.4°** | **12.7°** |
| Corrected residual circular coherence | 0.989 | 0.976 |
| Heldout same-word phase RMS with corrected clock | 0.43° | 0.37° |

All reported phase residuals above are **the audio-frequency tone
phase recovered from the SDR RF FM discriminator**. They are
**not** direct coherent RF carrier phase, RF I/Q EVM, or an
emissions-mask/SSB/QAM claim.

**Key conclusion:** the large previous REG_71 *different-frequency*
phase changes mostly arose because we projected the physical signal
onto a nominal 150/s schedule that drifted relative to the SDR clock.
With a pilot-only clock fit, the phase residual falls ~6-fold before
any audio phase-lag fit. A one-parameter pilot audio transfer term
reduces the heldout remainder further. The physical evidence is
**compatible with phase-continuous programmable audio FM** and
therefore improves TX-15's prospects as an AFSK/constant-envelope
audio source. It does not prove continuity through each RFIC
digital internal state or arbitrary RF waveform generation.

### Important uncertainties — do not overstate

- The observed **+2,958/+2,121 ppm** is the **source update schedule
  relative to the RTL-SDR sample clock** as detected by an
  audio-domain Hilbert midpoint. It is **not a direct calibrated
  STM32 oscillator measurement**. TX/RX filtering, SDR clock offset,
  edge timing and board temperature can contribute. In separate
  stable-tone fits, the radio oscillator's audio frequency was
  generally within a few hundred ppm of the nominal RFIC
  register-derived tone. That suggests the measured update-rate
  discrepancy is significant, but does not uniquely locate it.
- Just **three** high-contrast pilot transitions anchor the
  two-parameter update-time model. The independent **17 heldout
  midpoints** strongly support that model for these two captures,
  but it is not a universal clock calibration. Synthetic
  instantaneous-frequency discontinuities can shift Hilbert
  midpoint estimates and bias the fitted clock by ~1000 ppm.
  That intentionally remains a documented regression negative
  control. A future vector should use more and longer-spaced
  reference transitions in the **pilot**, ideally over more
  of a burst, with RFIC/TIM timer observations.
- The one-parameter **−19/−11.5 µs audio phase correction**
  is *effective* timing/phase transfer. It includes unknown
  components from RX demodulation, analog FM TX, SDR conditioning,
  finite tone settling and source clocks. It cannot be called
  an FD6818B's physically isolated group delay.
- A small local audio phase error within these specific
  slot sequences is not proof of phase fidelity to an
  arbitrary continuous RF target. No true FSK+APC joint TX,
  envelope qualification, broadband EVM or spectral-mask
  measurement was performed.
- The analysis uses the known source vector to locate tone
  transitions. It is not blind modem acquisition, and the
  heldout test is conditional on the predeclared waveform.
  Neither clock nor phase-lag parameters are refitted using
  heldout observations.

## Reproducibility

Files on `tyler-ldesktop`:

```
/storage5/dev/flux-native-fsk-pdm/tools/k6_tx15_transition_clock.py
/storage5/dev/flux-native-fsk-pdm/tests/test_k6_tx15_transition_clock.py
/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/tx15p-phase150-run01-clock-model-v1.json
/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/tx15p-phase150-run02-clock-model-v1.json
```

The saved `*.npy` files and the physical transaction
metadata remain unchanged. The analyzer opens *no*
serial device, SDR driver, microphone, radio or network socket.

Example (choose a new output filename; will not overwrite):

```sh
cd /storage5/dev/flux-native-fsk-pdm
python3 tools/k6_tx15_transition_clock.py \
  --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15p-phase150-run01.npy \
  --out /tmp/tx15q-run01-unique.json
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps python3 -m pytest -q \
 tests/test_k6_tx15_transition_clock.py \
 tests/test_k6_tx15_phase_rf_analyze.py \
 tests/test_k6_tx15_phase_vector.py
```

## Parallel TX-17 RX-only reference preflight

We checked the living K6 over its normal Prolific serial cable,
`/dev/ttyUSB0`, without RF transmission. The radio was
idle RX, at 144.390 MHz on both VFOs and ~8.04 V battery.
Two RX-only temporary tunes (each restored, via `finally`)
were recorded:

| Frequency | Reported K6 RSSI | Squelch |
|---|---:|---|
| 162.550 MHz (previous NOAA candidate) | −93 dBm | closed |
| 162.475 MHz (alternative) | −97 dBm | closed |

An RTL-SDR receive-only test with 0 dB tuner gain found
no confidently strong NOAA reference near 162.550 MHz.
This does **not** prove the weather station is absent
or the tuner cannot receive it. It only means that in this
specific setup the external steady RF reference was not
yet adequately verified for a **controlled** B-chip RX-IF
PLL-latch test.

**The 0x38/0x39 and 0x3B/0x3C TX-17 experiment is therefore
not physically executed.** With no stable known carrier,
receiver-noise zero crossings would not distinguish a
synthesizer register latching from moving. No B-specific
latch verdict can be inferred. The established non-B
0x4819 latched-register result remains specific to its
previously tested hardware.

Final serial K6 status: RX idle, both VFOs back at
**144.390 MHz**, no key-up or flash. The separate
MFJ-225 process using `/dev/ttyUSB4` was untouched.

## Next discriminator / implementation impact

1. Treat TX-15 as a candidate **phase-continuous audio FM
   backend** with a measured *per-run* scheduler clock,
   rather than merely 8-tone AFSK labels.
2. Prepare a stronger **TX-15R** waveform with at least
   8–12 frequency-change pilot edges spanning a longer
   prefix, plus blinded heldout symbols and repeatable
   same-pair revisits. Measure amplitude/phase modulation
   response and occupied RF spectrum, not just labels.
3. Investigate firmware timer calibration: TIM14/TIM17's
   assumed 96-MHz clock, MCU RC-vs-RFIC reference,
   observable temperature drift, and ability to align
   the MCU write schedule to an actual reference.
4. Continue the RX-only B synthesizer register tests
   only when a sufficiently strong, stable external
   carrier is available, with exact register backup,
   no flash/calibration mutation, and verified RX restore.
5. Preserve native direct 2-FSK/PDM, APC/polar,
   subaudio, external reference, I2S and digital
   baseband paths until each has appropriate real
   complex RF-IQ evidence.
