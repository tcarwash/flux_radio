# Flux TX-15R — 192-word longer-pilot RF tone and phase measurements

**Local bench date:** 2026-11-22 (America/Los_Angeles); 2026-11-23 UTC.
**Hardware:** physical K6 FD6818B/BK4829, RFIC ID 0x4829.
**Status:** **two real conducted/dummy-load TX experiments completed**
with verified radio / module restoration; all scores below are from
air-coupled RTL-SDR I/Q at 1.024 MS/s, **not** calibrated RF power or
absolute 433.500-MHz carrier phase. This report supersedes the
**64-word pilot only** design as the next TX-15 waveform evidence.
It does not supersede the other TX candidates.

## Physical findings

The FD6818B's serial-programmable REG_70/REG_71 FM audio tone
oscillator sent a **192-slot** (nominal 150/s, 1.28 s) programmed
sequence twice into the existing user-confirmed dummy-load bench.

- **48 pilot tone symbols:** 16 groups of 3, containing all eight
  tones and 15 strong frequency changes spread across the first
  320 ms.
- **144 held-out symbols:** 48 three-symbol runs, with repeated
  tone-pair changes for checking repeatability.
- Eight nominal audio tones: 502, 782, 1091, 1424, 1693,
  1987, 2273, and 2519 Hz.
- Both hardware modules returned **192/192 programmed events**,
  `late=0`, and the source TIMER report
  `cycles=122880086` / `122880068`. The module requested
  a firmware 1800-ms bounded TX lease, which it completed.
- Before any SPI mutation, the exact prior installed module
  executable in slot 30 was CRC/readback verified and saved as
  an original envelope. **Both experiments restored slot 30
  from a trusted original executable** with new signed replay
  serial and verified readback, and restored both 144.390-MHz
  VFOs, TX inactive. No main-MCU flash or calibration records
  were written.

| Real RF result | Capture 01 | Capture 02 |
|---|---:|---:|
| K6 firmware completed | 192/192 | 192/192 |
| Firmware-reported late writes | 0 | 0 |
| Tone IDs, 144 heldout | **143/144** | **144/144** |
| Strong pilot frequency transitions | 15 | 15 |
| Held-out strong transition midpoints | 44 | 44 |
| Observed SDR-relative update rate (pilot fitted) | **149.664/s** | **149.668/s** |
| Pilot midpoint RMS error | 12.3 µs | 13.4 µs |
| Heldout timing median abs. error (pilot-only linear clock) | **24.5 µs** | **58.8 µs** |
| Heldout same-word audio phase RMS, 95 edges | **0.383°** | **0.374°** |
| Heldout changed-word audio phase RMS with pilot clock, 48 edges | 21.72° | 39.11° |
| Heldout changed-word RMS after *one pilot-only* effective audio delay fit | **13.79°** | **30.35°** |

**Phase domain:** sinusoidal **audio** tone recovered by FM
frequency discriminator from the recorded RF. These numbers
**are not RF-carrier IQ EVM, SSB/quadrature phase error,
a calibrated symbol-rate clock, or arbitrary RF phase output**.

The pilots improved clock estimation substantially over
TX-15P/Q's original three strong transitions. But the two
runs' actual **heldout clock error varies**, especially late
in run 02. A long, pilot-only **prefix** does not track all
later scheduler, RFIC/RX filter, phase and SDR drift.

### What repeats, and what does not

95 consecutive same-word writes in each heldout reproduce
recovered audio phase to about **0.38° RMS**. 48
frequency-changing words remain systematically more sensitive
to the clock and analog transfer.

An independent offline cross-run analysis takes the *frozen*
pilot-calibrated audio-phase residuals of both saves, joins
the same **48 known changed-word heldout indices**, and gets:

- Pearson correlation **0.884** between the two phase-residual
  sequences (post-hoc, exploratory, **not** a blinded predictive
  model score).
- Median absolute run-to-run residual difference **6.90°**;
  circular RMS difference **19.27°**.
- Repeated 1→6 and 6→1 tone changes show similar phase
  residuals in a run, but the late run-02 events drift
  markedly larger than the same source events in run 01.
