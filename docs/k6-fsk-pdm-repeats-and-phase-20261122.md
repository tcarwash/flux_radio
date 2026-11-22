# Flux K6 — repeated 300-baud 8-FSK/PDM, native-chip recovery and phase FIR

Date: 2026-11-22. Research worktree: `research/native-fsk-pdm` at
`/storage5/dev/flux-native-fsk-pdm`. This is a finished **bench
checkpoint**, not a claim that the transceiver synthesizes arbitrary
complex I/Q RF or meets a spectral mask.

Related: [all-path experiments](k6-tx-next-experiments.md),
[prior live RF findings](k6-tx-injection-live-evidence-20261122.md),
[FSK pilot vector](k6-fsk-pdm300-vector.json).

## Radio, path and transmitted frames

The user confirmed that the K6 was connected to a dummy load; an RTL-SDR
was air-coupled to the radio. This measures relative RF but **not**
calibrated transmitter power or leakage. The SDR recorded at 1.024
MS/s with manual 0-dB gain and a 433.750-MHz receiver center for a
433.500-MHz RF signal (the ≈30.5-kHz apparent offset includes SDR error).

During this work, the device advertised firmware build
`95d6b260d4122287` with FD6818B FSK service enabled. Before keying,
the installed slot-30 signed library envelope was **byte-for-byte**
verified against the existing physical test file
`~/flux-bench/tx-injection-20261122/fsklab-pdm300-slot30-envelope.k6m`:
SHA-256
`223d6976090e4d29af70b9ae2c9a76a60dd57aa37720f4fe1950b44061c90a29`.
Its guarded 64-word module is API level 3, uses firmware-owned
TX-on/off and does not write calibration or flash.

**Three new, one-shot RF transmissions** were performed using this same
installed slot and test pattern (one request per recording, no automatic
retry). All three module calls returned 64 words sent; raw SDR IQ was
saved; each VFO was restored to 144.390 MHz; the final live status
reported TX idle and battery around 7.78 V. No firmware or module was
flashed, installed, or overwritten in this pass.

Files on `tyler-ldesktop` (large IQ data not checked into Git):

    ~/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-{01,02,03}.{npy,json}

The fourth data point is the already existing
`~/flux-bench/tx-injection-20261122/native-fsk-pdm300-slot30.npy`.
All four use the same fixed sequence, so they assess repeatability
and receiver processing rather than statistical diversity.

## Original 8-level analog frequency discrimination

The saved previous analyzer at
`/storage5/dev/flux-tx-waveform-superset/tools/k6_tx_pdm_analyze.py`
finds the burst, fits frequency levels/symbol start using only the
first **16 known 8-FSK symbols**, and classifies the following 28.

| Capture | Direct frequency-level symbol errors | Heldout frequency RMSE | Heldout frequency correlation |
|---|---:|---:|---:|
| First | 2/28 | 74.914 Hz | 0.996699 |
| Repeat 01 | 2/28 | 74.244 Hz | 0.996640 |
| Repeat 02 | 2/28 | 74.497 Hz | 0.996859 |
| Repeat 03 | 1/28 | 80.433 Hz | 0.996312 |
| **All four** | **5/112** | sequence-specific | highly correlated |

**107/112** is the held-out direct frequency-discriminator symbol
score, not a general-purpose BER and not direct complex RF validation.

The former phase analysis fits a CFO/complex phase rotation using
only known training and scores the withheld portion. Across these
captures, it gives low packet-wide **native-reference** complex IQ
coherence (about 0.176–0.352) and ideal-eight-level coherence about
0.123–0.222. Each comparison uses its own 1.6-kHz IQ reconstruction
filter, 4800-chip/s ideal phase model and known training. These numbers
were not independently reoptimized on the tail.

## New independent native-chip receiver: 0/1,792 heldout errors

[tools/k6_fsk_pdm_chip_decode.py](../tools/k6_fsk_pdm_chip_decode.py)
performs a **new acquisition without importing any saved frame start**:

1. Detect RF activity and downconvert the 1.024-MS/s U8 IQ by +280.5 kHz,
   decimating to 128 ksample/s.
2. Use **only the first 320 known FIFO bits** (256 alternating,
   32 zero, 32 one) to find packet start and native bit clock and
   calibrate low/high frequency plateaus.
