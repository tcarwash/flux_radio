# Flux TX-12D — MICIN input fixture, pinout verification and RF receive evidence

Date: 2026-11-22 local / 2026-11-23 UTC.
Branch: `research/native-fsk-pdm`.

## Outcome and hard boundary

**No MICIN signal source is presently verified to be connected to this
particular FD6818B K6. No new radio flashing, microphone driving,
TX command, or SDR recording was initiated by this checkpoint.**

A receiver-side tool, `tools/k6_micin_rf_observe.py`, now scores a
**preexisting saved** 48-ksample/s complex IQ or 1.024-Msample/s
RTL-SDR U8 IQ file against the exact known 44-ms 4-FSK loop
([TX-12B](k6-micin-loopable-coupling-20261122.md)).
It requires an isolated 65–200 ms RF burst with unkeyed samples before
and after. Acquisition uses the entire independently specified
16-symbol **shaped pilot only**, including synchronization and one
frequency offset + scale. It freezes this fit for the **32 held-out
symbols** and reports four-level errors and frequency-trajectory RMSE.

The tool **cannot establish that the sample came from the K6** or that
the radio's actual MICIN was driven. It is not calibrated spectrum/
power equipment, full complex-IQ EVM, emissions-mask validation, or
an RF transmitter. Test/CLI code does not open SDR/serial/audio devices.

### Hardware actually visible to the development host

Read-only host enumeration at this checkpoint showed:

- **USB audio capture:** `0c76:161e` USB PnP Audio Device,
  ALSA `hw:1,0` capture only
- **Onboard audio:** Intel HDA ALC887-VD with analog playback
  `hw:2,0` and analog capture `hw:2,0`
- **RTL-SDR:** RTL2838 USB device `0bda:2838`
- **Programming cable:** Prolific PL2303 `/dev/ttyUSB0`, another
  CH340 at `/dev/ttyUSB2`, and FTDI at `/dev/ttyUSB4`
- Flux development catalog service is running independently; its
  radio-cable/serial port should not be seized for this investigation

**Software enumeration cannot establish physical cables,
continuity, RF dummy-load rating, attenuator value, whether jacks are
occupied, radio PCB revision or whether the correct equipment is
connected.** In particular, the USB PnP Audio Device is not a
confirmed **output** adapter. Do not assume soundcard PCM full-scale
maps to any safe MICIN voltage.

## Accessory jack candidate mapping — NOT FD6818B-specific proof

UV-K5/K6 are reported to use the Kenwood-style two-plug K1 socket.
The candidate shared wiring in independent references is:

| Plug contact | Published K1 function | Critical caution |
|---|---|---|
| 3.5-mm **ring** | MIC+ analog source input | **Not** verified analog input voltage or coupling on this actual B radio |
| 3.5-mm **sleeve** | MIC−, shared programming UART RX / PTT sensing | Can **key TX** if shorted toward radio ground; do not tie casually to PC/audio ground |
| 3.5-mm **tip** | +V / microphone bias out | **Do not drive or short** |
| 2.5-mm **sleeve** | Radio ground / SPK− | Identify by actual battery-removed continuity |
| 2.5-mm **ring** | Serial TX from radio | **Do not drive** from soundcard |
| 2.5-mm **tip** | Speaker output; on some revisions has 3.6–3.8 V DC | **Do not connect** to low-voltage audio input without measuring and conditioning |

Sources (mostly older *non-B* K5/K6):

- https://cdomotor-g.github.io/quansheng_alert/cable/
  provides the explicit TRS contact map and notes published
  disagreements and a revision-specific 2.5-mm tip voltage.
- https://esp32idf-aprs.readthedocs.io/en/latest/en/hardware.html
  independently describes shared PTT/MIC and data/audio contacts.
- https://www.mobilinkd.com/2024/06/25/hacking-the-uv-k6-for-digital-modes/
  links Mobilinkd's **BK4819-era** hardware and firmware modifications
  for flatter low-frequency digital modulation.
- https://github.com/mobilinkd/uv-k6-digital-mod
  contains the upstream notebook, schematic/photograph annotations.
  The described parts/PCB traces are **not assumed compatible**
  with an FD6818B (ID `0x4829`) unit.

**Do not transplant the BK4819 capacitor/resistor or REG_7E DC-filter
values to the FD6818B without board-level evidence.** An API-26
MICIN ADC enable only proves a software state transition, not an
electrically flat modulator.

## Manual measurement evidence still required

Complete the following *physical* checks before authorizing a
MICIN source or real RF waveform test. The assistant cannot obtain
these facts through a USB/ALSA device listing.

1. **Radio powered off, battery removed, all cables unplugged.**
   Photograph the actual jack/breakout and PCB revision if accessible.
   Use a fully seated breakout plug with separated labeled test pads.
   Establish ground from the real chassis to the presumed 2.5-mm
   sleeve by continuity, then independently confirm plug tip/ring/
   sleeve identities. Do **not** probe inside a half-inserted jack.
2. **Independent adapter test disconnected from radio.** Measure
   unloaded source **DC**, peak-to-peak AC, series resistance,
   short-circuit current limit, output impedance, isolation and
   whether signal is AC/DC coupled. The nominal `0.03` WAV peak
   is a dimensionless normalized sample, *not* a safe mV target.
   A balanced electrical channel with enough headroom is required.
