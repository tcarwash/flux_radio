# TX-15V — FD6818B REG70 gate preserves a free-running tone phase

**Physical date:** 2026-11-22 Pacific (UTC 2026-11-23).
**Source and phase scorer frozen in commit `67dd2fa` BEFORE RF.**
See [TX-15V prospective protocol](k6-tx15v-gate-phase-protocol-20261122.md).

**Headline:** The B-chip's internal tone oscillator appears to
**continue advancing phase while REG_70 output is disabled**.
Neither a reset-to-constant-phase nor a freeze/resume model
explains the phase recovered after the gating intervals.
This suggests REG_70 is a **phase-preserving FM modulation gate**
rather than an oscillator phase reset or direct RF-IQ state register.

## Physical test

A single nominal 1091-Hz tone was programmed using
`REG_71=11264` and held unchanged throughout transmission.
The B-chip RF ID was `0x4829`. PA was continuously enabled
on the user-confirmed 433.500-MHz dummy-load setup
for the RF-on trials. REG_70 gate was 0 (OFF)
or `0x9800` (ON), toggled as follows:

- 200 ms initial ON pilot
- 57 ms OFF / 170 ms ON pilot
- 71 ms OFF / 170 ms ON heldout
- 83 ms OFF / 170 ms ON heldout
- 97 ms OFF / 170 ms ON heldout
- 100 ms OFF guard, then dekey and restoration.

The OFF durations span different fractional audio
cycles, deliberately separating phase-freeze,
free-running and reset-to-fixed-phase predictions.

The original program was bounded by one 1800-ms
transmitter lease; each of three physical transactions
returned `tx15v n=9 tone=11264` (one PA-off
readback/dry run, two conducted RF-on captures).
Original slot-30 module and both original VFOs were
restored and verified after **each** transaction.
No main firmware flash/calibration flash write.

The real SDR complex RF IQ was demodulated to a
64-kS/s frequency discriminator audio signal.
The analyzer learned the tone frequency from
**ON0 and ON1 only**, and ON1's phase served as
the prediction anchor. ON2–ON4 were independent,
held-out phase checks. Known source OFF/ON
time boundaries were applied with 13-ms margins
to avoid settled filter tails.

| Heldout phase model | Run 01 RMS | Run 02 RMS |
|---|---:|---:|
| **Free-run internally during OFF** | **0.246°** | **0.022°** |
| Freeze at disable; resume on enable | 101.66° | 101.68° |
| Reset to fixed oscillator phase every enable | 47.52° | 47.52° |

The precommitted label criterion was an RMS below
35° with a ≥30° margin from the runner-up.
**Free-running passed decisively in both independent
real-RF captures.** This is stronger than merely
observing same-tone repeated REG_71 writes while
still enabled, since the FM tone was actually
suppressed between measured ON windows.

Additional physical measurements:

| Quantity | Run 01 | Run 02 |
|---|---:|---:|
| Pilot-fitted 1091-Hz effective frequency | 1090.92736 Hz | 1090.92630 Hz |
| Median ON tone FM-discriminator amplitude | 1187.7 Hz | 1186.6 Hz |
| Median OFF FM-discriminator variation RMS | 54.7 Hz | 54.1 Hz |
| ON/OFF ratio | 21.7× | 21.9× |
| ON2–ON4 residual degrees, free-run model | −0.124, −0.208, −0.351 | +0.023, +0.017, −0.025 |

The ON and OFF amplitudes establish that REG_70 did
gate out observable audio-FM modulation while
the K6 stayed keyed. The similar phase prediction
after four unequal disabled intervals is consistent
with a free-running internal phase accumulator
and the same TX/RX transfer function each time.

These are **recovered FM audio oscillator phase**
estimates, not *direct RF carrier phase EVM*.
The receiver phase model and exact oscillator
implementation still have uncertainty.
The equivalent RF-carrier phase trajectory must
be separately scored from complex IQ before
claiming a usable RF phase-state DAC.

## Data and recovery provenance

Compiled guarded source code SHA256:
- PA-off `2c6c1b52126a9e4ac9326084829a347a34195210a3cbba4e26a7d91dfc5bd790`
- PA-on `a9987bd70a03781de8c0c9a873238b902bd07abd0de4d4a53b70c7d7284570ed`

Trusted original slot-30 executable SHA256:
`6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`.

Original IQ SHA256:
- PA-off `tx15v-gate-0-run01.npy`:
  `18a7eefc76e47f480d048233ea0c18b0519b2ddd11756da8681f1f66244da5e1`
- PA-on `tx15v-gate-1-run01.npy`:
  `c604f00858620f89f91371b17188055ec353887b0f106dd93f1f105674e3696d`
- PA-on `tx15v-gate-1-run02.npy`:
  `7a38693cdd33b621def55d988324d2ed545e40701b66d4d0e7d7f5f8d7af3c06`

Archive:
`~/flux-bench/tx-injection-20261122/tone-oscillator/`.
For each run, `<stem>.json` is the complete physical transaction,
`slot30-before-<stem>.k6m` backs up the original executable
and `<stem>-phase-v1.json` for PA-on runs contains the
pilot-only FM phase model scoring and phase residuals.

### Reproduce without hardware

```sh
cd /storage5/dev/flux-native-fsk-pdm
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
 tests/test_k6_tx15v_gate_phase.py \
 tests/test_k6_tx15v_gate_phase_analyze.py

python3 tools/k6_tx15v_gate_phase_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15v-gate-1-run02.npy \
 --out /tmp/tx15v-analyze-new-unique.json
```

## Modulation research implication

REG_70 enable is a separate **serial-controlled
gating actuator** for the B-chip tone-FM path.
The oscillator appears to keep its phase clock
running even while modulation is disabled.
This means source software can alternate
segments of constant RF carrier frequency
with phase-preserving sinusoidal FM modulation,
while keeping the radio keyed and serial
active. It does **not** set the oscillator's
starting phase or command an arbitrary
signed DC carrier offset.

**Next TX-15W candidate:** modulate REG_70's
gain bits, not only its enable bit. If
gain values produce calibrated, monotonic,
settled and phase-preserving FM deviation,
they could form a finer source-controlled
frequency/phase trajectory actuator.
Use an explicit gain ladder, coherent RF IQ
measurements, heldout prediction and
spectral occupancy before promoting this as
a general modulation backend.

**Also preserve:** TX-20 separate APC
RF envelope actuator, TX-18 digital
baseband, TX-17D state-re-latch,
TX-16 subaudio, TX-11 native FSK FIFO,
TX-14 external FM reference and an
external IQ daughterboard.
