# Flux native RF 2-FSK as a one-bit frequency DAC

**2026-11-22 follow-up:** [TX next experiments](k6-tx-next-experiments.md) is the
persistent roadmap for the current RF-frequency-DAC work **and all alternative
TX entry points** (native 4-FSK, MICIN, TCXO, tone oscillator, subaudio,
digital ports, PA envelope, external hardware). New host-only waveform
vectors and complex-IQ fidelity comparisons live in
`tools/k6_fsk_waveform_bench.py` and `tests/test_k6_fsk_waveform_bench.py`.
The next archive-analysis step is documented in [FSK-02/03](k6-fsk-02-archived-iq.md):
`tools/k6_fsk_iq_edges.py` and `tests/test_k6_fsk_iq_edges.py` measure
receiver filtering effects and nonconclusive transition/phase diagnostics.
The guarded native-FSK known-vector generator and Cortex-M0 emulator
transaction test are documented in [FSK-05](k6-fsk-05-known-vector-lab.md).
The earlier [RF pickup checkpoint](k6-fsk-05-live-rf-checkpoint.md)
validated a 400-ms FM carrier under the previous firmware, which refused
native-FSK module execution. A subsequent firmware image added the
FSK service: the latest [repeated physical 300-symbol/s 8-FSK PDM study]
(k6-fsk-pdm-repeats-and-phase-20261122.md) includes **four RF captures**
and 0/1,792 heldout binary-chip errors, 0/112 popcount-reconstructed
8-level symbol errors, and a heldout transition-aware phase-response model.
These are not proof of arbitrary coherent RF I/Q generation.

The subsequent [FSK-09 FIFO boundary experiments](k6-fsk-09-fifo-boundary.md)
measured **46.719 ms of extra framing/rearm modulation** between two
fixed 64-word frames while RF amplitude remained high. An initial
128-word FIFO-refill attempt safely aborted because the older-FD6818
almost-empty interrupt hypothesis **did not hold on the B chip**.
Therefore gapless streaming and phase-accurate arbitrary waveforms
remain open research milestones.


Experimental research branch: `research/native-fsk-pdm`. **No live transmit, serial
control, flash, PA operation or firmware changes.** This is a simulation
and bit-vector builder, not an approved over-the-air waveform.

## The slightly wild idea

The existing FD6818B native 2-FSK engine has transmitted arbitrary bit sequences
on real hardware. If it emits two approximate instantaneous RF frequencies
at +/- D Hz without resetting phase, its bitstream could serve as a one-bit
frequency DAC:

`desired signed RF offset -> duty-cycle + error feedback -> rapid 2-FSK bits -> RF phase integral`

At 75 output symbols/s and a 2400 native-FSK bit/s clock, each desired
frequency is approximated with 32 native bits. At 4800, there are 64.
The encoder **carries the error accumulator across symbol boundaries** to
minimize accumulated frequency/phase error. Eight example target offsets are
-1050,-750,-450,-150,+150,+450,+750,+1050 Hz. Deviation +/-4000 Hz is only an
illustrative model parameter, not a calibrated chip setting.

This is not the normal tone oscillator's *audio-over-FM* path: here the
hypothesis is that the RF frequency itself alternates and averages out after
appropriate receive filtering. The instantaneous RF spectrum is still wide.

### Simulation findings (16 known symbols, 75 symbols/s)

| Native binary clock | 1-bit choices per symbol | Avg-offset RMSE | LPF-center RMSE | Ideal-model 99% RF occupied bandwidth | Model energy outside +/-3 kHz |
|---|---:|---:|---:|---:|---:|
| 2400 bit/s | 32 | 96.8 Hz | 95.9 Hz | 14.5 kHz | 84.0% |
| 4800 bit/s | 64 | 46.4 Hz | 22.9 Hz | 15.3 kHz | 50.0% |

All figures are calculated from an **ideal instantaneous, phase-continuous FSK
oscillator** and the same seeded frequency sequence. The low-pass-center
figure comes from a 150 Hz fourth-order model detector; occupied bandwidth
from a Welch PSD. They are not hardware results, spectral mask compliance,
measured emissions or receiver BER. The simulation deliberately does not
pretend that high-rate PDM creates an actual narrowband 8-FSK signal.
The *wide RF sidebands* are the central concern; do not radiate this waveform
until properly characterized through a conducted, isolated setup.

### Grounded observations from previous real FD6818B IQ captures

Existing SDR recordings at
`/storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix/`
and the offline-assisted decode in `decoded.json` establish that the native
FSK engine can carry arbitrary data at several clocks:

- 1200 bit/s, 128 bytes: 0/1024 assisted bit errors.
- 2400 bit/s, 128 bytes: 3/1024 assisted bit errors.
- 4800 bit/s, 64 bytes: 30/512 assisted bit errors.
- 6000 bit/s, 16 bytes: weak correlation, 40/128 assisted bit errors;
  not evidence of reliable operation at 6000.

A separate offline examination of the SDR IQ (RF discriminator at bit centers)
gave frequency-mark separation of approximately 8.08 kHz at 1200,
7.53 kHz at 2400 and 6.98 kHz at 4800. These are approximate
receiver-assisted measurements, not calibrated chip deviation. The reduction
and increased BER at higher rate imply that **native transition settling,
filtering and timing must be measured**; the ideal DAC may not work.