- A naive *cross-run* per-tone-pair learned correction
  reduced run-02 RMS from **30.35° to 20.13°**, but when
  reversed it **worsened** run-01 from **13.79° to 16.42°**.
  This cross-run correction was developed after viewing
  both runs and is a diagnostic, **not a proven calibration
  method**. A per-tone static phase transfer model fitted
  on the 15 pilot edges offered only minor improvement.
  That argues against labeling the residual *solely*
  as a stable filter phase response.
- Late event midpoint timing relative to the frozen pilot
  grows beyond **100 µs** in the second capture. At
  2–2.5 kHz, that scale can generate many tens of degrees
  of audio phase residual.

**Conclusion:** much of the prior huge changed-word phase
error was indeed missing a correct source timebase (TX-15Q);
TX-15R further shows that accurate **time-varying** symbol
timing is necessary to exploit the phase-continuous tone
path. The residual is reproducible enough to be worth
engineering, but we still cannot uniquely apportion
oscillator-phase reset, MCU timer drift, analog TX filter,
RX discriminator and SDR clock contributions.

### Direct evidence of audio-frequency response / deviation rolloff

The recovered FM instantaneous-frequency *sine amplitude*
for each programmed tone, fitted independently in the heldout
slots (excluding ill-conditioned local fits), is:

| Programmed audio tone | Run 01 recovered deviation | Run 02 |
|---:|---:|---:|
| 502 Hz | 1,229 Hz | 1,230 Hz |
| 782 Hz | 1,202 Hz | 1,202 Hz |
| 1,091 Hz | 1,186 Hz | 1,188 Hz |
| 1,424 Hz | 1,169 Hz | 1,169 Hz |
| 1,693 Hz | 1,133 Hz | 1,132 Hz |
| 1,987 Hz | 1,074 Hz | 1,070 Hz |
| 2,273 Hz | 1,027 Hz | 1,026 Hz |
| 2,519 Hz | 1,001 Hz | 1,002 Hz |

The highest-vs-lowest tone amplitude ratio is **0.815**
in both independent RF captures, about **18.5% falloff**
across the 502–2519 Hz audio range. This makes audio
**deviation/gain equalization** a concrete future TX-15
task. It is the composite response of the RFIC, radio
and uncalibrated SDR receiver, **not** a measurement
isolating one transmitter filter or a claim of absolute
RF output power.

### Terminal-slot limitation: don't overclaim perfect decode

Run 01's sole heldout classification error is
**slot 191** (final tone 4, detected tone 0). In run
02, the final slot was nominally decoded as tone 4.
However **both captures have severely degraded terminal
sinusoid fits**, with fit RMSE of approximately
**2772 Hz / 1846 Hz** at slot 191 versus ~50 Hz
throughout interior slots. PA/TX shutdown or filter
tail near the end is strongly implicated, but the
precise mechanism is not isolated.

We report **143/144** and **144/144** *as captured*.
Do **not** secretly change the denominator or present
run 02's terminal-slot guess as a valid clean tone.
The next vector should reserve an explicit **unscored
tail guard** before release/de-key, designed *in advance*.

## Implementation and provenance

- C data-only waveform:
  `c/k6/mod/tx8_long_phase_probe.h`
- Existing inert/guarded module:
  `c/k6/mod/tx8probe.c`; optional
  `TX8_PHASE_VECTOR_V3=1` branch. Default behavior
  and prior V2 branch unchanged, V2/V3 mutually exclusive.
  The C module is clocked and uses a fixed 1800-ms
  RF TX lease; wrong firmware/chip, wrong frequency,
  active key, occupied channel, busy timer, failed SPI
  writes or missing RF acknowledgments abort.
- Exact archived RF-armed wrapper **outside the repo**:
  `~/flux-bench/tx-injection-20261122/tone-oscillator/tx15r-long150-enabled.c`
  with compiled code SHA256
  `5b167ff859e783464e01b7345fd7444501681d89edfbd06172e9ae0b4612a842`.
- Guarded physical transaction, exact-code hash and
  slot-30 backup/readback restoration:
  `tools/k6_tx15r_rf_bench.py`
