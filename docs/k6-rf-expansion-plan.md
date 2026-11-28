# Flux RF expansion and weak-signal laboratory roadmap

Follow-up implementation plan for clean CW, AM, SSB, QPSK and OFDM:
[`k6-modulation-workplan-20261124.md`](k6-modulation-workplan-20261124.md).

2026-11-18 — planning only. Do not change base firmware, band locks, module
ABI, feature packs, or existing modules while the current refactor is underway.
This roadmap complements k6-rf-lab.md and is a research design, not permission
to transmit outside validated bands. All speculative TX starts conducted,
shielded, and with suitable loads/measurement paths.

## 1. Evidence and open questions

| Claim | Evidence | Status |
|---|---|---|
| FD6818 original (0x4819) tunes down to ~15.5–16 MHz; real reception observed ~18–630 MHz in earlier bench | k6-hf.md, k6-characteristics.md | Measured on earlier original-chip board, not every revision |
| Generic FD6818 guide describes 16–560 and 740–1120 MHz | bandreg.c; guide uses different register numbering | Hypothesis for upper band; not established at SMA |
| FD6818B (0x4829) has different driver and FSK/IF behavior | Aura and Flux drivers; k6-aprs-b-rx-audit.md | Known; do not transpose raw recipes |
| K6 Plus Open v1.1 schematic labels Q15 and Q18 HTL7G06S006P | Baofeng schematic [1] | Reference design, verify actual populated boards |
| HTL7G06S006P is an unmatched discrete 6 W LDMOS rated 1.8–600 MHz | Manufacturer rev 3.3 [2], examples at 175, 430, 520 MHz | Transistor rating, NOT assembled radio rating |
| 2m/70cm PA and path control exist separately | Aura drivers reg 0x33/0x36 and band GPIOs | Known; exact routing/match from schematic and board tracing |
| Original chip's crystal and frequency words latch on RXON/TXON | k6-own-fsk.md, xtalstep | Measured negative result for live RF FSK on original |
| Original chip's audio tone changes are phase continuous | k6-characteristics.md, tonewr | Measured; not proven on B |
| FT8 audio carried by FM decoded at other end (FM receiver) | k6-ft8-wspr.md bench run 6 | Measured, not native FT8 RF interoperability |
| Original GP0 IF tap has genuine pre-discriminator access but poor sensitivity in bench | k6-characteristics.md, k6-ft8-wspr.md | Hardware limitation to remeasure, B differs |
| Native B 1200 baud FSK service exposed | k6-rf-lab.md | Modules built, RF validation outstanding |

The schematic also lists 2SC3356 devices and BA592/KI892 switching diodes
in its RF area. Do not infer their precise function or a particular board's
network solely from part names. Trace the RF path before ordering modifications.
FCC ID 2AJGM-NAK6 exhibits [3] concern a Part 15B scanning receiver grant, not
a blanket TX approval. Internal photos/schematic exhibit contents not accessible
from that mirror; the published open schematic was independently inspected
via PDF text extraction. Confirm nets visually, component markings (H0606E for
the documented PA), and PCB revision with photographs. B chip variants may
use a different PA, matching, filter, or APC.

## 2. Band expansion rationale

A true 2m LPF should pass lower RF, but the entire output path is not a LPF:
RFIC driver, input matching, PA drain/load network, DC-block capacitors,
band switches and diode bias, harmonic filter, antenna matching. Matching
can be strongly frequency-selective; the drain impedance at 50 MHz can be
wrong even while a 50-ohm dummy load is connected to the antenna port.
A series 10 pF capacitor is about 106 ohms at 150 MHz and 318 ohms at 50 MHz.
Conversely an inductance's reactance drops with frequency. Thus low output,
excess current, high drain voltage, instability, harmonics or heating are
possible even when the transistor itself is rated for the band. A saturated
FM PA is normal; harmonic generation and mismatch, not clipping alone, are
the main concern. External LPF cannot repair PA stress or poor input match.

### Priority table

