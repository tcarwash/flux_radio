# SoapyFang RX model × five modulation references — offline validation

2026-11-24 · no radio connected, no RF transmission, no modifications to the
radio or its firmware. This is **an offline, receive-only test** of the actual
Flux SoapyFang port and the host-side modulation reference generators added in
`sim/modulation/`. The new tool is `tools/k6_soapyfang_modlab.py`.

## What really executes

For each mode (CW, true AM, USB, LSB, Gray QPSK/RRC, QPSK/OFDM), the host
waveform code generates deterministic complex IQ at 48 ksamples/s. Each signal
is serialized as cf32 SigMF IQ and **replayed through the actual**
`SigmfSource` → `RFBackend` → `ChipModel` SoapyFang receiver signal path. The
model runs its RF-channel FIR front end, FM discriminator and sample/RSSI/noise
register estimation, with a tapped observation of the *filtered complex IQ*
that normally enters the discriminator. Only the host-side reference decoder
consumes those observed IQ samples. The tap is installed for the test on the
`chan.process` instance, and **does not alter production SoapyFang code**.

The experiment is fully virtual; the numeric tuned frequency (440.850 MHz) is
a recording center tag and simulated receiver frequency, **not a command to a
transmitter or SDR**. There is no import of a hardware SoapySDR device and
no serial access, radio flash, bench lock or PTT. SoapyFang currently implements
FM/FSK reception behavior; **it does not natively decode real RF AM, SSB,
QPSK or OFDM**. Neither successful host-side demodulation nor simulator
register reads establish a physical transmit path on the K6.

Because the 1.8-second CW recording is longer than SoapyFang's virtual-time
jump threshold, the runner pulls the receiver in 50 ms steps instead of
skipping to the last 50 ms. This preserves *every* sample and avoids a false
zero-energy RSSI/audio pass. The harness checks exact IQ sample counts and
checks the receiver emitted FM PCM samples (not that the PCM decodes these
non-FM modes). Peak RSSI is an **uncalibrated model number**, not measured dBm.

### Receiver/decoder boundaries

| Check | Executed in SoapyFang | Executed by host reference decoder |
| --- | --- | --- |
| IQ source loading, replay clock, RF center, tuned register, RSSI | Yes | No |
| Channel filtering and FM discriminator/PCM production | Yes | No |
| CW keyed envelope, shape/timing comparison | IQ conditioning | Host envelope threshold/correlation |
| True AM modulation depth, envelope correlation | IQ conditioning | Host amplitude demodulation |
| SSB USB/LSB image rejection and audio correlation | IQ conditioning | Host Hilbert/complex receiver reference |
| QPSK Gray matched filter, bit decisions, EVM | IQ conditioning | Host **genie-timed/gain-aligned** matched filter |
| OFDM CP/FFT symbols, BER, EVM | IQ conditioning | Host **genie-timed/gain-aligned** FFT/decoder |

The host compares the received complex channel output to the known test
waveform to estimate up to 22 samples of FIR delay and one complex scalar
(receiver phase/gain). This is an *optimistic acquisition bound*, not an
independent carrier/timing/frequency acquisition implementation. Real-world
CFO, phase noise, PA nonlinearity, adjacent-channel interference, AGC and drift
are not calibrated here.

## Repeatable runs

Run from a checkout with NumPy, pytest and the SoapyFang port installed:

```sh
python tools/k6_soapyfang_modlab.py --snr-db 30 \
  --json /tmp/flux-soapyfang-modlab-30.json

# Hypothetical envelope-less, phase-only transmitter — NOT K6 firmware:
python tools/k6_soapyfang_modlab.py --snr-db 60 --tx-model phase_only \
  --json /tmp/flux-soapyfang-modlab-phaseonly.json

# Exercise noise sensitivity without treating a failed frame as a runner bug:
python tools/k6_soapyfang_modlab.py --snr-db 12 \
  --json /tmp/flux-soapyfang-modlab-snr12.json

python -m pytest -q tests/test_k6_soapyfang_modlab.py
```

The CLI exits nonzero when a pass gate fails and **still writes the JSON
report**, preserving negative results. `--modes cw,qpsk,ofdm` narrows the
set. Default has six cases because USB and LSB share the SSB family.

### Measured modeled results

After replaying the same deterministic waveforms at *30 dB input IQ SNR*:

| Mode | Actual SoapyFang filtered-IQ + host reference | Representative check |
| --- | --- | --- |
| CW, 20 WPM “CQ” | PASS | Correct key-state classification, 5 ms ramp |
| AM, 70% modulation, 1 kHz tone | PASS | 0.720 observed depth vs 0.700 source; clean tone correlation |
| SSB USB / LSB, 800/1400 Hz | PASS / PASS | ~46.7 / ~45.7 dB opposite-sideband rejection |
| Gray QPSK, 1.5 kbaud, RRC | PASS | 0 bit errors; ~0.66% EVM |
| OFDM, 64 FFT, 16 data carriers, CP 16 | PASS | 0 bit errors; ~9.8% EVM (FIR response on outer carriers) |

At 60 dB SNR, an idealized **phase-only amplitude-limiter** diagnostic
(eliminating envelope variation but preserving arbitrary instantaneous phase)
produced:

| Mode | Result | Key impairment |
| --- | --- | --- |
| CW | FAIL | Keying ramp no longer approximates the shaped target |
| AM | FAIL | 70% AM becomes ~0.12% envelope depth |
| USB/LSB | FAIL / FAIL | Opposite sideband suppression drops to ~20 dB |
| QPSK | PASS in this **genie-aided** reference | 0 bit errors but ~9.1% EVM (vs ~0.35% ideal) |
| OFDM | FAIL | 0.156% BER, ~36.8% EVM (vs ~9.7% ideal) |

**Do not interpret the phase-only diagnostic as an actual K6 transmitter**:
that RF chip has *not* been shown capable of applying arbitrary continuous
signed phase offsets, and PA timing remains unqualified. A mathematical
phase-only waveform being decodable does not mean an OQPSK/CPM modem works
in radio firmware. Conversely AM/SSB/OFDM model failures do not prove an
external linear transmitter could not work.

## Acceptance / limitations

- Main automated test: `tests/test_k6_soapyfang_modlab.py` replays all six
  cases at 30 dB and checks receiver sampling and each reference decoder.
- Diagnostic tests assert phase-only *fails* the four amplitude-sensitive
  modes (CW shape, AM, SSB, OFDM) and QPSK's EVM gets materially worse;
  another test verifies receiver EVM deteriorates with added noise.
- All tested modes remain **host-only**; no on-device module, KISS provider,
  production modem or new TX capability has been enabled.
- Next: model `REG_36`/APC envelope, native FSK/tone register traces, clipping,
  input frequency-offset and drift in a separate emulator observer. Physical
  70 cm conducted SDR measurements remain the release gate for clean CW or
  any phase-only experiment. See `docs/k6-modulation-workplan-20261124.md`.
