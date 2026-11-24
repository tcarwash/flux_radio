# K6 B-chip candidate: constant-envelope DQPSK using hypothetical signed FM

**2026-11-24 · OFFLINE ONLY · independent CW/AM/QPSK modulation research, not Sotto.**

This models a potential way of making four RF carrier phase states **without a
linear I/Q transmitter**, using the time integral of a signed instantaneous
frequency controller. A Gray differential phase increment is applied per
symbol: `00→0°`, `01→+90°`, `10→−90°`, `11→180°`. The waveform is continuous
phase and constant envelope, unlike conventional pulse-shaped/RRC QPSK.
It is not the same RF modulation as QPSK with a linear RF DAC, and it does
not inherit conventional RRC QPSK occupied-bandwidth or EVM results.

**The enabling hardware is unproven.** `REG51` sign+magnitude and subaudio
bandwidth remain an unqualified combination on the FD6818B B-chip. It is also
not the `REG52` hardware CTCSS phase shifter: that register modifies the
*low-frequency subaudio tone*, not the RF carrier's phase directly. A
successful SoapyFang RX-only simulation cannot establish a QPSK transmit mode,
a working antenna waveform, a safe register recipe or a KISS provider.

## What is now implemented

`sim/modulation/dqpsk_polar.py` maps public pilot (64 phase increments),
public sync (16 increments), and 128 seeded held-out two-bit symbols into
*schematic* `REG51` update intentions. Every changing symbol's frequency
request is quantized to either 7-bit or 4-bit signed deviation and becomes
effective **only when its one 3-wire register transaction completes**. Overrun
requests are discarded, rather than queued or silently processed late. An
optional 1-pole subaudio low-pass (135 Hz default, **hypothetical** for this
signed multilevel route) determines the modeled actual instantaneous
frequency. Its integral yields continuous-phase, unit-amplitude synthetic IQ.
No physical PA gate or register write exists in the model.

`tools/k6_dqpsk_polar_lab.py` injects this IQ through **SoapyFang's real
simulation receive filtering**, with deterministic AWGN and a finite carrier
frequency offset. It measures successive symbol-boundary phase differences.
The receiver uses **public pilot symbols only** to choose a short delay and
estimate four phase-difference centroids; the sync and held-out data are
not used for training. It requires positive physical frequency sign order
as a separate qualification: otherwise a reversed DAC sign might yield
zero BER after pilot-relabeling, a dangerous false positive.

This is a deliberately simple **known-frame-start and known-baud** decoder,
not a deployable clock-recovery, burst-acquisition, or carrier-tracking
modem. The modeled register codes are *not safe hardware instructions*;
actual `REG51` fields, mux, DCS gating, RF PLL, timing and restoration must be
qualified independently. The virtual `K6Board` three-wire bus test confirms
only the encoded intentions' deterministic timing, without touching the radio.

## Deterministic SoapyFang receive results (30-dB model SNR)

| Symbol rate | 135-Hz first-order analog response hypothesis | Payload results |
| ---: | --- | --- |
| 25 baud | 7-bit signed deviation, ±1,500-Hz full scale | **Fail**, 13.67% BER; fine phase increments merge after quantization |
| 50 baud | Same | **Pass**, 0/256 payload bit errors |
| 75 baud | Same | **Pass**, 0/256 |
| 100 baud | Same | **Pass**, 0/256 |
| 150 baud | Same | **Pass**, 0/256 |
| 200 baud | Same | **Fail**, 3.125% BER |
| 300 baud | Same | **Fail**, 7.422% BER |
| 400 baud | Same | **Fail**, 26.56% BER |
| 600 baud | Same | **Fail**, 30.08% BER |
| 1,200 baud | Same | **Fail**, 33.20% BER |

For 300 baud, an **ideal unlimited analog frequency response** or a separate
**hypothetical 350-Hz** subaudio response passes with zero errors; an
assumed **50-Hz** response fails badly (42.97% BER). This is precisely why
the `REG51` instantaneous-frequency bandwidth is the key physical
qualification, rather than arithmetic ability to synthesize IQ in software.

At 100 baud, **5–250 µs** schematic register-write periods all pass; 1,000 µs
has measurable errors, and 5,000 µs/15,000 µs fail outright. With a
15,000-µs interval, many requests are lost to the one-write-at-a-time rule.
Constant offsets from **−80 to +80 Hz** decode at 50–150 baud in the tested
fixture using public-pilot phase calibration. This is not a fading,
variable-clock, noise-floor, acquisition or burst-error specification.

The **reversed-sign** control is especially instructive: the pilot-only
receiver can still get **zero payload BER** by learning swapped phase
centroids, but the independent signed-frequency-order check rejects it.
Disabling all signed frequency updates likewise fails. A **4-bit** DAC
model at 150 baud and 7-bit at 25 baud both fail due quantization and phase
constellation collapse. The nominal 100-baud case provides **200 raw bit/s**
*before* sync, CRC or FEC: even if it ultimately exists, this is a slow,
experimental mode and is nowhere near the useful throughput of proven
native-FSK FIFO. It is not a Sotto replacement.

## Reproduce

```sh
# Conservative test of the *modeled* subaudio bandwidth
python tools/k6_dqpsk_polar_lab.py --baud 100 --bandwidth-hz 135 \
  --write-us 117 --json /tmp/flux-dqpsk-100.json

# Loss of phase fidelity as baud increases
python tools/k6_dqpsk_polar_lab.py --baud 300 --bandwidth-hz 135 \
  --json /tmp/flux-dqpsk-300.json

# Unrealistically optimistic analog response: a diagnostic, NOT hardware proof
python tools/k6_dqpsk_polar_lab.py --baud 300 --bandwidth-hz 350 \
  --json /tmp/flux-dqpsk-wide.json

# Negative: signed-frequency order reversed, zero BER alone is insufficient
python tools/k6_dqpsk_polar_lab.py --baud 100 --reverse-sign \
  --json /tmp/flux-dqpsk-sign-fail.json

# Negative: missed SPI-update budget
python tools/k6_dqpsk_polar_lab.py --baud 100 --write-us 15000 \
  --json /tmp/flux-dqpsk-slow-bus.json

python -m pytest -q tests/test_k6_dqpsk_polar_lab.py
```

Failing cases return nonzero but preserve JSON diagnostic evidence. The
reported `simulated_decode_pass` requires zero pilot-aided payload bit errors,
valid independent public sync, correct signed phase order, a sufficiently
separated constellation and no dropped register writes. It is **not** a
physical modulation-capability or legal emissions certificate.

## What to test next and where

1. [TX-27 #20](https://github.com/tcarwash/flux_radio/issues/20) — first
   qualify the B-chip REG51 signed frequency control, safe register masking,
   polarity, quantization, bandwidth, and phase step response under exclusive
   hardware lease. None of that is established here.
2. [TX-31 #25](https://github.com/tcarwash/flux_radio/issues/25) — separately
   determine if REG52 can shift CTCSS phase on the B-chip. **Do not confuse**
   a 120° subaudio oscillator shift with directly controlling RF carrier
   phase. The results should remain separate.
3. [TX-32 #35](https://github.com/tcarwash/flux_radio/issues/35) — only if
   signed FM is physically demonstrated, examine simultaneous safe envelope
   control for genuine QPSK, SSB and other polar RF methods. OFDM remains
   host-only until actual broadband linearity and PAPR budget are measured.
4. For useful data rates on existing stock hardware, native-FSK is still
   the credible proven TX primitive; this new lab is a **capability probe**,
   not authorization to start a new firmware mode or interfere with Sotto.
