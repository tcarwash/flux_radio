# Flux K6 — TX waveform research: next experiments

Updated 2026-11-22. Research branch: **research/native-fsk-pdm** at
/storage5/dev/flux-native-fsk-pdm. This is the persistent **all-path TX
research queue**. Preserve experimental alternatives even if a particular
native-FSK approach fails.

**Scope and safety:** Everything implemented in this pass is offline,
host-side simulation. No RF transmission, PTT, firmware flash, live
register writes, radio control, or new hardware configuration. Hardware
experiments below are proposals and require an attended conducted RF fixture,
verified independent TX timeout and explicit operator approval.

## What has been established

- The FD6818B chip on the tested K6 identifies as **0x4829**. The existing
  hardware native-FSK test transmitted a binary waveform successfully.
  Known-payload-assisted recovery of archived SDR IQ gave 0/1024 errors at
  1200 bits/s, 3/1024 at 2400, and 30/512 at 4800 for particular captures.
  At 2400 the three errors were in bits 0,1,3; at 4800 all 30 were in the
  first 168 bits. These are **not independent blind BER measurements**;
  preamble/acquisition rather than sustained data failure is a hypothesis.
- Flux's B driver uses REG_3F for modem mode, REG_59 for FSK TX enable,
  REG_5F for the FIFO, REG_72 for FSK timing and REG_70 for tone/FSK gain.
  REG_40 is also used for FM deviation. **Which settings alter actual
  native-FSK deviation on this specific B silicon remains unverified.**
- The original non-B chip's REG_3B/REG_38 tuning writes were observed to
  latch until RXON/TXON. This rules them out as fast phase-continuous RF
  modulation on that tested radio; the B chip must not be assumed identical.
- A live RX-only B-chip check verified that REG_2B TX filter-disable bits
  can be written and restored, not whether they provide flat TX modulation.
  A lab-only MICIN-on API level 26 compiled but was not flashed.
- The FD6818 programming guide's suggested I2S register layout conflicts
  with the tested 0x4819 and 0x4829 maps. Its I2S/MCBSP and DMR examples
  remain architectural leads, not a safe B-chip register recipe.