3. Decide all subsequent native bits using that pilot-derived
   threshold, without using their truth for timing or levels.
4. Use the next **16 known PDM symbols** to learn a mapping from the
   count of 1 bits in each 16-chip symbol to one of 8 levels.
5. Decode the final **28 symbols / 448 bits** without using their
   expected bits or symbols during any model fitting.

| Capture | Pilot correlation | Pilot half-separation | Heldout native chips | Heldout popcount symbols |
|---|---:|---:|---:|---:|
| First | 0.993934 | 1,209 Hz | 0 errors / 448 | 0 errors / 28 |
| Repeat 01 | 0.993880 | 1,214 Hz | 0 / 448 | 0 / 28 |
| Repeat 02 | 0.992841 | 1,200 Hz | 0 / 448 | 0 / 28 |
| Repeat 03 | 0.993676 | 1,194 Hz | 0 / 448 | 0 / 28 |
| **Total** | — | — | **0 / 1,792** | **0 / 112** |

The learned popcounts per level were **1, 3, 5, 7, 9, 11, 13,
15**. The native-bit clock selected from the restricted pilot search
was 4,800 bits/s in every capture.

This is a viable method of receiving the **encoded multilevel
information** from the binary modem. It is **NOT an ordinary analog
8-FSK receiver**; that receiver scored 107/112, above. It is also not
proof of arbitrary waveform synthesis: the pulse density (and exact
binary chip order) may carry data reliably even when the actual
multilevel RF phase trajectory differs from the reference.

### Reproduce with the saved physical IQ

~~~sh
cd /storage5/dev/flux-native-fsk-pdm

python3 tools/k6_fsk_pdm_chip_decode.py \
  --manifest docs/k6-fsk-pdm300-vector.json \
  --iq /home/tyler/flux-bench/tx-injection-20261122/native-fsk-pdm300-slot30.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-01.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-02.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-03.npy \
  --out /tmp/flux-fsk-pdm-native-heldout-review.json
~~~

The concrete saved analysis:
`~/flux-bench/tx-injection-20261122/phase-repeat/native-chip-holdout-v1.json`
includes source IQ SHA-256 hashes and the acquired timing/plateaus.
The checked-in manifest preserves the exact original FIFO truth and
its own SHA-256. Synthetic controls confirm heldout symbol-label
changes change only scoring, not acquisition or decoded symbols.

## Transition response, local phase and the FIR reconstruction lead

Frequency-calibration holds in the 64-word PDM packet settle near
**±1.19–1.21 kHz**, in agreement with the intended 1.205-kHz
deviation. But rapidly alternating native chips show smaller
instantaneous-frequency samples at their centers, as expected for
smeared transitions. At the 32-zero to 32-one pilot edge, an IQ
10-kHz LPF/discriminator/smoother estimates a 10–90% transition time
around **156–172 µs**, against 208 µs per native bit. Extrapolated
phase-bridge residuals for this single transition ranged
0.018–0.170 rad. The estimates include **noncausal receiver DSP**,
so neither early threshold crossings nor absolute rise-time values
may be attributed solely to the physical RFIC. A single near-zero
bridge is encouraging but not a universal phase-continuity proof.

A short-span complex IQ comparison against ideal 2-FSK, with its
independent per-window arbitrary complex phase removed, gave median
coherence around **0.985** over individual 300-baud symbols. Longer
packet-wide comparisons were much weaker. In three of the four
captures the symbol-to-symbol phase-error step sequences were
extremely repeatable (pairwise correlations **0.9965–0.9983**);
the remaining repeat-03 trace was an outlier.

### System identification from known chip-phase increments

[tools/k6_fsk_phase_fir.py](../tools/k6_fsk_phase_fir.py) samples
unwrapped *received RF phase* at every known native-chip boundary.
The phase increment of chip `i` is approximated by a regression
on the already-known command sequence. Models:

- single binary chip (+ intercept);
- causal three-tap chip history;
- adjacent three-tap (previous/current/next chip);
- adjacent seven-tap (three neighbors each side).

Only chip phase increments **3–575** (573 known training values)
are used to fit the coefficients. Chips **576–1020** (445 phase
increments) are predicted **without any heldout RF measurement
fitting**. The accumulated residual phase coherence is measured
with a single initial anchor at the tail boundary; no tail CFO
fit or phase rotation is performed.