| Range | Why | Initial direction |
|---|---|---|
| 50–54 MHz (6m) | PA part is in rated range. Sporadic E, weak-signal, repeaters/data; quarter wave ~1.5 m, dipole ~3 m | First new TX candidate after 2m baseline. Dedicated external 6m LPF; calibrated low-power conducted tests |
| 28–29.7 MHz (10m) | Good candidate if 6m works; stock antenna is unsuitable | RX first, dedicated filter, different matching likely; operator/mode privileges vary |
| 18–25 MHz (17/15/12m) | Potential HF reception; transistor rated, RFIC lower limit near here | RX first; filter/match redesign likely; no FM voice in narrow data segments |
| 14 MHz and below | Original synthesizer below its observed range | No direct TX project; external transverter/converter only if worth it |
| 222–225 MHz (1.25m) | Some K6 boards marketed tri-band | Confirm board routing, APC calibration, stock behavior; don't assume all variants |
| 902–928 MHz (33cm) | Amateur packet / weak-signal, lower output could be useful | RX and PA-disabled leakage characterization first; PA specified only to 600 MHz |
| 740–1120 MHz other than amateur 33cm | Synthesizer research, receive applications | RX only unless independently authorized shielded laboratory experiment; no public TX profile |
| 2.4 GHz | Beyond documented RFIC range | External BLE/LoRa radio or transverter |

### Low-band external fixture

- A power-rated 6m LPF with verified insertion loss, return loss and rejection
  at 100/150/200 MHz and further harmonics. Target passband including 50–54 MHz,
  with stopband driven by measured harmonic levels and applicable emission
  rules, not a guessed design alone. Measure with analyzer/VNA before attaching.
- Verify 50-ohm termination, power and reflected power on both sides of filter.
  A reflective stopband filter can still present bad harmonic loads to PA.
  Use a suitable pad/isolator or redesigned matching only if rated and justified.
- At low frequencies, record fundamental output versus supply current and
  case temperature. Escalate in short bursts only after baseline comparable
  conditions; no full-power extrapolation from a single measurement.
- External filter and antenna are practical on 6m where the antenna is already
  a separate whip or wire. If PA mismatch dominates, consider an external
  low-power matching/filter board or transverter rather than destroying radios.

### Upper-band low-EIRP hypothesis

Loss can be useful *when it is a known, stable, matched attenuator*; an
uncharacterized filter stopband is not necessarily that. It may reflect
power into the drain, pass harmonics preferentially, or vary with temperature
and revision. A deliberately low-power RFIC-only path or driver tap into a
matched external pad/filter is preferable to driving an out-of-band 6 W PA.
A PA-off measurement on the original previously observed leakage about 40 dB
down, but that was a relative bench observation, not a calibrated output or
a promise for 900 MHz. Never assume PA-off means no emission.

Illustrative 5 W = +37 dBm, minus 40 dB = -3 dBm (~0.5 mW) conducted *if*
that loss really applies to the fundamental. EIRP = measured conducted output
minus feeder losses plus antenna gain (dBi). This math does not establish
compliance. For comparison US 15.249 at 902–928 MHz specifies 50 mV/m at 3 m
(roughly -1.25 dBm EIRP in ideal free space), and different harmonic limits.
15.247 is a different hopping/digital-modulation rule with its own conditions.
Neither grants a modified ham handheld unlicensed authorization. 15.201
certification and narrow 15.23 home-built exemption need independent review;
do not assume modification qualifies. US 33cm amateur use is a separate
Part 97 allocation, secondary/shared, with geographic and interference
conditions; verify 97.301/.303/.305/.307/.309/.313 and local band plans.
No operation in aircraft, public safety, cellular or other non-amateur
allocations just because output is small. No attempt to bypass RF restrictions.

Preferred experimental route: if 915 MHz RFIC locks and leaks a clean useful
fundamental, quantify it in the conducted fixture with PA disabled, then
evaluate a matched dedicated 900 MHz path. If it does not, stop trying to force
the PA and use an external 900 MHz transceiver. A transverter from validated
2m/70cm drive is a practical alternative for both 6m and 33cm.

## 3. Modulation: FM is a phase synthesizer in principle

