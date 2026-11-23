# Flux — arbitrary RF waveform engine: architecture and physical TX paths

**New alternative TX source audit:** [TX-11 native multilevel / DMR TX](k6-tx11-multilevel-source-audit-20261122.md) finds existing B-driver binary FIFO controls and separately implemented 4-FSK/DMR receivers, but **no verified native four-level transmitter**. MICIN analog 4-FSK remains a separate candidate, not ruled out.\n\n**New physical fidelity finding:** [FSK-24 archived real-RF holdout](k6-fsk-physical-iq-holdout-20261122.md) scores four real 300-baud 8-level PDM/FSK captures against independently prescribed binary-FSK IQ. Despite reliable chip decoding, heldout complex EVM is ~107–171%; this is FSK-only RF, not simultaneous FSK/APC or a calibrated arbitrary-waveform transmitter.\n\n**New independent-target / pilot-holdout verification checkpoint:** [TX-21B complex-IQ measurement tools](k6-polar-iq-heldout-verifier-20261122.md) compile an independently specified 1024-bit FSK + 80/100% envelope target, then fit one offset/CFO/complex gain on pilot-only and score truly held-out complex IQ. Synthetic tests pass; this did NOT transmit and the physical concurrent-FSK/APC test remains blocked.\n\n**New joint-IQ feasibility checkpoint:** [FSK phase + APC amplitude surrogate and phase-coupling tests](k6-polar-combined-iq-offline-20261122.md). An offline counterfactual composition from *separate* physically captured FSK and APC RF signals yielded ~4.5% optimistic **amplitude-only** EVM; two archived APC-only RF waveforms also show directional phase-fit perturbations at 80/100% amplitude transitions. Neither is real simultaneous-FSK/APC RF or full EVM; the hardware joint-TX code attempt was blocked by a tool safety gate, and no new transmission or flashing occurred. The actual production radio remained unchanged.

**New measured envelope evidence (2026-11-22):** [FD6818B APC hysteresis and high-side modulation-rate experiments](k6-apc-fine-hysteresis-bandwidth.md) show a second physically functioning RF control dimension. For this K6, the APC target responds over 80-100% at ~25-48 Hz but loses substantial modulation contrast by ~91 Hz; dropping to 70% initiates ~8-ms periodic RF fades that persist until the target returns toward 80%. The offline polar-IQ workbench now includes an evidence-constrained measured APC feasibility diagnostic and idealized 80% amplitude-floor EVM bound. This does **not** demonstrate synchronous combined phase/envelope IQ, usable audio-rate amplitude modulation, calibrated power, or spectral compliance. All experimental firmware was rolled back to the original build after the tests.

Date: November 22, 2026.
Worktree: /storage5/dev/flux-native-fsk-pdm
Branch: research/native-fsk-pdm

## Goal and precise meaning

This project is **not limited to FSK**. The target is a waveform engine
accepting an arbitrary sampled complex baseband signal, determining which
parts the K6's available controls can reproduce, compiling a bounded
TX plan for an appropriate backend, and measuring the emitted RF against
the original signal.

The ideal RF signal is:

    s(t) = A(t) * cos(2*pi*fc*t + phi(t))

where A(t) is an arbitrary nonnegative envelope and phi(t) is
arbitrary continuous unwrapped phase. The desired instantaneous
frequency offset is:

    df(t) = (1 / (2*pi)) * d(phi(t))/dt

**Two independent control dimensions are needed** for arbitrary
complex RF: reliable phase/frequency modulation AND reliable envelope
modulation. A saturated, constant-envelope FM transmitter alone cannot
emit arbitrary amplitude-varying QAM/SSB/OFDM I/Q, regardless of how
precisely its FSK bits are sequenced.

For the experimental K6, the hardware-control problems are:
- How can we supply df(t) with sufficiently low latency, dynamic
  range, phase continuity and sample rate?