3. **Powered radio, test-point meter only**, with no audio source
   connected: measure DC voltage at each candidate contact relative
   to independently verified radio ground, and verify idle PTT is
   not asserted. Document whether the programming plug physically
   occupies the shared MIC/PTT line and how a safe breakout would
   isolate them. Do not drive 3.5-mm tip, 2.5-mm ring/tip, or
   3.5-mm sleeve from the soundcard.
4. **Measure actual MICIN bias/loading** with high-impedance
   instrumentation; no arbitrary 0.03-full-scale PC audio output.
   Check whether the proposed isolation/attenuation preserves the
   desired 4-FSK low-frequency content. An *ordinary isolation
   transformer or series capacitor blocks DC* and therefore
   does not establish the flat signed-level modulation needed
   for long-frequency plateaus.
5. **Independently verify RF fixture:** known rated dummy load,
   coax termination, isolated pickoff/coupler, attenuation ahead
   of RTL-SDR, bounded serial keying, external TX timeout
   independent of firmware main loop, no shared accidental PTT,
   measured ability to restore default receive state.
6. **First real RF case after all of the above:** distinguish
   RF-edge bursts and carrier-only leakage from *qualified*
   waveform success. Use both balanced and stress audio patterns,
   known pilot + heldout labels, measured deviation, occupied
   bandwidth and TX startup/shutdown timing.

The safety gate from earlier experimental combined-FSK/APC work
is not bypassed. This file does **not** authorize TX.

## Important older PA-off negative-control recording

The **other Flux research worktree** previously recorded a PA-off
state test. Artifacts under
`~/flux-bench/modulation-micin-20261121/` include:

- `txmic-paoff-rfcheck.json`:
  RAM-loaded lab probe; mode PA-off, no MIC waveform injected,
  user bench acknowledgement, RX restoration after run.
- `txmic-paoff-rfcheck-sdr-iq.npy`, a **6-second** historical
  1.024-MS/s RTL-SDR capture centered at 433.750 MHz.
- `txmic-paoff-rfcheck-rf-edge-analysis.json`:
  independently logged **one 26-ms RF-like spectral segment**
  at ~3.040–3.066 s, ~22.51 dB over local adjacent spectral
  bins, near an **uncalibrated** -280.5-kHz receive offset.
  This is not calibrated RF output power nor a spectrum mask.

A negative-control run of the new receiver:

```sh
python3 tools/k6_micin_rf_observe.py \
  --iq /home/tyler/flux-bench/modulation-micin-20261121/txmic-paoff-rfcheck-sdr-iq.npy \
  --sample-rate 1024000 --rf-offset-hz -280500 \
  --pattern balanced --report /tmp/flux-paoff-micin-qualification.json
```

correctly **refused to qualify a MICIN 4-FSK cycle**:
`unable to distinguish RF from no-TX baseline` (exit code 2).
It did **not** claim no RF existed. A 26-ms event is too short
to contain the 44-ms known loop, and is a separate safety/
transient-emission concern. Another receive-path edge detector
should retain those short bursts rather than treating them as
successful 4-FSK.

The earlier radio activity briefly included RF-capable TX state
before PA-off was commanded. The archived report alone does
not prove the RFIC emitted 22.5 dB relative to a calibrated
load or identify the burst's precise electrical cause. Do not
use PA-off as evidence of RF isolation.

## Reproduce software-only positive check with synthetic RF

```sh
cd /storage5/dev/flux-native-fsk-pdm
python3 -m pytest -q \
  tests/test_k6_micin_rf_observe.py \
  tests/test_k6_micin_burst_lab.py \
  tests/test_k6_micin_4fsk.py
```

The test suite generates a **synthetic** 100-ms RF burst with
arbitrary audio-loop phase, plus noise/HPF cases; both complex
48-kHz and raw 1.024-MS/s unsigned-byte input are exercised.

With one generated balanced reference and no highpass,
pilot correlation was ~0.997, 0/32 heldout errors.
A simulated 20-Hz first-order high-pass on the **stress**
pattern produced 19/32 heldout errors, versus 0/32 on balanced.
These are **not observations of physically transmitted MICIN
modulation**. They are receiver validation against synthetic
signal construction and hypothetical analog coupling.

### Future recorded-data command (only after real capture exists)

```sh
python3 tools/k6_micin_rf_observe.py \
  --iq /path/to/archived-iq.npy \
  --sample-rate 1024000 \
  --rf-offset-hz VERIFIED_MEASURED_OFFSET_HZ \
  --pattern stress \
  --report /path/to/new-unique-micin-rx-score.json
```

The LO offset must be established by the receiver/capture
setup; no default center frequency for an arbitrary physical
RF recording is safe to assume. A successful receiver score
never attests source or proves the MICIN-to-RF analog path
without synchronized, independent TX and audio provenance.

Related: [MICIN baseband/filter exploration](k6-micin-bypass-lab.md),
[API-26 emulator acceptance](k6-micin-api26-emulator-acceptance-20261122.md),
[TX roadmap](k6-tx-next-experiments.md).
