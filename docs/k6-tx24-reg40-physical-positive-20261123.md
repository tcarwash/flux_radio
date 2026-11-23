# TX-24 — measured B-chip REG40 FM-deviation control (first physical RF pass)

**2026-11-23 Pacific, bench #17.** Single real FD6818B/BK4829
K6 conducted 433.500-MHz dummy-load RF experiment, air-coupled
RTL-SDR complex IQ at 1.024 MS/s. Experiment source and receiver were
frozen at commit **5acbd7b BEFORE the RF run**. Outcome: all predeclared
engineering criteria **PASS** for REG40 lowbyte step modulation with
one constant 1091-Hz REG71 oscillator and fixed REG70 gain24. One
transmission only; no independent second physical burst.

**GitHub queue:** [experiment #17](https://github.com/tcarwash/flux_radio/issues/17),
[request](https://github.com/tcarwash/flux_radio/issues/11#issuecomment-7116284873),
[lease start](https://github.com/tcarwash/flux_radio/issues/11#issuecomment-7116300572),
[full hardware lease release](https://github.com/tcarwash/flux_radio/issues/11#issuecomment-7116350622).
The previous Sotto FSK-13 and FSK-14 one-shot leases were fully
released before TX24 acquired its independent lease; fresh shared queue,
process/fuser and source hash checks were performed. Canonical
nonblocking Flux serial_lock('/dev/ttyUSB0') covered the complete
radio, SDR, original slot30 backup, module execution and restoration.
No permanent MCU firmware flashing or calibration mutation.

## What the physically measured RF demonstrates

**REG40 is a real, directly responsive FM frequency-deviation scale
actuator on this particular 0x4829 B-chip under the tested conditions.**

The module kept:
- **REG71 = 11264** (received approximately **1090.927 Hz** FM tone);
- **REG70 = 0x9800** (gain24, tone1 enabled);
- all FSK FIFO, synth-PLL, APC and calibration registers unchanged;
- exactly one keyed RF burst within a firmware **1800-ms TX lease**.

It changed **only REG40[7:0]** between four known values, leaving
REG40[15:8] at **0x35** and restoring the original 0x3516 afterward.
The actual received RF frequency discriminator is a 1091-Hz sinusoid
whose amplitude changes precisely when its REG40 low-byte changes.

The first two reference (baseline lowbyte 0x16) intervals were fitted
as pilots. The other eleven slots were strictly heldout, including
two observations of each nonbaseline level and five subsequent
baseline controls. No IQ data were used to alter the scorer after RF.

| REG40 word | Pilot-relative FM deviation | Approx absolute peak deviation at fixed tone | Old-family law hypothesis |
|---|---:|---:|---:|
| **0x3500** | **0.92123×** | **1093.8 Hz** | 0.92086× |
| **0x3516** | **1.00023×** | **1187.5 Hz** | 1.00000× |
| **0x3540** | **1.15125×** | **1366.9 Hz** | 1.15108× |
| **0x3580** | **1.38039×** | **1638.9 Hz** | 1.38129× |

*The absolute deviations above are SDR frequency-discriminator-derived,
not calibrated transmitter-side deviation with traceable RF test
equipment. The peak reference from pilot slots was 1187.27 Hz.
Four medians by code use all appearances, including heldout repeats;
the baseline row is therefore not numerically exactly 1.00000.*

The measured ratios closely match **(256 + lowbyte) / (256 + 0x16)**,
a formula from the older family which we explicitly did not use in
the prospectively locked pass thresholds. The simple law is now
empirically supported for **these four levels with REG40 high byte
0x35 and this particular audio-FM source** on the B chip. This is
not evidence for all unknown REG40 bitfields, arbitrary timing,
audio input modes or direct signed carrier-frequency offset.

## Every transmitted slot and raw physical evidence

| Slot | REG40 | Peak FM deviation (Hz) | Relative to two reference pilots |
|---:|---|---:|---:|
| 0 | 3516 | 1186.58 | 0.9994 |
| 1 | 3540 | 1366.91 | 1.1513 |
| 2 | 3516 | 1187.96 | 1.0006 |
| 3 | 3580 | 1638.06 | 1.3797 |
| 4 | 3516 | 1187.54 | 1.0002 |
| 5 | 3500 | 1093.43 | 0.9210 |
| 6 | 3516 | 1186.75 | 0.9996 |
| 7 | 3540 | 1366.79 | 1.1512 |
| 8 | 3516 | 1187.74 | 1.0004 |
| 9 | 3580 | 1639.74 | 1.3811 |
| 10 | 3516 | 1188.06 | 1.0007 |
| 11 | 3500 | 1094.07 | 0.9215 |
| 12 | 3516 | 1186.86 | 0.9997 |

Exact raw IQ file (10,485,888 bytes, NPY):

    ~/flux-bench/tx-injection-20261122/tx24-reg40/tx24-reg40-run01.npy

**SHA256 4115c2c6d401871e38479edd1393298b76978fbfca9f2b0f110bb28de5cd9f48**.

Metadata and unmodified before/after/locked-score files:

    ~/flux-bench/tx-injection-20261122/tx24-reg40/tx24-reg40-run01.json
    ~/flux-bench/tx-injection-20261122/tx24-reg40/tx24-reg40-run01-slot30-before.k6m
    ~/flux-bench/tx-injection-20261122/tx24-reg40/tx24-reg40-run01-locked-v1.json

Exact frozen physical API3 module source SHA256
**de786e08e3bc8d6c1c57a2f53be7d55c77deb1f7ca3babbe79e9c103bb40360e**;
compiled code **808 bytes**, SHA256
**e792a8bd77d2f055a16f9e1da025a4f7a4555b6dbb9d3417817612e5e21024f0**.
Source code is on branch research/tx24-reg40-deviation-offline-20261123
in commit **5acbd7b**; frozen proposed test protocol
[k6-tx24-reg40-physical-prospective-20261123.md](k6-tx24-reg40-physical-prospective-20261123.md).

RTL-SDR onset ~0.861984375 s, independent received-RF magnitude
quantile signal/noise proxy **43.1**, no ADC extreme samples,
preflight no overload/occupancy indication. The SDR front-end is
uncalibrated; quiet-channel evidence cannot independently certify
a dummy load, attenuator, RF power level or legal emission mask.

## Prospective criteria: all five positive

| Fixed requirement before first TX | Physical outcome |
|---|---|
| Monotone 00 < 16 < 40 < 80, each adjacent ≥3.5% reference | **PASS** |
| Heldout REG40=16 reference FM amplitude spread ≤15% | **PASS**, **0.124%** |
| Heldout reference oscillator-phase RMS ≤18° | **PASS**, **0.204°** |
| Each nonreference level replay relative change ≤15% | **PASS**, all <0.2% |
| Every nonreference settled sine amplitude / residual noise ≥4 | **PASS**, ~34–51 |
| Firmware expected 13 register intervals, source hashes and safe rollback | **PASS** |

A deliberately **post-hoc, independent cross-check** of the
original saved *discriminator time series* was performed after
the frozen primary scorer finished. It computes raw
sqrt(2)×standard-deviation within each settled source interval,
**without fitting any sinusoid, phase or oscillator frequency**.
This independent amplitude estimator gives

| Lowbyte | Independent no-fit deviation ratio | Frozen primary result |
|---|---:|---:|
| 00 | 0.92132 | 0.92123 |
| 16 | 0.99995 | 1.00023 |
| 40 | 1.15096 | 1.15125 |
| 80 | 1.37946 | 1.38039 |

All four compare within 0.001 of the frozen result, despite
completely different amplitude estimators. Raw **complex RF
magnitude** across 13 intervals varied only **0.991–1.003×**
their overall median (spread ~1.2%). This is consistent with
**constant-envelope RF FM plus programmable deviation scaling**,
not an APC/PA-power amplitude actuator. No RF wattmeter or spectral
mask is implied.

The cross-check was designed *after seeing* the capture. It is
corroborating analysis, **not an additional independent RF trial**
and does not affect the original prospective pass/fail result.
The per-slot discriminator phase errors relative to the pilot fit
stay between ~0° and -0.32° under repeated REF/changed states,
which suggests no large oscillator phase reset at these 90ms
updates **in recovered audio**, not necessarily calibrated
RF-carrier phase continuity.

## Exact module and radio restoration

Firmware returned:

    tx24 n=13 pa=1
    module load 808, returned True, result 13

Exactly ONE launch request. The original installed code SHA256
**6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522**
was backed up *before* temporary SPI write. The original
authenticated module was restored and read back verified.
Original watched VFO A **440.850 MHz**, VFO B **144.390 MHz**,
both wide FM and not memory VFOs, were restored exactly.
Final status **standby RX idle, transmitting False**; battery
approximately **8.09 V**. RTL-SDR tuner driver reattached.
The canonical Flux nonblocking serial flock was released.
A hardware-free lease release record with hashes and result
was posted in queue #11 and experiment #17. No TX retries
and no additional tests under this lease.

Offline replay on the same authorized host:

    cd /storage5/dev/flux-tx24-reg40-deviation-offline
    python3 tools/k6_tx24_reg40_deviation_analyze.py \
      --iq ~/flux-bench/tx-injection-20261122/tx24-reg40/tx24-reg40-run01.npy \
      --out /tmp/tx24-reg40-unique-offline-replay.json
    PYTHONPATH=/tmp/flux-fsklab-unicorn-deps python3 -m pytest -q \
      tests/test_k6_tx24_reg40_physical.py

The playback regression in tests/test_k6_tx24_reg40_physical.py
checks raw SHA256, original scorer unchanged, original module
readback, both VFOs, single launch, and a post-hoc independent
no-fit amplitude / RF-magnitude control. It SKIPs if this
host-local private bench IQ is not present. No raw IQ is committed
to Git.

## Implications and deliberately unproven claims

The controlled source kept the **1091-Hz audio oscillator phase**
and **REG70 gain24** unchanged, and used only REG40 scaling.
Therefore it adds a second independently programmable *deviation
gain* dimension to the previously proven REG70 gain/frequency
actuators. At the present slow 90ms dwell, these controls
can describe different magnitudes of FM deviation. The effect
matches the B-chip expected field law at four chosen words.

**NOT proven:**
- Fast or symbol-rate REG40 updates at 4800 baud (208µs/bit),
  distortion, overshoot, signal bandwidth or occupied emission mask.
- Native FSK modem and REG40 co-modulation at the same time
  (the TX24 physical source did NOT enable FSK work mode).
- Constant-envelope phase continuity at the *RF carrier* rather
  than the source audio; only a discriminator audio-phase proxy
  was fitted for this test.
- Four independently decoded RF tones, 2 bits/symbol,
  4FSK, QAM, QPSK, direct frequency offset DAC, arbitrary
  RF-IQ or independent RF amplitude+phase control.
- Reproducibility across distinct physical transmissions,
  temperature, other TX sources or other radios;
  this report covers **one physical RF trial with
  intra-trial repeated controls**.

**Next most informative experiment:** TX-24B offline first:
freeze a **native-FSK + REG40** compatibility/source audit and
known-bit control pattern. Verify whether the established Sotto
guard-word workaround is necessary/appropriate for finite
FSK frames, keep REG59 FIFO and decoder independent, and
test slow REG40 transition while long binary-mark and -space
runs are present. New source and a separately acknowledged
hardware lease are mandatory. A successful four-level FM
deviation transfer must be measured from received RF complex
IQ with strictly heldout known-bit regions before
describing a four-level scheme.

**Bottom line:** This is the first precommitted, physical
B-chip proof of a distinct REG40 deviation-scale modulation
actuator, with quantitative ratios closely following an
older-family mathematical law. It materially enlarges the
modulation-control toolbox in Flux while leaving full
arbitrary RF waveform generation unproven.