- Can we make A(t) take more than off/on values, accurately, quickly
  and repeatably, even though the RF PA normally operates saturated?
- Can both be synchronized, with bounded TX, no unintended framing,
  controlled RF spectrum, and a proven recovery path?

Do not equate arbitrary **modulation-control voltage** with arbitrary
**RF spectrum**: FM PLL/group-delay, audio HPF/DC cancellation,
PA gain feedback, and tuned RF output filters all distort the request.

## Manufacturer physical architecture

A publicly available manufacturer BK4829 datasheet (2023, v1.0)
shows the 0x4829-compatible architecture. The RF synthesizer
uses a PLL/fractional-N VCO; FM modulation is performed within
the RF synthesizer, downstream of a mux combining voice (MIC ADC),
CTCSS/CDCSS and SELCALL/DTMF/FSK generators. The RF transmitter
uses a buffer and saturating PA driver for constant-envelope FM.
The device's pins include MICP/MICN, VRAMP and PAOUT but there is
no general external I/Q DAC interface in the published block diagram.

Source (PDF, functional block diagram figure 2-1 / transmitter section):
https://manuals.plus/m/b03fb6dba016158ab9f4b5b3d1aeb1d9ea178efc44cc11c2c08398621553cdc9.pdf

The FD6818B radio reports ID 0x4829, and the B-chip driver uses
matching register roles, but **no board-specific hidden I/Q input,
PLL waveform port or VRAMP connection has yet been proven**.
The older FD6818/0x4819 programming guide is NOT a safe register
map for the B silicon.

### Current measured/known physical injection routes

| Route | Current evidence | Can affect phase/frequency? | Can independently affect envelope? |
|---|---|---|---|
| Native 2-FSK PLL modem, 0x59/70/72/5F | Real binary/PDM RF, 8-level symbol reconstruction, 0/1,024 heldout bits in two-frame test | **Yes** (binary FM, shaped approximation via PDM) | **No** |
| Programmable single-tone oscillator 0x70/71 | **New live physical**: 64 timed tone updates, 47/48 heldout identifications | **Yes** via audio-FM tone modulation; not static 8-level RF FSK | **No** |
| MICIN with API26 | Real radio enables MICIN ADC and disables TX voice filters, restores afterward; waveform not externally injected | **Likely** via analog FM path; actual transfer function unmeasured | **No** |
| Fractional-N/TCXO modulation or hidden offset DAC | Datasheet and older family architectural hints; no viable B-chip continuously updated candidate | Possible | No |
| CTCSS/DCS subaudio source | 55-Hz tail audio modulation has physically been observed | Limited, low-frequency FM | No |
| PA gate CW/OOK | Real PA on/off and timed CW path, with cleanup | No clean, independent fine phase control from gate alone | **Only binary** |
| APC target REG_36[15:8] | Existing B-chip driver writes calibrated target value when PA enabled; no dynamic RF transfer function measured | Not by itself | **Candidate**, unverified latency, noise, linearity or safety |
| External DAC/coupled reference or daughterboard | Board-specific hardware modifications needed | Potentially | Potentially, if combined with RF envelope stage or external IQ modulator |

All values in the right-hand columns are **capabilities or
hypotheses**, not claims that arbitrary I/Q RF has been demonstrated.

## New physical test — independent tone-oscillator FM

We extended existing c/k6/mod/tx8probe.c with a strictly
opt-in, 433.500-MHz, 150-symbol/s, dummy-load-only,
bounded RF-on variant (maximum 1,800-ms firmware TX lease).
The normal TX8 source remains **PA-off or inert by default**.
The enabled build requires a deliberate conducted-fixture and
independent-watchdog acknowledgement.

- 64 deterministic symbol positions, eight audio tone words
  for 450, 750, 1050, 1350, 1650, 1950, 2250, 2550 Hz.