For a constant-amplitude target s(t) = A exp(j[2*pi*fc*t + phi(t)]),
its instantaneous offset is delta_f(t) = (1/2*pi) d(phi)/dt.
A DC-coupled, sufficiently linear and fast FM modulator with suitable
deviation and known group delay can therefore generate constant-envelope
CPFSK, GFSK, MSK/GMSK and continuous-phase chirps directly at RF. A smoothed
phase transition can approximate PSK; arbitrary amplitude-varying SSB,
QAM or OFDM cannot be generated by phase alone. Envelope modulation
requires separate controlled amplitude or a linear signal chain.

The distinction is essential:
- **Direct RF frequency trajectory:** FT8 eight shifts at 6.25 Hz spacing,
  WSPR four shifts at ~1.465 Hz, etc. One RF tone at a time, phase continuous
  and shaped as specified, potentially standard WSJT-X compatible.
- **Audio-over-FM:** the chip's audio oscillator produces an audio tone, which
  frequency-modulates a separate RF carrier. It is useful for an FM receiver,
  but ordinary USB reception sees the carrier and symmetric FM sidebands, not
  the desired single RF trajectory. It does not automatically inherit true
  narrowband weak-signal sensitivity.
- **FM sideband experiment:** for sinusoidal modulation at f_m, the carrier
  and sidebands have amplitudes J_n(beta), beta=deviation/f_m. At small beta,
  J1 approximately beta/2, so each first sideband is weak; at beta about
  2.405 the carrier vanishes ideally, but both first sidebands and higher
  orders remain. In a shielded lab an SDR USB receiver can isolate one
  sideband and potentially decode our tone sequence. Do not radiate the
  unwanted mirror/carrier or claim a native narrowband mode. A narrow external
  IF filter/mixer could select one sideband; practical only as hardware route.

### Access-path audit before implementation

1. Existing audio oscillator REG_70/71 (per-chip mapping): measure frequency
   resolution, update latency, phase continuity, actual deviation and sideband
   levels on both chips. Original ~0.097 Hz audio frequency steps and ~117 us
   register writes were measured; this is NOT a direct RF +/-0.097 Hz control.
2. Native FSK: B has managed 1200-baud fixed FIFO; original has earlier
   experiments with binary FSK. Characterize supported deviations, real
   symbol rate, clocks, modulation sources and whether a slow/static mark
   or space can be held. Do not assume undocumented DC mode works.
3. Direct baseband: trace MIC/EARO/GP0 and MCU PWM/DAC/ADC connections;
   investigate flat bypass/digital-input and subaudio paths, TX HPF,
   pre-emphasis, limiter and PLL low-frequency response. A software
   waveform must actually reach the RF frequency control, not just audio.
   Characterize constant positive/negative offsets and response for 0.1 Hz
   through kHz. A PLL may cancel DC offsets if injected in the wrong place.
4. Live synthesizer frequency/trim: original chip's REG_3B and REG_38
   measurements showed changes only after RXON/TXON. Repeat passive
   test on B, and only consider TX with PA off and calibrated isolation.
   10 Hz tuning words alone are too coarse for WSPR / 6.25 Hz FT8 spacing.
   Re-keying per symbol is not a valid phase-continuous implementation.
5. Optional external TCXO/FM modulation or daughterboard if all stock DC
   routes fail; need separate proof of PLL low-frequency transfer, oscillator
   stability, reference injection suitability, and a board-specific schematic.
   Keep optional and do not assume it is necessary or feasible.

## 4. Mode candidates

