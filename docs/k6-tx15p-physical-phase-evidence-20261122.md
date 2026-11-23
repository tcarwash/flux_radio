# Flux TX-15P — two real RF captures establish repeatable same-tone phase continuity

Bench date: 2026-11-22 America/Los_Angeles (2026-11-23 UTC).
Branch: `research/native-fsk-pdm`.
This **supersedes the emulator-only status** of the new TX-15P
phase vector in [the original design report](k6-tx15-phase-discriminator-20261122.md).

## Result, and exactly which kind of phase was measured

**The FD6818B / 0x4829 K6 physically transmitted two bounded,
independent 64-register-update TX-15P waveforms at nominal
433.500 MHz into the user-confirmed dummy-load setup.** The
air-coupled RTL-SDR recorded two separate 1.024 MS/s I/Q files,
fixed tuner gain 0 dB, centered at 433.750 MHz. Both modules
returned 64, reported zero late writes and unkeyed; exact
original slot-30 executable was restored and verified after each
transaction, and both 144.390-MHz VFOs restored.

**The measured phase is the phase of the audio-frequency
oscillator tone recovered from the RF FM discriminator. It is
NOT the absolute 433.500-MHz RF carrier phase** and is not
coherent arbitrary complex RF I/Q EVM.

| Metric | Run 01 | Run 02 |
|---|---:|---:|
| Firmware module completed | 64/64 | 64/64 |
| Firmware reported late writes | 0 | 0 |
| Known-pilot tone IDs | 16/16 | 15/16 |
| Held-out tone IDs | **48/48** | **48/48** |
| Qualified held-out **same-word** updates | 28/28 | 28/28 |
| Same-word median absolute apparent phase jump | **0.214°** | **0.229°** |
| Same-word RMS apparent phase jump | **0.424°** | **0.404°** |
| Qualified held-out **changed-word** updates | 20/20 | 19/20 |
| Changed-word median absolute apparent phase jump | **92.88°** | **95.18°** |
| Changed-word RMS apparent phase jump | 110.23° | 100.65° |
| If same words **reset on every write**: held-out phase residual RMS | 103.34° | 103.25° |
| Slot 30 and VFOs restored | yes | yes |

The 1/16 pilot miss in run 02 and the single changed-word
transition excluded from its phase assessment reflect RX
startup / local sinusoid-fit qualification. The held-out
tone identification is still perfect. Exclusions are
threshold-driven on fitting amplitude and residual, not
chosen by inspecting the ground-truth phase labels.

**Strong physical conclusion:** rewriting the same
`REG_71` frequency word does **not** measurably reset the
recovered oscillator audio phase at 150 writes/s in these
captures, far better explained by continuous tone phase than
a hypothetical reset-on-every-write source.

**Not established:** that the 0x4829 phase is continuous
*across different REG_71 frequency words*. Changed-tone
apparent phase differences are large and variable, even
though the audio tone identity follows programming perfectly.
The received FM discriminator includes audio filter group
delay/phase, finite transmitter settling, frequency-dependent
deviation, SDR response, CPU/RFIC clock mismatch and carrier
offset; the local sinusoid phase comparison **cannot separate
those effects** from the oscillator's phase state. Old
0x4819 continuity experiments do not fix the 0x4829 result.

This is *negative evidence* for treating TX-15 as a plug-and-play
arbitrary **coherent RF-phase** trajectory generator, not a
proof that it is fundamentally impossible. TX-15 remains a
valuable native programmable audio/AFSK backend.

## Physical state, guards, and reproducibility

The software first read the live K6 state (before RF):

- Firmware build `95d6b260d4122287`, module API ceiling 26,
  chip identity checked by the module as 0x4829.
- K6 RX idle, VFO A and B **144.390 MHz**, ~8.03 V battery.
- Prior slot-30 source executable
  SHA256 `6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`,
  identical to the trusted archived source code.
- A Prolific PL2303 programming cable on `/dev/ttyUSB0`
  was free for exclusive use. `/dev/ttyUSB4` was occupied by
  a separate MFJ-225 Python service; it was never touched.
- The RTL-SDR opened/closed successfully; separate RX-only
  preflight on nominal test channel had ~2.83 dB local peak,
  no ADC clipping or overload heuristic.

The exact compiled RF-on `tx8probe.c` module had code SHA256
`544e9d8e54617eaa3c2f17c483f02eaba2ab76ff76ecfc52672c476a50745903`
and source SHA256
`48fa0e7d551542721c655615da69dc569edc9696bfcc4023b2e3eb79e8b8c135`.
Its wrapper explicitly defined
`TX8_PHASE_VECTOR_V2=1`, `TX8_BENCH_ENABLE=1`,
`TX8_RF_ENABLE=1`, `TX8_RF_FIXTURE_ACK=1`,
`TX8_RF_WATCHDOG_ACK=1`, `TX8_HZ=433500000`,
`TX8_SYMBOL_RATE=150`, `TX8_GAIN=24`.
The module requests a firmware-owned **1800-ms TX lease**.
This is software watchdog/lease evidence, **not independently
measured emergency de-key hardware** or RF connector isolation.

