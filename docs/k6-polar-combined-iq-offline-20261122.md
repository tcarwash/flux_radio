# Flux TX-21 — physical FSK + APC polar feasibility gate

Status: OFFLINE and source-interface analysis only.
Date: 2026-11-22 local (2026-11-23 UTC).
Branch: research/native-fsk-pdm.

## Objective

Show *simultaneous* RF **frequency/phase modulation** and
independently controlled **amplitude** in one K6/FD6818B TX lease,
with directly captured, decoded complex SDR IQ and an explicit
target-versus-measurement error calculation.

This is a different requirement from (a) proving FSK independently,
(b) proving APC independent amplitude modulation, or (c) multiplying
two independent recordings together offline.

The known K6 is on the user's dummy load. This checkpoint did **not**
transmit, flash, or modify the installed module library; the
attempt to stage a combined experimental transmitter module was
blocked by a remote tooling safety check and was NOT bypassed.
The current K6 reports its original production firmware build ID
95d6b260d4122287 (API26), with TX idle and both VFOs at 144.390 MHz.

## Current independent physical evidence

1. **Native FSK:** A 64-word / 1024-bit, 4800-baud native-FSK FIFO
   waveform has been physically observed. Gain-24 RF frequency
   clusters approximate a 2.4–2.5 kHz mark/space separation;
   this is uncalibrated SDR evidence and does not establish
   absolute RF frequency accuracy. Independent 8-FSK/PDM
   captures also exist with fully specified symbols.
2. **APC envelope:** The B chip's calibrated low-power target
   REG_36=0xB4A2 gives an 80–100% envelope actuator, with
   an experimentally stable 24–48 Hz full-cycle response.
   Amplitude swing retains only ~39–40% of its 24-Hz
   reference at ~91 Hz. Crossing 70% causes repeatable
   low-end hysteresis/periodic carrier fades until return
   toward 80%.
3. **APC phase disturbance:** During APC-only constant-carrier
   transmissions, local pre/post carrier-phase extrapolations
   around actual 80–100% transitions give reproducible
   directional offsets (see table below). No FSK was applied
   during those APC-only captures.

### New phase-fit evidence

The receive-only tool tools/k6_apc_phase_coupling.py identifies
the 16 actual low/high transitions in each recorded 20-ms
half-step segment, then fits the unwrapped RF carrier phase
separately 2–7 ms before and 2–7 ms after each edge. It compares
their **phase extrapolations to the estimated edge time**.
This is NOT necessarily a mathematically instantaneous phase
discontinuity; PA load effects, temporary frequency pulling,
PLL response and SDR receiver artifacts can all contribute.

| Phase metric | APC fast run 1 | Run 2 |
|---|---:|---:|
| Median 100→80% edge phase-fit offset | +0.1405 rad | +0.2207 rad |
| Median 80→100% edge phase-fit offset | −0.2828 rad | −0.2642 rad |
| Median absolute edge offset | 0.1936 rad | 0.2313 rad |
| Median absolute nonedge comparison offset | 0.0648 rad | 0.0268 rad |
| Largest absolute nonedge comparison offset | 0.1144 rad | 0.1036 rad |

The directional sign pattern holds for all 16 transition
estimates in each recording. The effect is larger than
typical nonedge comparison measurements and thus warrants
investigation before synchronized IQ claims.

The FM phase derivative in steady high/low intervals also
differs modestly (approximately 8–10 Hz between amplitude
states, as estimated in this uncalibrated receiver). This may
indicate RF carrier pulling; do not ascribe it uniquely to
the transmitter without an independently calibrated receiver.

Original archived inputs and tool reports:

    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run01.npy
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run01.phase.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run02.npy
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run02.phase.json

## OFFLINE counterfactual polar composite (NOT a transmitted waveform)

tools/k6_polar_surrogate.py takes a **real native-FSK IQ
recording** and a **different real APC amplitude IQ recording**,
downconverts both against the same nominal SDR IF, extracts
the FSK phase and separately measured APC amplitude, and
multiplies them to create a *counterfactual* complex signal.
Both actual source captures independently document the
dummy-load experiment. Neither contains simultaneous
FSK and APC.

The *target* phase is set equal to the measured FSK phase.
An optional global time shift minimizes the APC mismatch
against an ideal 80/100% sequence. Therefore the reported
EVM is **amplitude-only, optimistically aligned, and is
not combined-hardware EVM**. It is not a calibrated
end-to-end modem performance number.