| Mode | RF waveform | Feasibility and test |
|---|---|---|
| True CW / QRSS | Keyed unmodulated carrier (A1A) | Highest priority; improve existing cwkey; QRSS useful for cheap beacons but slow |
| Native 2FSK / RTTY | Two direct RF frequencies | Existing native FSK baseline; check slow-rate and small-deviation control; RTTY 45.45 baud/170 Hz as candidate |
| Flux narrow MFSK | Orthogonal continuous-phase tones plus FEC | First custom mode: 4/8/16FSK, 10–100 baud, spacing >= symbol rate as a starting point; coherent/noncoherent comparisons |
| FT8 / FT4 | Shaped direct RF 8/4-GFSK | 6.25 baud / 6.25 Hz spacing / ~50 Hz for FT8; 20.833 baud for FT4; direct-offset path needed |
| WSPR / FST4W | Narrow slow RF FSK | WSPR ~1.465 baud and spacing, ~6 Hz, 110.6 s; needs very stable oscillator and thermal/duty-cycle budget |
| JT9 / Q65 | Narrow MFSK | Good low-SNR research once direct slow-FSK proven; Q65 useful at VHF for difficult paths |
| JS8 | FT8-derived GFSK | Reuse frequency generation after true FT8 interoperability; more relevant to keyboard messaging |
| MSK144 | Constant-envelope MSK | 2000 baud, ~2.4 kHz RF bandwidth; demanding timing and direct flat path, excellent 6m meteor-scatter target |
| M17 packet | 4800 symbols/s shaped 4FSK | Separate high-rate path; not a narrow weak-signal mode; open packet protocol worthwhile |
| LoRa-like CSS | Direct RF chirps | If fast continuous-offset path exists, try custom narrow chirps and dechirp before FM limiter; no stock LoRa promise |
| Audio chirp/MFSK over FM | Audio modulation of FM | Already viable family; compare with existing PHY R; FM threshold/capture effect limits weak-signal benefit |
| BPSK/DPSK | Controlled phase transitions | Can in principle generate via FM derivative, requires calibrated fast signed offsets; true raised-cosine PSK needs envelope control |
| Ordinary SSB speech | Varying envelope + phase | No pure-FM equivalent; external SSB/filter or full polar TX is separate hardware project |

MSK144 and FT8 are designed for constant envelope [4], not intrinsically
dependent on an SSB PA if the same RF waveform can be synthesized directly.
For continuous-phase mode, phase errors, timing and spectral mask—not the
label on a modulation register—determine interoperability.

### Clean CW work

Existing cwkey uses tx_on/tx_pa binary gating, with PLL left on. Problems to
measure separately: initial unintentional burst, PLL settling/chirp, abrupt
envelope edges/key clicks, PA-off leakage, APC loop dynamics, and TX/RX
turnaround. Avoid repeatedly dropping the whole synthesizer between elements.
New experimental host callback (only after refactor) could arm TX with PA
disabled, apply a calibrated ramp, and retain hardware timeout. Try 4–8 ms
raised-cosine rise/fall as an initial CW envelope target, scaled for speed.
Do NOT assume APC code maps linearly to RF voltage or can safely be live
updated. Measure amplitude versus code, monotonicity, timing, overshoot,
current, off-state isolation and phase/frequency transient at each edge.
If the PA always leaves a residual carrier, gate the driver or a suitable
RF switch while keeping PLL locked; consider external switch as fallback.
Compare SDR spectra at 5/12/20/30 WPM with the same mean power; characterize
spacing/timing and clean key-up/down. QRSS can start with long elements
and well-controlled duty cycle; not a substitute for clean envelope.

### Weak-signal performance test

Compare three receivers using identical conducted input power and message:
FM demod + matched audio decoder; SDR coherent/narrow RF decoder; K6 pre-FM
IF decoder. Record sensitivity as required input dBm for 50%/90% packet
success (>=100 trials per level), and SNR in named reference bandwidth
(e.g. 2500 Hz for WSJT-X). Include synchronization cost, payload airtime,
ID overhead, offset/drift, duty cycle and energy per successful bit; never
compare a narrow mode's advertised dB SNR with FM RSSI directly.

The ideal noise floor in a receiver scales as 10*log10(B). Reducing 12.5 kHz
to 50 Hz is about 24 dB less integrated thermal noise, but real sensitivity
depends on noise figure, coherent/noncoherent detection, oscillator phase
noise, antenna noise and symbol energy. Strongest opportunity is direct
narrow RF TX plus pre-limiter RX. Existing GP0 tap's effective floor around
-79 dBm for usable captures in earlier bench is a concern; measure with the
new B IF route and compare a conventional narrow SSB RX audio path before
assuming it can compete with full SDRs. An external SDR gateway can initially
receive direct RF modes even if the handheld cannot decode them reliably.

## 5. Bench infrastructure

