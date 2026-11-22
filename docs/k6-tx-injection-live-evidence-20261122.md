# Flux K6 TX signal injection — live FD6818B evidence and next discriminating tests

Date: 2026-11-22; independent review in `research/native-fsk-pdm`.
Companion to [the complete TX experiments queue](k6-tx-next-experiments.md),
[FSK-05 RF checkpoint](k6-fsk-05-live-rf-checkpoint.md), and
[MICIN bypass lab](k6-micin-bypass-lab.md).

## Device concurrency and provenance

At 13:13 local time the radio reported the earlier live firmware
`f5c33782022b4016` and idle RX. During this investigation, the live
firmware changed **without this agent flashing it**. At 13:14:31 the
radio advertised `95d6b260d4122287`, `fsk_rx=true`,
`module_api_max=26`, live library mutation and module execution. A
subsequent status read confirmed both VFOs at 144.390 MHz, RX idle,
battery ~7.8 V. This indicates another live workflow was active or
had just completed. **This investigation did not transmit, install
modules, or flash** to avoid conflicting hardware access.

The same development host already held new physical test artifacts in
`/home/tyler/flux-bench/tx-injection-20261122/`, dated 12:53–13:06.
Those captures belong to that other workflow. We **independently
reanalyzed the saved IQ**, not the physical transmitter.

## Confirmed native binary-FSK deviation control

Six archived RF recordings named
`native-fsk-gain{16,21,24,32,40,48}-slot*.npy` contain native-FSK
transmissions, according to their saved module metadata. We applied
the same deterministic, offline DSP to each:

- Crop 0.78–1.05 seconds around the saved RF burst.
- Mix by +280,500 Hz relative to the RTL-SDR center, decimate 8×
  to 128 ksample/s and use a 10-kHz fourth-order low-pass.
- Demodulate frequency from complex sample-to-sample phase differences.
- Restrict to high-amplitude burst samples and select the two
  prominent frequency histogram peaks.
- Bin resolution: 87.5 Hz; these are **approximate relative plateau
  separations**, not metrology-grade frequency deviations.

| REG_70 gain code | Measured mark/space separation | Estimated deviation ±D |
|---:|---:|---:|
| 16 | 2,100 Hz | ±1,050 Hz |
| 21 | 2,275 Hz | ±1,138 Hz |
| 24 | 2,450 Hz | ±1,225 Hz |
| 32 | 2,888 Hz | ±1,444 Hz |
| 40 | 3,500 Hz | ±1,750 Hz |
| 48 | 4,200 Hz | ±2,100 Hz |

All six captures show two resolvable peaks. This supports the
**physical gain-to-FSK-deviation hypothesis**, not merely a model.
A simple least-squares description over these six points is

    separation_hz ≈ 911 + 66.0 × gain_code

The actual curve may not be linear between measured points and
depends on chip settings. For the desired ~±1,200-Hz binary modem
deviation, **gain 21–24** is a measured starting bracket. Do not
interpret the intercept as a physical zero-gain deviation.

Reproduce from saved IQ without touching the radio:

    python3 tools/k6_fsk_gain_replay.py \
      /home/tyler/flux-bench/tx-injection-20261122/native-fsk-gain*-slot*.npy \
      --output /tmp/flux-fsk-gain-replay.json

The actual replay JSON is preserved at
`/home/tyler/flux-bench/tx-injection-20261122/native-fsk-gain-independent-replay.json`.
The script and synthetic tests are checked into this research branch.
This estimator deliberately does not claim absolute TX power,
adjacent-channel compliance, coherent phase quality or an unbiased BER.

## Physical eight-level frequency DAC

A separate saved capture,
`native-fsk-pdm300-slot30.npy`, has a corresponding training-only
analysis in `native-fsk-pdm300-analysis.json`:

- Native FSK rate: 4,800 bits/s; target multilevel symbol rate:
  300 symbols/s; 16 known training symbols and 28 held-out symbols.
- Held-out nearest-tone decisions: **26/28 correct** (2 errors).
- Held-out frequency RMSE: **74.914 Hz**; correlation: **0.996699**.
- The training-only affine frequency slope was 0.983155.
- A separate saved phase-realignment report gives **0.791603 training-native complex coherence** after an additional 675 µs alignment shift. That is a pilot-aligned phase metric, not independent coherent EVM validation.
- These are **known-training aligned** results, not a blind decoder,
  RF complex-EVM result or emission-mask measurement.

This is strong initial evidence that pulse-density modulation of the
binary native-FSK stream can synthesize several **average RF frequency
levels** at 300 symbols/s. It does not yet establish phase-coherent
MFSK, PSK or continuous arbitrary waveform fidelity. Verify results
across independent repeated bursts, startup timings, SNR, pulse
sequences, and actual phase trajectory.

