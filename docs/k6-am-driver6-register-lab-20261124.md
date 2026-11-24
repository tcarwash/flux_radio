# K6 B-chip AM TX feasibility: bounded REG36 driver-step simulation

**2026-11-24 · independent non-Sotto modulation track · OFFLINE ONLY.**

This is a true-*RF-amplitude* AM **hypothesis**, not the existing AM receive
mode and not microphone audio over FM. It builds on the five-mode waveform
models, the causal CW register replay, and the virtual SoapyFang channel to
identify what hardware evidence would be needed to support true AM and
possibly later polar SSB. All virtual commands remain synthetic. **No radio,
GPIO, RF transmitter, real SPI write, PA state or RFIC register is accessed.**

## Candidate path and conservative constraints

The sole candidate here is the open-loop `REG_36[5:0]` six-bit driver control
investigated in [TX-29 #22](https://github.com/tcarwash/flux_radio/issues/22).
The simulator imposes a declared maximum gain code **`0x22`**, consistent with
the working assumption for a low-power UHF gain setting recorded in the
experiment queue. It generates no higher code, and all codes remain
nonnegative. That code is **not a measured RF-power calibration**, and
these records are **not safe to replay on hardware**: the actual `REG36`
contains other APC/PA configuration fields that must not be replaced by a
bare six-bit number. In particular, real register writes would need to
preserve masked high fields, a guarded stock-calibration ceiling, board ID
and power-range verification, independent TX watchdog and restore.

The experiment drives a 250-ms, 1-kHz sine-modulated **70% conventional AM**
envelope. Its maximum desired amplitude maps to code `0x22`, and its minimum
maps to a positive, lower code, preserving the modeled carrier. This assumes
RF output voltage is *linear and monotonic* with the six-bit gain setting:
**that assumption is unproven and is the key physical gate**. A hypothetical
`REG30[3]` controls the virtual PA gate. The actual `REG30[3]` is not known
to be a safe independent physical PA gate; it is only the model's explicit
on/off indicator.

`sim/k6emu/rf/am_trace.py` serializes the necessary commands to **one
complete three-wire transaction per write slot**. It emits an initial
gate-off, prepares driver gain, enables the hypothetical gate on a later
slot, and skips redundant gain writes during the waveform. The final gate-off
is scheduled at or after the waveform end, never squeezed into a partial
SPI slot. A causal replay through `sim/k6emu/rf/tx_trace_replay.py`
reconstructs the hypothetical PA envelope using the same request timeline.
A regression feeds those words through the actual **K6Board virtual
three-wire bitstream decoder**, captures their timestamps and verifies they
match before computing IQ. There is **no production C or Rust modulation
code** in this change.

## Signal chain and independent receiver measurements

```text
Known AM reference, 1 kHz / 70%, 48 ksamples/s
  -> normalized downward-only REG36 gain requests + virtual gate (hypotheses)
  -> causal 6-bit RF-voltage amplitude model, optional analog PA lag
  -> optional hypothetical AM-to-PM phase coupling
  -> 48-kHz cf32 synthetic IQ -> SoapyFang SigMF RX-only FIR
  -> host analytic IQ/envelope analyzer (NOT K6 native AM RX)
  -> recovered AM depth, envelope THD/correlation, carrier phase RMS,
     upper/lower sideband balance, key-off tail
```

The host independently measures amplitude and phase after the *actual
SoapyFang simulated receiver FIR*. Its 25-ms beginning/end measurement trim
excludes carrier startup and the FIR final tail from stationary-tone THD;
startup and final unkeying are scored separately, rather than silently
mislabeling them as waveform distortion. SNR defaults to 45 dB in the
simulation. The THD result is **envelope-audio harmonic distortion** of the
virtual AM signal, not conducted radio transmitter THD/IMD or a regulatory
emission limit. Carrier phase and sideband measurements reveal distortions
not visible in the AM detector envelope alone.

## Model results — deterministic, not physical measurements

| Nominal virtual SPI register-write interval | Command count | AM depth after RX | Envelope audio THD after RX | Virtual-AM decision |
| --- | ---: | ---: | ---: | --- |
| 5 µs, hypothetical fast SPI | 10,004 | 70.7% | 0.04% | PASS model |
| 35 µs, hypothetical fast SPI | 5,003 | 70.8% | 0.04% | PASS model |
| **117 µs**, software 3-wire timing assumption | **2,001** | **70.8%** | **0.37%** | **PASS model** |
| 250 µs | 1,001 | 71.3% | 31.49% | FAIL distortion/correlation |
| 500 µs | 501 | 71.4% | 31.50% | FAIL distortion/correlation |
| 1,000 µs | 4 | ~1.0% | ~190% | FAIL; 1-kHz target aliases into near-DC |

The **117-µs register model is just fast enough** for a clean *mathematical*
1-kHz envelope if the chip could change actual RF voltage without lag.
This is an optimistic upper bound, not a proof of true AM on the K6. The
250-/500-µs steps retain approximate modulation *depth* but exhibit heavy
waveform distortion: tracking depth alone is **not an adequate pass test**.
At 1,000 µs the sampling and 1-kHz audio coincide and the desired tone is
almost entirely lost.

A separately hypothesized 0.5-ms rise / 0.765-ms fall analog PA response
at a 117-µs register rate drops recovered AM depth to roughly **16%** and
leaves nonzero key-off tail, causing a model failure. Longer 2.0-/5.25-ms
analog attack models also fail. These analog response values are *not* a
measured transfer function for `REG36[5:0]`; they are sensitivity controls
informed by older, different APC/PA observations.

A **60-degree input AM-to-PM coupling parameter**, in an independent
unverified mathematical PA model, still gives about 70.8% envelope AM,
but the carrier-phase RMS rises from about **0.64°** to **9.17°**. The
separate RF phase-purity gate correctly rejects it. Envelope-only AM tests
would miss that failure. Neither number represents real K6 phase coupling.

## Reproduce

```bash
# Causal six-bit gain commands, SoapyFang IQ and coherent AM audio/phase metrics
python tools/k6_am_register_replay.py --write-us 117 \
  --ceiling 0x22 --json /tmp/flux-am-reg117.json

# Negative: slow updates severely distort the amplitude waveform
python tools/k6_am_register_replay.py --write-us 250 \
  --json /tmp/flux-am-reg250.json

# Negative: an assumed sluggish analog PA response
python tools/k6_am_register_replay.py --write-us 117 \
  --attack-ms 0.5 --release-ms 0.765 \
  --json /tmp/flux-am-slow-pa.json

# Negative: AM-PM phase coupling despite apparently clean envelope
python tools/k6_am_register_replay.py --write-us 117 \
  --am-pm-degrees 60 --json /tmp/flux-am-phase-distortion.json

python -m pytest -q tests/test_k6_am_register_replay.py
```

A negative experiment returns nonzero **but still writes JSON evidence**.
`simulated_am_shape_pass` and `simulated_am_spectral_pass` are separate
metrics; **neither** is a transmitter hardware qualification. Tests cover
K6Board decoded register words, per-write timing, low-power downward-only
code bounds, explicit gate-off, AM quality and negative analog/coupling
models. All inputs are synthetic; no RF is emitted.

## Next hardware and modem decisions

- [TX-29 #22](https://github.com/tcarwash/flux_radio/issues/22) must first
  validate *bounded real* driver gain and bias behavior, safe masked writes,
  APC independence, AM-to-PM coupling and key-up shutdown, under exclusive
  bench lock and dummy load with attenuated SDR. Do **not** activate any AM
  transmitter based on this model or copy its bare register values.
- [TX-32 #35](https://github.com/tcarwash/flux_radio/issues/35) can then
  determine whether a signed FM/phase path and a *measured* linear
  envelope could operate simultaneously for **polar SSB** or shaped QPSK.
  Simultaneous register control and PA linearity remain unknown.
- Better CW remains the nearest practical candidate, but requires a
  measured clean RF envelope and independent gate-off. The existing CW
  experiment documents simulated 117-/250-µs ramp shaping; this AM work
  probes the much harder continuous-envelope 1-kHz tracking case.
- Conventional RRC QPSK, USB/LSB and OFDM still require additional
  physical transmit-path proof. Host IQ/SoapyFang passing alone cannot
  establish stock K6 TX; **no production mode is enabled**.