Already owned: RTL-SDR, RSPduo and MFJ-225. RSPduo for calibrated relative
spectrum, symbol trajectory, key shape, frequency error, harmonics up to 2
GHz (higher harmonics require other instrument). RTL for reproducible fixed
gain IQ capture; check actual tuner model and HF direct sampling/upconverter
support. Both are receivers, neither a calibrated spectrum analyzer by
default; AGC, images and overload can fake harmonics. MFJ-225 generator
1.8–170 MHz (~-5 dBm nominal) is a low-band reference, not a TX load or
spectrum analyzer. MFJ firmware/USB automation is separate project.

TX chain: radio -> rated 50-ohm directional coupler -> rated dummy load;
coupled RF -> verified total 60–70 dB attenuation (including coupling) ->
SDR or calibrated power meter. For 5 W this is ~-23 to -33 dBm. Check worst
case transmitter power, component power ratings, frequency coverage and
maximum SDR input; disable bias tee, use fixed low gain. Never connect radio
directly to SDR/MFJ. Separate harmonic acquisition at conservative settings,
verify receiver linearity with extra 10 dB pad. Use suitable notch/highpass
for harmonic dynamic range, calibrate losses. A proper calibrated analyzer
may be required for compliance or harmonics above receiver range.
For RX use MFJ/other known generator -> stepped attenuation -> K6, and
measure actual frequency and power. No transmitter back-feed to generator.
At 915 MHz need a separate controlled signal generator/reference.
Use shielded fixtures with terminated unused ports; check leakage outside.

Additional instrumentation: current-limited supply or protected battery
current instrumentation, probe for TX supply current, temperature sensor,
calibrated forward/reflected power sensor, known attenuators, 6m LPF and
ideally a VNA with appropriate range. Never connect a VNA active input to
powered TX. Establish stock-band reference on each radio before optional
experiments.

## 6. Proposed modules and host software (future, not implemented here)

Reuse the existing RF Limits / FSK RX / FSK TX / RF IF B bundles and earlier
rangerx, gp0dma, xtalstep, tonewr, ft8tx, wsprtx, cwkey, etc.; don't duplicate.

| Work package | Deliverable |
|---|---|
| RF inventory | Board profile: RFIC ID, PCB revision, PA marking, driver, paths, calibration baseline |
| RX characterization | Add frequency grid / reference offset / repeated levels, report raw status rather than claiming PLL lock |
| SDR companion | Host CLI for RTL/RSP capture, calibrated power, FFT, occupied bandwidth, FM deviation, unwrapped phase and instantaneous frequency, CW envelope, symbol errors |
| CW Lab | Conducted key-edge measurement, PA-off floor, test ramp profiles via future guarded service; compare old implementation |
| FM Lab | Test native FSK pattern, known audio tones and flat baseband access; compare actual RF trajectory to commanded trajectory |
| Weak-signal Lab | First direct-RF 2FSK/MFSK experiment, then FT8 receive on ordinary USB decoder; later WSPR and MSK144 |
| Low-band PA Lab | Conducted test harness restricted to explicitly characterized board+band; measure output, current, harmonics, thermal |
| 33cm passive Lab | Determine LO lock/actual signal sensitivity first; PA-off coupled spectrum, no PA drive beyond documented range |
| Hardware capability report | JSON + human summary per board and firmware build; separate measured/inferred/unknown facts |

No mandatory base feature growth; keep lab module and host tooling optional.
If a host primitive is needed, discuss after refactor: bounded session,
explicit physical confirmation, board-specific allowlist, PA-off default,
calibrated low-power and ramp operation, watchdog/abort, restoration,
no arbitrary radio safety register access. Keep license/region restrictions
independent from physical capability. Do not change ordinary user frequency
gate merely because a chip tuned. All unsafe/unverified modes unavailable
by default; no automatic extended-band transmit, no hidden unlock.
Do not expose a bench-only procedure in the production RF menu.

## 7. Phased acceptance gates

- A: Photograph actual boards and verify schematic identity; baseline stock
  RX/TX 2m and 70cm (220 only if supported). Establish calibrated limits and
  fixture isolation. Save both variants' data and firmware build IDs.