`tools/k6_tx15_phase_rf_bench.py` was adapted from the
established conservative APC single-shot transaction.
It enforces an exact firmware build, exact compiled test-code
hash, exact trusted pre-test slot-30 executable and signed
header/CRC, the source's conducted/load/watchdog acknowledgments,
two VFOs and RX idle, an SDR baseline and a low RSSI channel.
It backs up the original complete module envelope **before**
SPI mutation and restores the trusted executable with an
incremented replay serial, checking readback. It starts
**one TX module per invocation**, does not retry automatically,
and refuses output overwrites.

Both runs reported:
`tx8p2 n=64 cycles=40960055 late=0 worst=38`.
Both returned from guarded TX and checked exact restored code
and VFO status.

No internal MCU firmware was flashed; only slot 30 was
temporarily replaced and restored. No registers outside the
previously tested `REG_70/71` tone control or routine TX
control were intentionally modified by this test. The SDR
measurements are **air-coupled and uncalibrated**: no
absolute RF power, calibrated spectral mask, conducted
attenuation, SWR or out-of-band emissions were verified.

## Files on the development host

All saved under:

`/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/`

- `tx15p-phase150-run01.npy` (raw interleaved U8 IQ):
  SHA256 `e81f60724ddd6920a75cbcfde8981e61d9bd0ddd4e065082bd2e6446d687bd33`
- `tx15p-phase150-run02.npy`:
  SHA256 `8fc58fbf6910c648805a3628421225e4e3a288a2c9b056c5946461f5c80fd203`.
- `tx15p-phase150-run01.json`,
  `tx15p-phase150-run02.json`: SDR summaries and exact
  per-run hardware transaction / restoration evidence.
- `slot30-before-tx15p-rf150.k6m`,
  `slot30-before-tx15p-rf150-repeat.k6m`: exact
  pre-run full envelopes; later restoration has a NEW
  replay serial/MAC but identical executable code.
- `tx15p-phase150-phase-edges-v2.json`,
  `tx15p-phase150-run02-phase-edges-v1.json`: per-transition
  phase observations, qualifications, timing, amplitude,
  and both runs' FFT-domain RF metadata.
- `tx15p-rf150-enabled.c`: exact arming wrapper, deliberately
  **outside** the source repository.

The earlier `tx15p-phase150-phase-edges-v1.json` was
an intermediate pilot-only **tone-ID** timing fit with
unnecessarily loose edge alignment. Its same-word RMS
is valid for its chosen window, but the v2 analysis
adds independent **pilot-only near-edge timing
refinement**, so v2 is the comparable final run-01
measurement. Neither correction uses held-out *symbol*
labels to select timing.

## Reproduce the analysis without any radio interaction

```sh
cd /storage5/dev/flux-native-fsk-pdm

python3 tools/k6_tx15_phase_rf_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15p-phase150-run01.npy \
 --out /tmp/tx15p-phase-run01-unique.json

python3 tools/k6_tx15_phase_rf_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15p-phase150-run02.npy \
 --out /tmp/tx15p-phase-run02-unique.json

PYTHONPATH=/tmp/flux-fsklab-unicorn-deps python3 -m pytest -q \
 tests/test_k6_tx15_phase_rf_analyze.py \
 tests/test_k6_tx15_phase_vector.py \
 tests/test_k6_tx_tone_rf_bench.py
```

`tools/k6_tx15_phase_rf_analyze.py` consumes saved IQ only.
It acquires the 150-Hz slot time using **the first 16 tone
IDs**, then refines the slot boundary using only those
same 16 pilot slots' near-edge fit residuals. It
**freezes timing** for all 48 held-out symbols and
compares same-word and changed-word phase separately.

Each slot fits a sine, cosine and DC offset at the
programmed (quantized) tone frequency. A local phase
jump is measured after subtracting the expected
advance through the previous nominal tone. This is
a *receiver-conditioned audio phase metric*, not
coherent full RF I/Q EVM. It uses fixed audio
amplitude/residual thresholds for qualifying
well-formed windows; no heldout labels are
used to choose the timing or thresholds.

## Meaning for arbitrary waveforms / next experiment

TX-15 has shown:
- Reliable serial-driven audio FM tone selection.
- Strong local audio phase continuity across unchanged
  frequency-register writes (twice on physical B-chip).
- Large, variable apparent phase changes across
  different frequency-register values, with source
  uncertain due to filters and receiver clock.

Next discriminating test: measure the **actual B-chip
frequency-change waveform**, with a dedicated pilot
that trains transition-specific analog filter/phase
response and separate held-out revisits of identical
tone-pair changes. Compare a continuous-accumulator
with estimated transfer/filter delay against
per-write, per-change and hardware-relative
accumulator hypotheses; do **not** fit models on
held-out labels. This is a more targeted question
than more ordinary FSK tone decoding.

Parallel TX-17: the [direct signed-FM synthesizer
register audit](k6-tx17-direct-fm-register-audit-20261122.md)
found no verified instantaneous RF offset register.
An **RX-only** B-chip latch/readback test of 0x38/39
is an independent, serial-preserving vector, before
attempting TX hardware writes. Preserve TX-10..23,
including native modem/PDM, APC envelope, CTCSS,
digital baseband, TCXO and external I/Q frontends.