- Training-only RF audio-phase, symbol-clock,
  transfer, 144-tone holdout and response analyzer:
  `tools/k6_tx15r_rf_analyze.py`
- Between-run read-only comparison, explicitly
  exploratory across the two heldout sets:
  `tools/k6_tx15r_compare.py`
- Emulator and synthetic/physical regressions:
  `tests/test_k6_tx15_long_vector.py`,
  `tests/test_k6_tx15r_rf_analyze.py`,
  `tests/test_k6_tx15r_compare.py`

All raw RF/transaction evidence lives under:

`/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/`

| Saved file | Content |
|---|---|
| `tx15r-long150-run01.npy` | Raw interleaved U8 SDR IQ, SHA256 `2894080e5fa47cf1336e0461131af90537a93340761003021a396127387b9c9f` |
| `tx15r-long150-run02.npy` | Raw IQ, SHA256 `a89d96fb9c57fa0fc98fbd98562e7b9ccb0046a78004a9fed21c84d499905912` |
| `tx15r-long150-run01.json`, `tx15r-long150-run02.json` | Full physical transaction, SDR baseline and exact restoration receipts |
| `slot30-before-tx15r-long150-run01.k6m`, `slot30-before-tx15r-long150-run02.k6m` | Verified, complete pre-replacement module backups |
| `tx15r-long150-run01-phase-v2.json`, `tx15r-long150-run02-phase-v2.json` | Per-transition timing and audio-phase metrics, frozen training/holdout, tone amplitude response |
| `tx15r-long150-crossrun-v1.json` | Exploratory cross-run residual and 8-tone response comparison |

Reanalysis without any SDR/radio device interaction:

```sh
cd /storage5/dev/flux-native-fsk-pdm
python3 tools/k6_tx15r_rf_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15r-long150-run01.npy \
 --out /tmp/tx15r-example-unique.json

python3 tools/k6_tx15r_compare.py \
 --a ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15r-long150-run01-phase-v2.json \
 --b ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15r-long150-run02-phase-v2.json \
 --out /tmp/tx15r-crossrun-example-unique.json

PYTHONPATH=/tmp/flux-fsklab-unicorn-deps python3 -m pytest -q \
 tests/test_k6_tx15_long_vector.py \
 tests/test_k6_tx15r_rf_analyze.py \
 tests/test_k6_tx15r_compare.py
```

All these analyses operate on saved files. The RF bench
requires explicit load/watchdog RF acknowledgments and
rejects an unexpected firmware/image/slot; it is not
a default radio action.

## Next actionable waveform: TX-15S distributed-pilot scheduler

Keep the **192-event / 1800-ms lease budget** and
the same eight noncommensurate audio tones.
Unlike a prefix-only 48-symbol pilot, distribute
four predeclared **12-symbol reference windows**
across the waveform (for example beginning at
indices 0, 45, 90 and 135), with other slots
reserved for heldout scheduled transitions.
Choose windows with multiple strong known
tone-frequency steps and repeated pairs.

Train a **piecewise-linear or low-order spline
timebase entirely from those reference windows**;
fit any tone/phase model only on designated pilot
edges, score only heldout regions, and compare
against the previous simple linear clock and
audio-phase delay. Use a **terminal non-scored
tail guard** before de-key. Fix pilot layout and
all scoring rules **before** capturing physical RF.

A separate diagnostic experiment should observe
the MCU TIM17/TIM14 clock against an independently
measured reference during source execution.
A third path is gain equalization using REG_70;
its signed/temporal behavior must first pass
a separately guarded real-RF test. The **TX-17
signed synthesizer frequency-offset** lead remains
open pending a qualified stable RX-only external
reference. Preserve TX-10/11 native FSK/DMR,
TX-16 CTCSS, TX-18 I2S/digital baseband, TX-20
APC/polar and TX-23 I/Q-daughterboard routes.

**Current bottom line:** strong, repeatable internal
audio-FM modulation is demonstrated. **True arbitrary
coherent RF IQ / phase is not demonstrated.**