| Capture | Single-bit model | Adjacent 3-tap | Adjacent 7-tap |
|---|---:|---:|---:|
| First | 0.096 | **0.716** | 0.773 |
| Repeat 01 | 0.088 | **0.641** | 0.698 |
| Repeat 02 | 0.074 | **0.782** | 0.834 |
| Repeat 03 | 0.069 | **0.293** | 0.252 |

These are **chip-boundary accumulated phase scores**, not continuous
complex IQ EVM. The test records exact coefficient sums, prediction
RMSE and residual frequency bias.

First three three-tap fits gave approximately:

    phase_increment_i =
        const + 0.21 * bit_(i-1)
              + 1.23 * bit_i
              + 0.11 * bit_(i+1) radians

where each `bit` is -1 or +1. Summing these gives about
1.55 radians per binary state per native chip, near the expected
1.57 rad at ±1,200 Hz / 4,800 bit/s. This is persuasive evidence of
**finite-memory phase/frequency modulation**, rather than a fully
instantaneous ±D switching process.

The fitted "future chip" coefficient reflects how we partition a
transition interval and how the **offline zero-phase receiver
filter** operates. It is **not proof of noncausal radio hardware**.
The physical modulator impulse response and channel/filter effects
still need to be disentangled. Model fidelity is lower on repeat 03;
the outlier's heldout phase bias and symbol alignment need
independent verification before producing a TX predistorter.

The model is stable when receiver low-pass cutoffs are moved among
5, 8, 10, and 16 kHz (single-binary: ~0.07–0.11, adjacent three-tap
for the first three: ~0.64–0.79). This is not simply an arbitrary
single-filter-setting artifact.

### Reproduce phase FIR

~~~sh
python3 tools/k6_fsk_phase_fir.py \
  --manifest docs/k6-fsk-pdm300-vector.json \
  --chip-report /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/native-chip-holdout-v1.json \
  --out /tmp/flux-fsk-phase-fir-heldout-review.json
~~~

Actual saved phase report:
`~/flux-bench/tx-injection-20261122/phase-repeat/phase-fir-v1.json`.
The script checks each source capture's SHA-256 against the native
chip-decoder record before fitting.

## Relative RF occupied-spectrum comparison

The saved RF captures were also compared with the same payload-interval
Welch spectrum procedure in
[tools/k6_fsk_spectrum_replay.py](../tools/k6_fsk_spectrum_replay.py).
The receiver center and tuning correction were fixed for all trials.
The script subtracts a local median PSD noise estimate over 7.5–15
kHz offsets, integrates only a ±15-kHz baseband window, and reports
the 0.5–99.5% power span. It uses `detrend=False` explicitly so a
carrier at baseband DC is **not** inadvertently removed.

| Signal, 433.500 MHz nominal | Relative 99% span | Relative power outside ±3 kHz |
|---|---:|---:|
| Baseline gain-21 binary 2-FSK | 4,720 Hz | 0.188% |
| 8-FSK/PDM original | 4,918 Hz | 0.163% |
| 8-FSK/PDM repeat 01 | 4,917 Hz | 0.161% |
| 8-FSK/PDM repeat 02 | 4,918 Hz | 0.166% |
| 8-FSK/PDM repeat 03 | 4,949 Hz | 0.176% |

The 8-level PDM waveform is ~200 Hz wider in the measured 99%-power
span than the conventional binary baseline. The fraction outside
±3 kHz was small in the SDR's relative, noise-subtracted received
spectrum, but the measurements **do not constitute emission-mask
compliance, calibrated occupied bandwidth, adjacent-channel power,
harmonic suppression or radiated power**. In particular, low-level
noise, front-end response and omitted frequencies outside ±15 kHz
can bias these estimates.

~~~sh
python3 tools/k6_fsk_spectrum_replay.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/native-fsk-gain21-slot30.npy \
       /home/tyler/flux-bench/tx-injection-20261122/native-fsk-pdm300-slot30.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-01.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-02.npy \
       /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-03.npy \
  --out /tmp/flux-fsk-relative-spectrum-review.json
~~~

The actual checked physical replay was saved outside Git at
`~/flux-bench/tx-injection-20261122/phase-repeat/relative-spectrum-v2.json`.

