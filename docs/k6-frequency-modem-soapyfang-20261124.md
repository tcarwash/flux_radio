# K6 frequency-modem offline lab: FIFO FSK vs register-paced subaudio/4-FSK

**2026-11-24 · Simulation only. No hardware, radio TX, flash, installed mode, or KISS change.**

The preceding CW lab modeled **causal amplitude control** from timestamped
register requests into SoapyFang. This step isolates the **frequency/phase
trajectory** of three distinct transmit primitives, each with its own data
clock and physical evidence status, and runs a *separate pilot-calibrated
receiver* over SoapyFang's actual filtered IQ rather than pretending all
host-generated modulation can be emitted by the stock K6:

1. `native_fsk` — **observed hardware primitive**: continuous-phase native
   FD6818B FIFO 2FSK with a separate 4,800-baud symbol clock. Register writes
   configure the frame, but **do not** supply one symbol through SPI per bit.
   `REG59` setup is a schematic label, not a complete real radio TX driver.
2. `subaudio` — **observed binary polarity/gain components**, modeled here
   at independently selected 150–2,400 baud, with one REG51 update for each
   *changed* symbol (serialized, completion-time sampled), optional RF-side
   first-order LPF and fixed 2FSK deviation. Actual limit at 315-ish symbols/s
   was observed previously on a particular 134-Hz configuration; it is not
   a certified 300-baud production mode.
3. `signed_dac` — **unverified combined hardware claim**: proposed `REG51`
   DCS sign + 7-bit magnitude as a four-level bipolar frequency DAC. Symbols
   are assigned ±1/3 and ±1 of model deviation; a speculative REG51 sign/
   magnitude command is generated, but there is **no demonstrated** physical
   signed multilevel DAC on the board. Simulated 350-Hz bandwidth is an
   *unverified wider response*, not the chip's measured LPF.
4. `reg40_per_frame` — **unverified native-FSK deviation scaling**: a single
   modeled REG40 setup before the frame (never mid-frame). Tests whether a
   gain variation between finite frames remains decodable, **not** continuous
   multilevel or 4-FSK using REG40 register writes.