- Firmware executed all 64 updates with **0 late updates**.
- Received RTL-SDR capture at 433.750 MHz (nominal RF 433.500 MHz),
  complex U8 IQ, 1.024 Msamples/sec, no calibrated RF power.
- After FM demodulation, a training-only time-alignment search
  uses the **first 16 symbols only**.
- The remaining 48 are reserved for a strictly held-out
  matched-audio-tone decision.

| Result | Physical observation |
|---|---:|
| Emulator/firmware TX events | 64 |
| Firmware late events | 0 |
| Training tone IDs | 15 / 16 correct |
| **Held-out tone IDs** | **47 / 48 correct** |
| Held-out median true-vs-best-other tone-score | 3.09 |
| Nominal symbol update rate | 150/s |
| SDR relative RF active interval | approx. 0.899–1.314 s |
| Final radio | TX idle, both VFOs 144.390 MHz |
| Original slot-30 executable | Restored and verified after trial |

The first/last tone positions appear affected by RF startup/
shutdown timing. This is audio **FM modulation of RF**; it is
not direct multilevel RF shift keying, a sustained arbitrary waveform,
or proof of RF phase continuity at each oscillator reprogramming
event. A first whole-sequence timing fit gave 63/64 IDs, but the
more conservative held-out result above is the one to use.

Recording:
~/flux-bench/tx-injection-20261122/tone-oscillator/tx8-tone150-run01.npy

Analysis:
~/flux-bench/tx-injection-20261122/tone-oscillator/tx8-tone150-analysis-v1.json

Reproduction (new output path):

    python3 tools/k6_tx_tone_rf_analyze.py \
      --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx8-tone150-run01.npy \
      --out /tmp/flux-tx8-analysis-repeat.json

Delivery was reversible: an original signed module envelope was
saved before temporary slot-30 substitution; original code was
restored using a new replay serial and verified by readback.
No MCU firmware was flashed for this tone study.

## New first-party arbitrary-IQ *target* representation (offline)

tools/k6_rf_iq_polar_workbench.py accepts sample-rate-tagged
complex numpy I/Q or generates reference vectors: CW, chirp,
constant-envelope multilevel FSK, AM, smoothed QPSK and illustrative
16-QAM. It:

1. Verifies complex finite 1D samples and sample-rate limits.
2. Converts I/Q into envelope A[k], unwrapped phase phi[k],
   and instantaneous RF frequency offset df[k].
3. Tests four **idealized** backends: FM-only constant envelope,
   binary OOK envelope, 4-step envelope, and ideal continuously
   controllable amplitude, all with a user-selected hypothetical
   frequency-offset cap.
4. Reconstructs baseband I/Q, computes normalized EVM and coherent
   correlation after only one global complex-gain fit, and reports
   clipped frequency-sample fraction and envelope demand.
5. Exports JSON + .npz for later physical backend benchmarking.

None of these mathematical backends transmits RF; in particular,
the continuous-amplitude output **does not mean REG_36 supports
continuous envelope modulation**.

Reproduce example models:

    python3 tools/k6_rf_iq_polar_workbench.py \
      --mode chirp --out-prefix /tmp/flux-iq-chirp
    python3 tools/k6_rf_iq_polar_workbench.py \
      --mode am --out-prefix /tmp/flux-iq-am
    python3 tools/k6_rf_iq_polar_workbench.py \
      --mode qam16 --out-prefix /tmp/flux-iq-qam16

Illustrative normalized EVM at an **assumed** ±1,200-Hz
instantaneous-frequency capability (no actual radio exercised):

| Reference | FM-only modeled EVM | Ideal independent envelope modeled EVM |
|---|---:|---:|
| CW | 0.000 | 0.000 |
| Sweep chirp (-750 to +750 Hz) | 0.000 | 0.000 |
| Constant-envelope 4-FSK reference | 0.000 | 0.000 |
| AM | **0.444** | 0.000 |
| Illustrative QPSK | 0.978 | 0.978 |
| Illustrative 16-QAM | 0.971 | 0.967 |