## Alternative transmit paths remain active

The [full all-path TX queue](k6-tx-next-experiments.md) still
includes MICIN analog baseband, filtered MCU DAC/PWM physical injection,
native DMR/4-FSK or transparent modem, tone oscillator, subaudio,
reference/TCXO modulation, synthesizer static offsets, I2S, PA/APC
amplitude and external arbitrary IQ.

A separate, **nontransmitting** attempt to exercise the existing
inert API-level-3 tone-oscillator module through the high-API RAM
runner was rejected by the current firmware with **RAM verification
-3** (the local host runner allowed high API, but the device did not).
The radio stayed RX-idle at 144.390 MHz. We did **not** install or
overwrite a library slot, flash a firmware variant, or claim that the
eight-tone RF path was tested. The existing `tx8probe.c` source and
Cortex-M0 emulator tests provide the software starting point for
TX-15, but a separately verified library-module or compatible RAM
execution method is still required for hardware measurement.

The independent MICIN hardware investigation in
`/storage5/dev/flux-tx-waveform-superset/docs/k6-micin-70cm-api26-rf-feasibility-20261121.md`
already confirmed that the FD6818B's TX MICIN ADC can be enabled
in module TX (0xC1FA → 0xC1FE) through API 26 while PA is disabled,
and REG_2B TX audio filters can be bypassed/restored.
**No external shaped analog waveform has been fed to MICIN yet**:
an isolated adapter, actual microphone bias, amplitude and coupling
must be checked at the physical jack. An RF carrier or mic-ADC
enablement alone does not prove the desired modulation response.

The *older* FD6818 programming guide describes a DMR/dPMR bypass
in which externally shaped 4-FSK enters MICIN, and optional
reference/TCXO modulation. That guide's register map is not the
same as the tested FD6818B; never blindly transplant the old
0x7E/0x40 or state recipes. Public discussions of FD6818B/BK4829
compatibility and other DMR devices support further **source
comparison**, not unverified writes:

- https://www.glaver.org/blog/UV-17/FD6818.pdf
- https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE/issues/18
- https://radiofoundry.org/docs/spec/hardware-dm32
- https://radiofoundry.org/docs/spec/hardware-dm1801pro

In some DMR radios a **separate HRC7000/HRC6000 digital modem**
handles 4-FSK baseband. Therefore DMR support in a radio using an
FD6818-family RF chip must not be treated as proof of native
four-level modulation inside the RF transceiver.

## Next discriminating experiments

1. **RF phase repeatability:** new independent PDM payloads/seeds and
   300/600 symbol/s trials, including simultaneous chip demod and
   conventional 8-FSK analog decision. Keep the existing known vector
   as a fixed reference.
2. **Modulator response:** capture isolated transition/pulse patterns
   at several native baud rates and compare 5/10/20-kHz receiver
   filters to isolate actual RFIC impulse memory. Fit a physically
   causal response model to the known training and test on an unseen
   chip sequence.
3. **Phase correction:** build an offline predicted complex-IQ
   generator using the measured response. Compare heldout
   continuous-time RF complex EVM, occupied bandwidth and spurs.
   A good chip-boundary score alone is insufficient.
4. **Transparent / streaming FSK:** inspect framing and FIFO IRQ
   refill on the actual 0x4829 driver. Prove gap/phase continuity
   across frames, watchdog and recovery before indefinite streams.
5. **MICIN:** with a verified isolated mic-jack adapter, inject a
   calibration tone and then 4-FSK Gaussian baseband, capture RF,
   compare TX filter on/off with exact register restoration.
6. **Other physical sources:** investigate tone generator step
   timing, CTCSS, TCXO reference, PA-envelope and undocumented
   DMR/digital baseband paths separately. Do not treat the
   B-chip's RF TX register map as BK4819 or FD6818 v1.0.
7. **Waveform API:** capability-gated `tx_begin`, `tx_write`,
   `tx_end`, `tx_abort`, with sample/symbol clocks, RF budget,
   fixed-frequency limits, watchdog and receiver-confirmed cleanup.

The current K6 can demonstrably carry eight-level **encoded
information** via native-FSK PDM. It has *not* demonstrated
arbitrary complex amplitude/phase waveform generation, continuous
phase-accurate PSK, SSB or QAM through its internal RF chip.