## Ranked TX entry points (distinct physical mechanisms)

| Path | Present confidence | Main limitation | Next discriminating test |
|---|---|---|---|
| **Native 2-FSK** | Real RF captures and gain-dependent separation | Binary constant-envelope, framing/PLL | Full deviation, phase, burst-repeatability and bandwidth matrix |
| **PDM 4/8-FSK via native 2-FSK** | One promising physical 8-level capture | Phase fidelity, high-frequency dither, FIFO length | Repeated held-out decode and RF complex phase/EVM against target |
| **Native 4-FSK / transparent modem** | B-chip architectural lead, no validated TX register sequence | Undocumented mode/packing | Trace B-specific register driver and safely identify four stable RF plateaus |
| **MICIN FM baseband injection** | Hardware connection known; bypass/API-26 compiled | Requires isolated physical analog feed; normal TX disables MICIN | Mic bias/coupling verification; 1-kHz tone then shaped 4-FSK with filters on/off |
| **MCU DAC/PWM into MICIN** | Plausible if physical adapter is added | PA11 is speaker output, not MICIN; ripple/filtering | Bench-only attenuated DAC and electrical isolation |
| **Tone oscillator / AFSK** | Existing audio-FM functionality | Tone stepping, AF filters, no direct RF I/Q | Timed REG_70/71 updates and phase continuity |
| **Subaudio CTCSS/DCS path** | Known low-frequency modulation feature | Small bandwidth and unknown polarity/control | Read-only map, then low-power signed-deviation test |
| **TCXO/reference injection** | Possible board-level hardware experiment | PLL filtering, drift, oscillator loading | Schematic/oscilloscope before any reversible injection |
| **LO frequency registers** | Older non-B chip latched writes | No fast symbol-rate control shown | Read-only B-chip map and static trim behavior |
| **Digital I2S/MCBSP** | Only older FD6818 guide | B-chip register map differs; no demonstrated TX pins | Physical board continuity and read-only register trace |
| **PA/APC envelope** | Existing CW and power-control experiments | Residual carrier, nonlinear PA, unverified amplitude fidelity | Calibrated rise/fall and off-leakage into load |
| **Polar TX (phase + amplitude)** | Hypothesis only | Need independent phase and envelope actuators | First prove both independently; then IQ reconstruction |
| **External RF/IQ hardware** | Technically viable alternative | Extra cost/complexity | Benchmark external DAC/SDR and filtering for SSB/QAM/OFDM |

No single verified K6-internal path yet provides a general
two-dimensional complex I/Q DAC. Constant-envelope frequency modulation
can carry 2/4/8-FSK, shaped FSK, chirps and some phase trajectories
if the RF chip settles and preserves phase; it cannot by itself create
arbitrary amplitude-varying SSB/QAM/OFDM.

## Prioritized continuation

1. **Avoid conflicting device ownership.** Establish an exclusive
   session before additional live writes, TX or flashing. Preserve the
   currently running build identity and full SPI snapshot.
2. **Native 2-FSK characterization:** gain 16–48 plateau/deviation
   repeated across 1200/2400/4800 bits/s; quantify repeatability,
   occupied bandwidth, PA envelope and phase bridges.
3. **Frequency-DAC RF validation:** 300-symbol/s 8-FSK across at least
   ten independent bursts, true held-out decode and symbol error
   statistics, coherent RF phase error and out-of-band energy. Test
   75/150/300/600 symbols/s.
4. **FSK streaming:** determine whether native FIFO can refill or
   chain without phase discontinuity; longer transmissions are a
   gating feature for useful arbitrary frequency trajectories.
5. **Analog injection:** separately validate MICIN electrical input,
   audio bypass and shaped 4-FSK. Do not conflate it with PA11 speaker
   PWM or direct I/Q.
6. **Undocumented modem features:** B-specific 4-FSK, tone/subaudio,
   reference/PLL, I2S, PA/APC, with reversible tests and independent
   before/after register snapshots.
7. **Backend design:** separate `native_fsk`, `native_fsk_pdm`,
   `micin_analog`, `tone_fm`, `pa_envelope`, and `external_iq`
   behind a capability-gated, bounded `tx_begin / tx_write /
   tx_end / tx_abort` API with waveform metadata, sample/symbol clock,
   watchdog, RF budget, and recovery invariants.

**Current result:** Real multilevel frequency synthesis is plausible
and measured. General arbitrary complex RF waveform generation is
still unproven on the internal FD6818B. The cleanest next gains are
phase/streaming measurements and the physically separate MICIN path.