Sources within this repository: [native FSK research](k6-native-fsk-pdm.md),
[MICIN lab](k6-micin-bypass-lab.md), [digital TX backends](k6-digital-tx-backends.md),
[original FSK research](k6-own-fsk.md), [RF lab](k6-rf-lab.md),
[FD6818 audio](k6-fd6818-audio.md). Original known-payload IQ files:
**/storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix/**.

## Primary track: binary native 2-FSK as an RF frequency DAC

Given two instantaneous RF offsets -D and +D at the native bit rate,
a carried first-order error accumulator selects binary states whose
time-average tracks a desired frequency trajectory:

    average offset ~= D * (2 * fraction_of_high_bits - 1)

This is a **constant-envelope, phase-trajectory** experiment, not an
arbitrary RF I/Q or amplitude DAC. Average frequency alone is inadequate:
complex-IQ phase fidelity, spectral occupancy and the actual silicon's
switching/PLL response determine whether the waveform is useful.

Earlier ideal simulation with D=4000 Hz badly polluted the spectrum
for eight tones from -1050 to +1050 Hz. Reducing modeled D to
1100–1200 Hz improved the spectrum, and an ideal phase-coherent RF comparison
was much more favorable at 4800 native bit/s than at 2400 bit/s.
**This is a model, not a calibrated 0x4829 capability.**

**Reproducible 32-symbol eight-level sample (seed 17, 75 symbols/s,
1.6-kHz complex-IQ comparison filter):**

| Native bits/s | Assumed deviation | Complex coherence | Normalized EVM | Ideal 99% occupied BW |
|---:|---:|---:|---:|---:|
| 2400 | +/-1200 Hz | 0.275 | 3.50 | 5.10 kHz |
| 4800 | +/-1200 Hz | 0.844 | 0.64 | 5.69 kHz |
| 4800 | +/-4000 Hz | 0.058 | 17.19 | 15.32 kHz |

These are sequence/filter-specific ideal-model results, not RF power,
interoperability, or actual K6 deviation measurements. A 99% occupied
bandwidth number is not a regulatory spectral-mask pass.

### Offline implementation on this branch

- **tools/k6_native_fsk_pdm.py:** original per-symbol encoder preserved;
  new pdm_encode_frequency_samples accepts *one desired frequency per native
  FSK bit*, with its phase-error accumulator maintained continuously.
- **tools/k6_fsk_waveform_bench.py:** seeded four- and eight-level FSK,
  Gaussian-smoothed eight-level trajectories, chirps, an MSK target at
  600 symbols/s, and optional phase pulses. It compares complex RF IQ
  after the *same* reference/approximation filter: coherent correlation,
  normalized EVM, coherent/in-band power, wrapped phase error, 99% occupied
  bandwidth and power outside +/-3 kHz. It emits JSON + NPZ research
  vectors; it writes **no radio-loadable packet or device command**.
- **tests/test_k6_fsk_waveform_bench.py:** deterministic vectors,
  bounds/phase-quantizer checks, ideal waveform fidelity regression,
  frame-capacity flag and no-hardware CLI smoke test.

Run reproducible host-only tests:

~~~sh
cd /storage5/dev/flux-native-fsk-pdm
python3 -m pytest -q tests/test_k6_native_fsk_pdm.py tests/test_k6_fsk_waveform_bench.py
python3 tools/k6_fsk_waveform_bench.py \
  --out-dir /tmp/flux-fsk-pdm-waveform-study \
  --modes 4fsk,8fsk,gaussian-8fsk,chirp,msk600 \
  --native-bauds 1200,2400,4800 \
  --deviations-hz 1100,1200,1400,4000 \
  --symbol-baud 75 --symbols 32
~~~

The report marks any vector that would exceed the existing experimental
64-word FIFO frame. An actual streaming/refill API has **not** been built.
The phase-pulse experiment is **not** verified BPSK; MSK targeting is also
not proof of interoperable transmitted MSK.

### Native 2-FSK experiments, ordered

| ID | Experiment | Evidence required | State |
|---|---|---|---|
| FSK-01 | Sweep modeled deviations, native clocks and desired waveforms; compare complex-IQ reference, not just mean frequency | Seeded vectors, coherence/EVM, occupancy and bandwidth | Implemented offline |
| FSK-02 | Reanalyze archived SDR IQ for phase continuity, edge settling, frequency separation, clock drift and startup errors | [Offline FSK-02 report](k6-fsk-02-archived-iq.md): receiver I/Q filtering improves 4800 baud from 28 to 6 assisted-timing errors; phase reset unproven | Initial archive analysis complete; physical validation remains |
| FSK-03 | Compare BK4829/FD6818B register sources and live B driver for gain/deviation: REG_70, REG_40, REG_59, REG_72 | Source triage in [FSK-02/03 findings](k6-fsk-02-archived-iq.md): REG_40 controls nominal FM deviation, REG_70 is tone/FSK gain; B physical effects unproven | Partial source review; further B bench gated |
| FSK-04 | Test long 000/111 runs and 0101 transitions, measuring instantaneous RF deviation and cumulative carrier phase | Calibrated conducted IQ and no phase reset across transitions | Hardware gated |
| FSK-05 | Design a guarded arbitrary FIFO-word lab API, with explicit bit/word order and startup preamble, preserving independent watchdog | [Known-vector lab](k6-fsk-05-known-vector-lab.md): 64-word payload, generated truth manifest, inert-by-default C module, 20 passing Unicorn tests and RX-only chip fingerprint | **Implemented and emulator-tested; [RX-only preflight and held-out decode](k6-fsk-05-rx-preflight.md) complete; physical TX still gated** |
| FSK-06 | Retry 2400/4800 with sacrificial preamble; distinguish physical settling from assisted-decoder acquisition | Blind sync/CRC where possible, repeated recordings and confidence bounds | Hardware gated |
| FSK-07 | Run one bounded real PDM 4/8-FSK sequence with measured minimal D | RF complex-IQ EVM/coherence, OBW, spurs, BER, cleanup | Conditional |
| FSK-08 | Try MSK/GMSK, shaped FSK, chirps and phase pulses; reject if fidelity/adjacent RF energy fails | Receiver decode and emission measurements, not only model | Conditional |
| FSK-09 | Investigate transparent FSK mode, FIFO refill/chaining and preambleless operation | Continuous phase and clock across FIFO boundaries, no silent gaps | Research |

**Decision:** A narrower ideal RF spectrum does not imply correct RF
phase. If silicon deviation cannot be reduced or phase continuity/
framing cannot be maintained, do not promote PDM to a deployable backend.

## Do not lose these additional TX entry points

These are **independent hypotheses**, not variations of one implementation.

| ID | Entry point | Why it matters | Next discriminating check |
|---|---|---|---|
| TX-10 | Native **ordinary direct 2-FSK** | Already functional baseline for packet modes, no frequency dithering needed | Characterize rate/deviation, frame overhead, repeatability, power |
| TX-11 | Native **4-FSK/DMR/dPMR/transparent modem** in B/BK4829 | Potentially much cleaner multilevel RF; possible M17-style modulation | Compare B drivers and stock firmware disassembly; prove actual four levels, not just a DMR marketing term |
| TX-12 | **MICIN baseband** with TX pre-emphasis, 300-Hz HPF, LPF bypass | Shaped real FM/FSK input potentially cleaner than PDM | Validate API-26 MICIN-enabled TX, REG_2B, actual mic levels/coupling/DC response |
| TX-13 | **Filtered MCU PWM or external DAC physically connected to MICIN** | Software-generated analog frequency-control waveform; alternate to chip modem | Map actual K6 mic jack and bias; safe reversible adapter. **PA11 currently drives speaker path, not MICIN** |
| TX-14 | **TCXO/reference modulation and two-point FM modulation** | Could enable slow direct-offset trajectories beyond AC-coupled MICIN | Identify K6 board oscillator and accessible modulation pad; bench-only reversible electrical validation |
| TX-15 | **Tone oscillator REG_70/71, dual-tone/AFSK** | Existing Bell 202/APRS and possible 600-symbol/s eight-tone over-FM | Measure tone step latency and continuity, distortion and spectral behavior. **Audio over FM, not a pure narrow RF tone** |
| TX-16 | **CTCSS/DCS/subaudio modulation source** | Potential slow signed FM injection or separate programmable path | Trace summing point, control clock, gain, deviation and achievable polarity |
| TX-17 | Undocumented **direct FM offset / synthesizer DAC** | Best possible per-symbol frequency-control register if it exists | Read-only source/register audit; no working candidate established |
| TX-18 | **I2S/MCBSP or digital baseband / GPIO** | Maybe RX PCM or TX data stream without analog mic | Old FD6818 register addresses mismatch B; only GP0/PB1 wired to MCU in known board; confirm map and direction |
| TX-19 | **REG_3B/38/39 static LO/xtal tuning** | Carrier trim and maybe very slow hops | Proven latched on original non-B; determine B behavior before considering anything beyond static calibration |
| TX-20 | **PA/APC envelope and true CW gating** | Clean CW/QRSS, amplitude shaping and polar-modulation exploration | Existing click/tail issues, off-state carrier leakage, nonlinear PA; measure ramp and true RF off gate |
| TX-21 | **Polar TX / phase-pulse PSK plus independent RF amplitude** | Might access QPSK/BPSK-like trajectories | Phase pulses alone are NOT conventional BPSK; true envelope control must be independently verified |
| TX-22 | **First-party pluggable TX backend contract** | One RF-mode API selecting native FSK, MICIN, oscillator, or external modem | Explicit frequency/reference, sample/symbol clocks, spectral budget, cleanup, watchdog and capability gates |
| TX-23 | **External DAC/daughterboard/transverter/SDR gateway** | Honest fallback for real I/Q, conventional SSB/QAM, narrow FT8/WSPR, or high-fidelity phase modulation | Board access, RF filtering and independent conducted validation |

Possible protocol families for whichever physical entry point succeeds:
binary CPFSK, MSK/GMSK, 4/8/16-FSK, M17-like 4FSK,
narrow weak-signal FT8/FT4/WSPR/JS8 **only if** oscillator precision,
deviation and stability meet their requirements, chirps, and experimentally
phase-keyed waveforms. **Constant-envelope binary FSK cannot directly
produce arbitrary amplitude-varying SSB/QAM/OFDM.**

### External implementation and hardware references

- BK4819/UV-K5 family: https://github.com/egzumer/uv-k5-firmware-custom
- FD6818B/UV-5RH reverse engineering:
  https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE/issues/18
- Another B driver: https://github.com/sophiel-meow/bf-5rh-firmware-open
- BK4829 driver reference: https://github.com/armel/uv-k1-k5v3-firmware-custom
- Original FD6818 guide (not a B-chip register map):
  https://www.glaver.org/blog/UV-17/FD6818.pdf
- MICIN flat-mod precedent on **non-B** chip:
  https://github.com/mobilinkd/uv-k6-digital-mod
- TCXO/two-point precedent:
  https://radiofoundry.org/docs/spec/hardware-db25d

## Shared bench gates for all future physical TX trials

1. Review source diff and firmware hashes; verify a usable radio-recovery
   path before experimental flashing. Do **not** merge an uncontrolled TX mode.
2. Require explicit operator consent, legal amateur test frequency,
   confirmed 0x4829 identity, correct TX lock and low-power configuration,
   independent hardware/firmware timeout, and an external abort.
3. Operate **conducted only** into a rated 50-ohm dummy load, suitable
   coupler and verified attenuation to the SDR. **PA-off is not RF-off**.
4. Preserve and restore register/calibration baseline on every path,
   including aborts; never copy original non-B values to the B chip.
5. Record exact SDR IQ sample-rate, frequency calibration, waveform seed,
   bit and phase timing, input power, RF deviation, spurious/adjacent
   emissions, harmonics, firmware commit and post-test RX restoration.
6. Start with short bounded *known* vectors. Assisted-data correlation
   is diagnostic, not unbiased bit-error or interoperability proof.
7. Only promote a backend after repeatable fidelity, decoder success,
   emission and independent watchdog evidence.

## Milestones

- **M0:** offline frequency-per-chip PDM and complex waveform bench,
  tests and preserved TX roadmap (current).
- **M1:** archived IQ phase/settling and long-run analysis, source-only
  B-chip register/deviation audit. Initial archival results and filter comparison
  now documented in [FSK-02](k6-fsk-02-archived-iq.md); native-B phase
  continuity, exact deviation and true hardware startup remain open.
- **M2:** guarded arbitrary-FSK FIFO API in emulator, with tight bounds
  and independent timeout proof.
- **M3:** attended conducted short-pattern test **only after** all safety
  gates; capture calibrated RF I/Q and compare with modeled target.
- **M4:** build a reusable TX backend only for paths that meet the
  measured RF and stability requirements; continue investigating
  MICIN, TCXO and multilevel paths regardless.

Keep IDs stable; record each future experiment's branch, evidence path,
hash, revision, result and stop/go decision without overwriting earlier
failed or superseded experiments.