The QPSK/16-QAM illustrative waveforms contain short, very rapid
phase transitions, so at ±1,200 Hz the proposed *frequency* control
is also insufficient even if an ideal envelope control existed.
The out-of-band/steep phase behavior is waveform-specific and not a
claim that QPSK or QAM requires 24-kHz deviation under all pulse
shaping. These are **feasibility diagnostics**, not measured RF EVM
or standards-compliant pulse-shaping examples.

## Immediate next hardware experiment: APC/envelope response

The **live FD6818B driver** uses REG_36:

- [15:8] = calibrated APC target byte, loaded from radio calibration.
- [7] = PA driver enable.
- [5:0] = band-specific gain configuration, 0x22 for UHF.
- It constructs REG_36 = (pa_target << 8) | 0x00A2 during UHF TX.
- PA-off uses 0x0022.
- Existing module fd_write purposely forbids direct writes to
  REG_36, which is good for safety.

That gives us a **real bounded power-control candidate** without
assuming a hidden I/Q port. However, the closed-loop APC target
may change power too slowly or nonlinearly for RF symbol-envelope
control. No on-hardware power-vs-target measurement or amplitude
step response exists yet.

The proper next test is to add a **firmware-owned opt-in callback**
with a permitted, calibrated PA target range and mandatory
readback, bound by the existing TX watchdog. Flashing the test
firmware is now authorized by the user; verify an actual recovery
image and physical bootloader restore path before trying it.
Measure several short steady target levels, then a 2- or 3-step
envelope response. Evaluate absolute RF-off leakage, relative RF
power, envelope rise/fall times, overshoot, spectral regrowth
and ripple. Do not modify calibration flash or expose
unrestricted module writes to REG_36.

If APC cannot follow modulation without large spurs, report this
as a constraint instead of claiming arbitrary-IQ capability.

## Next firmware-facing architecture

Design one backend-neutral control plan for arbitrary sampled
signals:

    IQ target -> polar trajectory + spectral budget + constraints
              -> (modulator backend, optional envelope backend)
              -> bounded execution schedule + software/hardware abort
              -> SDR capture -> complex-IQ measurement against target

Backends should declare exact capabilities (frequency slew,
phase continuity, deviation, minimum hold time, sample clock,
envelope levels/rise time, carrier leak, startup/tail, simultaneous
control support), and refuse plans they cannot satisfy.

Suggested priority:
1. **TX-20/21 APC amplitude step bench**, using the above
   firmware-owned callback and a verification path.
2. **TX-12/13 MICIN analog waveform injection** using a
   physically measured, reversible adapter; this is a stronger
   continuous-frequency-control prospect than FIFO-based 2-FSK.
3. **TX-15 programmable tone oscillator**, improve transition
   and instantaneous RF phase measurements, perhaps finer
   scheduling if SPI timing permits.
4. **TX-17/19 direct synthesizer/reference modulation**, probe
   only source-supported, recoverable B-chip candidates; a
   static tuned carrier is not an IQ modulator.
5. **FSK-09 FIFO continuous streaming**, valuable fallback
   for constant-envelope phase trajectories; prior attempt
   failed safely without a valid almost-empty event.
6. **TX-23 external I/Q modulator** if constant-envelope chip +
   saturated PA cannot implement the desired envelope fidelity.

The complete IDs TX-10..TX-23 and native-FSK work remain in
docs/k6-tx-next-experiments.md. Any work on one entry point
must not eliminate another.

## Current operational state

One independently watchdog-bounded 433.500-MHz RF oscillator
transmission was executed on a user-confirmed dummy load.
The original slot-30 source code was restored with signed
readback. The radio is TX idle and both VFOs were restored
to 144.390 MHz. This study did **not** flash MCU firmware or
erase radio calibration / arbitrary SPI contents.
