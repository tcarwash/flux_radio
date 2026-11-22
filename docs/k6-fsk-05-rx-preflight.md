# Flux FSK-05 — SDR preflight and held-out decode verification

Date: 2026-11-22; branch research/native-fsk-pdm. Companion to
[k6-fsk-05-known-vector-lab.md](k6-fsk-05-known-vector-lab.md) and
[k6-tx-next-experiments.md](k6-tx-next-experiments.md).
**RX-only checkpoint; no physical RF TX or firmware flash.**

## Actual connected equipment

- K6 responded on /dev/ttyUSB0; last status was TX idle, 7.79 V battery,
  both VFOs at 144.390 MHz (APRS).
- RTL2838 SDR and R820T tuner enumerated. librtlsdr.so.0 was installed
  and a real IQ recording succeeded, returning USB DVB kernel ownership.
- Physical 50-ohm transmitter load, rated coupler and SDR attenuation
  **were not verified**. The archived November 19 captures had explicitly
  used an antenna-to-antenna setup, not a calibrated conducted fixture.
- Serial capabilities advertised module_caps, run, ram_run and ptt,
  but **not lib_mutate**. The running Flux temporary RAM runner
  accepts only API levels 1–2; the new FSK-05 module requires API 3.
  The installation path must be characterized before any physical run.

## Receive-only RTL-SDR capture tool

tools/k6_fsk_rf_record.py records interleaved unsigned 8-bit I/Q plus a
manifest, without importing a radio transmitter, using a serial port,
or sending any K6 command. Initially fixed to 433.750 MHz receiver
center / 433.500 MHz target, 1.024 MS/s, requested fixed 0-dB gain,
no bias tee, and a maximum of 12 seconds. It refuses overwrites and
always marks the transmitter fixture as **unverified**.

An actual receive-only capture succeeded:

| Measurement | Value |
|---|---:|
| Capture duration | 4.096 s |
| Complex samples | 4,194,304 |
| Fixed gain request | 0 dB |
| Closest-channel peak above median spectral noise | 3.04 dB |
| Clipped / extreme ADC fraction | 0 |
| Frequency of maximum local spectral bin | 433.494 MHz |
| Busy/overload advisory | False |
| Dummy load / attenuation verified | **No** |
| RF transmission performed | **No** |

The 433.494-MHz peak is just the strongest *noise-bin estimate*,
not evidence of a carrier; frequency and absolute RF level are not
calibrated. A clear receiver spectrum is **not evidence of a
properly attached load**.

Recording and metadata (not committed):
/tmp/flux-fsk-05-rtl-baseline-20261122.npy
/tmp/flux-fsk-05-rtl-baseline-20261122.json

Reproduce with a fresh prefix:

    python3 tools/k6_fsk_rf_record.py \
      --out-prefix /tmp/flux-fsk05-another-baseline --seconds 4

## First-prefix sync with a truly held-out data tail

tools/k6_fsk_lab_decode.py accepts the recorded I/Q and the FSK
vector manifest. The decoder estimates burst start, near-nominal bit
clock and sampling phase using *only* the known training prefix,
and uses those measurements to decode the remaining bits without
putting tail bits into synchronization or threshold calibration.
This is stronger than full-message-assisted correlation, but is
still a known-training receiver, **not fully blind packet decoding**.

When run on the real quiet baseline, it reported no lock
(best false-match training correlation 0.2212), and did NOT invent
an empty-channel BER result.

Once a safe new RF capture exists:

    python3 tools/k6_fsk_lab_decode.py \
      --iq /path/to/new_capture.npy \
      --manifest docs/k6-fsk-lab-vector.json \
      --training-bits 512 \
      --out /tmp/flux-new-packet-analysis.json

## Replayed genuine archived FD6818B RF recordings

tools/k6_fsk_legacy_holdout.py reconstructs the previous
November 19 matrix payloads and attempts acquisition from the
*first 256 bits only*. The recovered sample clock and packet start
are not imported from the old decoder. The established SDR
frequency shift (+280375 Hz), nominal bit rate and training truth
are known a priori.

| Baud | Training errors | Independent held-out tail errors | Training correlation |
|---:|---:|---:|---:|
| 1200 | 0/256 | **0/768** | 0.998 |
| 2400 | 0/256 | **0/768** | 0.996 |
| 3600 | 2/256 | **0/256** | 0.967 |
| 4800 | 5/256 | **0/256** | 0.915 |

This establishes strong recovery of *settled data* through
4,800 bit/s in the archived conditions. It does not establish
an unbiased general-purpose BER, transmitter phase continuity,
spectral compliance, calibrated FSK deviation or a multi-level
waveform synthesizer.

Reproduce:

    python3 tools/k6_fsk_legacy_holdout.py \
      --data-dir /storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix \
      --out /tmp/flux-fsk05-legacy-verified.json \
      --training-bits 256

The JSON includes an SHA-256 of each archived IQ file. The original
capture data are not modified or checked into this research branch.

## Real hardware TX go/no-go

The user has authorized further TX experiments, but the necessary
physical test and firmware capabilities are not yet demonstrated.
Before executing the enabled FSK-05 module:

1. Physically confirm a **rated 50-ohm dummy load** directly on the
   K6 transmit path, a suitable directional coupler, sufficient
   verified attenuation and SDR input protection. Confirm power
   ratings and connector order. Do not infer this from SDR silence.
2. Verify the independent TXDOG firmware backstop and a recovery
   procedure. Build acknowledgement macros alone cannot prove that
   hardware and watchdog are safely configured.
3. Establish how to run an API-3 module. The current live RAM path
   accepts only levels 1–2, and the running serial image does not
   advertise lib_mutate. Do not flash new firmware merely to bypass
   this without recovery preparation.
4. With an operator present, inspect channel occupancy and carry out
   one short, bounded, conducted native-FSK transmit test on
   433.500 MHz. **Do not transmit the test pattern on 144.390 APRS**.
5. Measure transmitted state separation, edge phase, frame overhead,
   out-of-channel energy, TX cleanup and return to RX. Analyze the
   saved IQ with the held-out-tail decoder.
6. Then, with independently verified RF monitoring, evaluate a
   small set of REG_70 codes while leaving REG_40 unchanged until
   B-chip mapping and restoration have been established.

All alternative TX paths, including MICIN, TCXO, native 4-FSK,
tone, subaudio, PA envelope and external modulation, remain in
the master roadmap.