- B: Complete passive frequency map and reference-carrier validation.
  Require frequency-selective response at known test frequency and absence
  when source off; demonstrate offset rejection. PLL register alone is not proof.
- C: Characterize native 1200 FSK and clean CW on stock bands. Compare SDR
  spectrum, keyed envelope, phase and actual payload. Establish conservative
  current/temperature guard based on baseline and component limits.
- D: Test 6m only with reviewed PA/path risk, conducted load and PA-off
  first. Compare forward/reflected power, current, voltage/temperature trend,
  carrier and spur spectrum. Stop on unstable output, current anomaly,
  overheating, poor match, loss of lock, or unexpected emissions. Low-power
  usable output (even tens of mW) is worthwhile. No extrapolation to 5 W.
  Add external filter, retest with full path. Use regulatory limits with
  measurement uncertainty and engineering margin before antenna operation.
- E: Characterize flat modulation path. Accept direct FSK only after actual
  target RF frequency follows commands with continuous phase and clean
  spectrum. Initial FT8 trajectory accuracy target <0.5 Hz relative tone
  spacing error, negligible symbol timing error, low drift; actual WSJT-X
  USB decode of repeated test frames is the gate. For WSPR target <0.1 Hz
  relative spacing error and controlled drift over 110.6 s, repeated decode.
  Treat these as provisional engineering goals, not protocol guarantees.
- F: Repeat at controlled input SNRs and temperature. 6m direct weak-signal
  interop, then 10m if justified. 33cm only after passive map and explicit
  board-specific hardware route; no extrapolation of PA rating.
- G: Review actual band privileges and emission limits. Under US 97.307(d),
  modern HF transmitters need >=43 dB spurious suppression; 30–225 MHz
  has default >=60 dB plus specific low-power exception (read text). Design
  to exceed applicable minimum, with measured fundamental, spur power,
  calibrated chain and uncertainty. Above 225 MHz general spurious,
  bandwidth and interference requirements still apply. No field testing
  until operator accepts documented results and legal frequency/mode.

## 8. Record format

One run per chip ID + board revision + firmware hash + module hash.
Timestamp UTC; frequency commanded and measured; RX/TX state; PA/path
selection and settings; supply V/current, temperatures, expected and measured
RF power/return loss, attenuation calibration, SDR model/gain/sample rate;
relative deviation/phase/timing/occupied bandwidth; spurs in dBc and absolute
dBm with uncertainty; raw register reads (only known safe), source levels,
IQ/log files and test results. Save raw ADC/IQ where practical, independent
decoder output, and failures. Hardware evidence must not be replaced by
emulation claims. No signing keys/radio backups/large IQ artifacts in git.

## Sources

1. Baofeng UV-K6 Plus Open v1.1 schematic (Q15/Q18 PA labels):
   https://github.com/cnt7/BAOFENG-UV-K6-Firmware/blob/main/docs/schematic/BF_K6_Plus_Open_V1.1.pdf
   Source-available BFPL; use factual study only, no code or document redistribution.
2. Watech HTL7G06S006P product datasheet rev 3.3:
   https://www.huatai-elec.com/Public/Uploads/uploadfile/files/20240416/HTL7G06S006PProductdatasheetrev3.3.pdf
   Alternate: https://file01.andesource.com/dkpdf/HTL7G06S006P_Product_datasheet_rev3.3.pdf
3. FCC grant/exhibits: https://fccid.io/2AJGMNAK6
4. WSJT-X protocol specifications: https://wsjt.sourceforge.io/wsjtx-main_en.html
5. FCC: https://www.law.cornell.edu/cfr/text/47/97.307 ;
   https://www.law.cornell.edu/cfr/text/47/97.303 ;
   https://www.law.cornell.edu/cfr/text/47/15.249 ;
   https://www.law.cornell.edu/cfr/text/47/15.247 ;
   https://www.law.cornell.edu/cfr/text/47/15.201
6. Existing Flux evidence: k6-characteristics.md, k6-hf.md, k6-fesw.md,
   k6-own-fsk.md, k6-ft8-wspr.md, k6-fd6818b-refcal.md, k6-rf-lab.md,
   k6-insights/10-a-weak-signal-mode-constant-envelope-mfsk-on-ssb.md.