## Reproduction

```sh
python3 tools/k6_native_fsk_pdm.py --n-symbols 16 --native-baud 2400 \
  --out-dir /tmp/flux-native-pdm-2400
python3 tools/k6_native_fsk_pdm.py --n-symbols 16 --native-baud 4800 \
  --out-dir /tmp/flux-native-pdm-4800
python3 -m pytest tests/test_k6_native_fsk_pdm.py -q
```

Each run writes `native-fsk-pdm.bin` as raw 0/1 payload bytes (not firmware
or a module) and a reproducibility JSON. Pack bits are MSB-first per byte,
with the existing laboratory convention of the low byte first within
16-bit native FIFO words. Default maximum 64 words mirrors the currently
bounded laboratory frame. The FSK writer for arbitrary custom FIFO bytes
is **not implemented here**; current lab sends seeded pseudo-random words.
Do not feed this raw bit file to a firmware/module uploader.

## Most valuable physical follow-ups

1. **No transmission required:** reuse existing SDR captures to estimate the
   transient RF frequency response around 0-to-1 and 1-to-0 native FSK
   edges, phase continuity, symbol-clock jitter and effective bandwidth.
2. **Conducted, bounded single-burst trial only after reliable independent TX
   timeout verification:** stream an explicit 64-word test pattern at native
   2400 and capture its RF spectrum with suitable load, coupler and attenuation.
   Compare chip behavior with the ideal model. No keying from this branch.
3. Compare adjacent-channel emissions against a conventional 75-baud
   eight-level *direct RF* reference. Check whether RF filtering converts
   the frequency PDM into a usable, sufficiently clean continuous phase
   waveform; if not, retain the scheme as a curiosity.
4. Characterize any *native multi-level FSK or bypass TX baseband* mode
   before using high-rate PDM for a production modem. A real 4-FSK
   internal modulator could be substantially cleaner and more efficient.

## Other routes to a software-defined $30 radio

- **Native chip modulator exploitation:** audit B-chip hardware FSK/DMR
  controls for multi-level modulation, transparent bitstream mode, deviation
  scaling and the ability to refill FIFO without gaps. This is the most
  practical first route to direct RF waveforms, not the PDM trick.
- **Tone oscillator / instantaneous-frequency control:** frequency-word
  changes already offer audio-over-FM. Measure whether changing oscillator
  gain, phase or very-low-frequency settings supplies a usable signed RF
  frequency trajectory. Do not conflate audio-over-FM with direct modulation.
- **Software analog bridge:** generate PCM using the MCU's PA11 DMA/PWM,
  filter/level-shift it and feed it into the actual RF MICIN analog input
  through a reversible, properly isolated board modification. This could
  enable arbitrary FM audio modulation; it is not arbitrary RF I/Q or SSB.
- **Hidden serial digital baseband:** reverse-engineer B-chip MCBSP/I2S pins
  and the DMR transmit bypass. Verify which *physical* pads are available
  before rerouting GPIOs; do not reuse original 0x4819 register mappings.
- **Polar/IQ hardware hack:** attempt controlled analog phase/frequency plus
  independent PA envelope modulation, or an optional filtered daughterboard
  waveform source. Possible research direction, but needs PA linearity,
  isolation and emissions characterization; otherwise uncontrolled spurs.
- **RX side:** combine GP0 IF/IQ and PA11 demodulated audio under one
  modem sampling API, preserving independent paths for experiments.

Aim for a menu of characterized transmit backends, not a single claimed
general-purpose SDR mode. The independent TX timeout, RF filtering and power
measurements are release gates, not reasons to stop the bench research.

## External architecture lead: bypass the voice path (2026-11-21)

The original FD6818 programming-guide discussion of DMR/dPMR describes a
**MICIN shaped 4FSK input** with transmit DC-cancellation bypass, and an
alternative **TCXO control / two-point modulation** arrangement. The
RadioFoundry Retevis RT10 hardware notes document a working FD6818 +
separate HR_C7000 modem in which the modem feeds TX modulation via the TCXO
MOD1 pin, and describe its bypass RX/TX setup. See:

- https://www.scribd.com/document/824197904/Document (FD6818 guide text)
- https://radiofoundry.org/docs/spec/hardware-rt10 (observed hardware)
- https://radiofoundry.org/docs/spec/hardware-db25d (two-point modulation notes)

**This does NOT establish the mapping for our FD6818B 0x4829, or that the
K6 board provides an accessible TCXO modulation input.** It changes the
research ranking: a reversible, AC-/bias-safe test using filtered MCU PWM
(or an external audio DAC) into the real MICIN analog chain, with receiver
SDR measurement, may be a better route to directly shaped continuous-phase
4FSK than one-bit high-rate native-FSK dithering. Test subaudio/DC response,
pre-emphasis disable, deviation, symbol shape and RF spectral purity. If the
B chip has a similarly useful DC-coupled bypass, arbitrary instantaneous
frequency trajectories become practical; if it removes DC, only audio-band
frequency trajectories work and ultra-narrow FT8/WSPR remain unavailable.
Do not write original-chip 0x4819/FD6818 register sequences to the B chip.