| Independent RF recordings multiplied offline | Counterfactual amplitude-only EVM |
|---|---:|
| Native FSK gain-24 + APC run 1 | 4.54% |
| 300-baud PDM/FSK + APC run 2 | 4.56% |

Each report preserves the explicit fields:

    combined_polar_tx_demonstrated: false
    hardware_combined_iq_evm_measured: false

Model exports (for experiments and downstream algorithms):

    ~/flux-bench/tx-injection-20261122/apc-lab/polar-offline-surrogate-fsk24-apcfast1.json
    ~/flux-bench/tx-injection-20261122/apc-lab/polar-offline-surrogate-fsk24-apcfast1.npz

    ~/flux-bench/tx-injection-20261122/apc-lab/polar-offline-surrogate-pdm300-apcfast2.json
    ~/flux-bench/tx-injection-20261122/apc-lab/polar-offline-surrogate-pdm300-apcfast2.npz

The APC-only phase-fit results above are **not included** in
the counterfactual 4.5% EVM. Any physical joint transmission
could have more phase error, even if the two independent
actuators work as they did separately.

## Firmware interface feasibility, not a completed implementation

Read-only inspection of the current opt-in API27 firmware
source shows two logically separate paths:

- The native FSK registers (mode/control, deviation gain,
  bit clock, framing, FIFO) are already module-addressable
  except for deliberately forbidden state/PA registers.
- The guarded firmware-owned API27 apc_set(percent) callback
  writes *only* REG_36[15:8] after checking B silicon ID,
  the active firmware-owned TX lease, REG_30 TX state,
  calibrated low-power PA target, exact test frequency and
  TIM14 watchdog readiness.
- The native-FSK FIFO should progress autonomously once
  enabled, in principle allowing the module to request APC
  updates before the FSK frame completes.
- PA disable/re-enable and RF-mode restoration need precise
  sequencing: they have **NOT** been demonstrated while
  both actuators are enabled simultaneously.

These observations identify a plausible interface contract,
not a proved concurrent transmitter or a safe finished patch.
A tooling safety check blocked staging the joint TX module
in this session; no alternate tool was used to bypass it.

## Definition of done for the first REAL joint-IQ experiment

Before any hardware attempt, validate an inert/compiled image
and emulator restoration path against the original SPI catalog,
explicitly confirm the fixture and watchdog, and retain the
known bootloader-reflashable production image and full SPI
backup. No raw user-module access to REG_36 or REG_30 is
needed or appropriate.

The first measured target should use **one short native-FSK
frame with a small 80/100% amplitude pattern**, never the
unstable 70% region. The FSK frame and amplitude pattern
must have **independently known sequences** and a fixed
time base. Only one bounded TX is permitted per capture.

Success requires, in the *same actual SDR IQ recording*:

1. Both native-FSK frequency states (or known PDM
   frequency trajectory) are independently detectable.
2. The 80% and 100% carrier amplitude levels are both
   detectable during the active FSK payload, not solely
   before or after it.
3. A known pilot/preamble permits estimation of fixed
   timing, carrier offset and complex gain using *training
   data only*. Heldout payload samples retain a reliable
   two-dimensional complex reconstruction.
4. The EVM estimate compares the **independently specified
   ideal IQ target** to measured RF. Do **not** reuse
   the measured FSK phase as the ideal target.
5. Measure amplitude-step-induced frequency pulling,
   phase perturbation and residual phase error separately.
6. The radio returns to RX idle, both VFOs are restored,
   the original module code is read back, and factory
   calibration/module library are unchanged. On any
   refusal, timeout or ambiguous state, dekey, restore
   and report *without automatic TX retry*.

### Stop/go design implications

A successful proof of two simultaneous actuator dimensions
still would **not** imply an arbitrary broadband IQ
transmitter. The native 64-word FSK FIFO has bit-rate,
framing and finite-buffer constraints. APC is already
significantly attenuated around 90 Hz, has 70% hysteresis,
and produces receive-observed phase perturbations at
amplitude transitions. Conventional SSB, high-rate QAM
and OFDM require much wider/smoother amplitude/phase
actuators than presently observed.

Keep the independent physical routes (MICIN baseband,
GPIO/PWM-to-MICIN, tone oscillator, subaudio, LO/TCXO,
I2S and external I/Q stage) in the main roadmap rather
than substituting this one candidate for all of them.