All paths synthesize a phase-continuous complex RF *model* by integrating
instantaneous frequency. This does **not** mean the actual PLL phase is
continuous across register writes. The hardware physics is precisely what
queued [TX-34](https://github.com/tcarwash/flux_radio/issues/37) will later
measure, after exclusive lease/approval and qualifying the dependent solo
paths (#20, #21, #26). No bench lease was requested or acquired for this lab.

## Real software signal path

```text
PUBLIC deterministic pilot + sync + held-out seeded payload
     -> native 4800-baud FIFO model OR register-paced REG51 symbol model
     -> optional modeled analog frequency-response lowpass
     -> continuous-phase FM IQ, no PA-gating inference
     -> SigMF cf32 file -> SoapyFang RX-only RFBackend/ChipModel FIR
     -> tap actual filtered IQ -> independent FM phase discriminator
     -> pilot-only timing/level estimation -> sync and payload BER/SER
```

At the receiver, the fixture has a known frame start but not known sample
phase: it searches a bounded range of sample delays **using only the public
pilot**, then estimates one mean Hz level per pilot label. The sync word and
held-out random payload are only scored *after* timing/level acquisition.
The decoder does **not** use the payload to train its timing, gain, deviation
or polarity. This is still **genie-aided start-of-frame acquisition**, not a
blind receiver, carrier-frequency-offset tracker, FEC or production packet
modem. A separate monotonic-level-order check prevents pilot calibration
from silently accepting reversed sign-to-frequency mappings. 4-FSK payload
BER is calculated over actual **two Gray bits per symbol**, not merely the
symbol-error rate; both are reported.

The physical source path is never invoked. The simulator uses 48 ksamples/s,
fixed model center of 440.850 MHz, deterministic AWGN, 50-ms virtual-clock
receiver reads (no skipped long IQ), and a 32/64-symbol public alternating
pilot, 16-symbol known sync plus 128-symbol held-out payload by default.
`source` contains the expected reference labels solely for **post-decode
scoring**. Actual module firmware, KISS and Sotto are unchanged.

## Results — modeled input SNR 30 dB (not measured RF)

| Path/model | Nominal symbol rate | Analog LPF scenario | BER and decoding |
| --- | ---: | ---: | --- |
| Native hardware-clock FIFO 2FSK | 4,800 baud | No additional modeled LPF | **PASS**, BER 0 |
| Native FIFO + REG40 scaling per *frame* | 4,800 baud | No additional modeled LPF | **PASS**, BER 0; physical effect unproven |
| Binary REG51 subaudio | 150 baud | 135 Hz | **PASS**, BER 0 |
| Binary REG51 subaudio | 300 baud | 135 Hz | **PASS**, BER 0 |
| Binary REG51 subaudio | 600 baud | 135 Hz | **FAIL**, model BER >0 |
| Signed-REG51 4FSK | 300 baud | 350 Hz, **hypothetical** | **PASS**, BER 0, only if physical bandwidth exists |
| Signed-REG51 4FSK | 300 baud | 135 Hz | **FAIL**, BER 0.3047; SER 0.5547 |
| Signed-REG51 4FSK | 1,200 baud | 350 Hz | **FAIL** even with hypothetical fast SPI |
| Signed-REG51 4FSK | 1,200 baud | 350 Hz, 1,000-µs writes | **FAIL**, missed >50 register updates |
| Signed-REG51 4FSK, **reversed sign order** | 300 baud | 350 Hz | **FAIL polarity validation** despite pilot-trained BER 0 |

These are not hardware rate benchmarks. They show which ***assumptions***
need validation and why native FIFO, subaudio, and signed multilevel must not
be collapsed into one synthetic "arbitrary IQ" mode.

## Reproduce

```bash
# Native hardware-FSK-clock software model (no physical FIFO/PA):
python tools/k6_frequency_modem_lab.py --path native_fsk \
  --json /tmp/flux-native-fsk-rx.json

# Slower binary subaudio with 135-Hz assumed analog LPF:
python tools/k6_frequency_modem_lab.py --path subaudio --baud 300 \
  --bandwidth-hz 135 --json /tmp/flux-subaudio-300.json

# Experimental four-level signed DAC, optimistic *unverified* LPF:
python tools/k6_frequency_modem_lab.py --path signed_dac --baud 300 \
  --bandwidth-hz 350 --json /tmp/flux-signed4-300.json

# Deliberately failed narrow-bandwidth case (still writes JSON):
python tools/k6_frequency_modem_lab.py --path signed_dac --baud 300 \
  --bandwidth-hz 135 --json /tmp/flux-signed4-narrow.json

# Deliberately failed serial write-budget case (not native FIFO):
python tools/k6_frequency_modem_lab.py --path signed_dac --baud 1200 \
  --spi-write-us 1000 --bandwidth-hz 350 \
  --json /tmp/flux-signed4-slow-spi.json

python -m pytest -q tests/test_k6_frequency_modem_lab.py
```

## Next engineering gates

- **Physical TX-34**: compare actual SDR IQ step response, phase continuity,
  register-update sideband/glitch energy and held-out BER. The issue is
  deferred behind solo path gates. Do not start physical RF on this document.
- For **Sotto**: keep native 4,800-baud packetized 2FSK as the credible
  physical route; improve frame/sync/FEC/interleaving independently from
  speculation about REG51 multilevel speed. Packetized half-duplex RX/TX
  and timing/size checks remain separate from this waveform test.
- For **QPSK/OFDM/SSB**: no proof of a linear arbitrary IQ or suitable direct
  phase DAC emerged. Current results cannot promote those as flashable modes.
- Future offline work can replace the pilot-start oracle with realistic
  burst acquisition and drift recovery, validate native FIFO payloads through
  the actual `FskRx` register model, and fit analog response **only** using
  real captured evidence rather than assumptions.
