# Flux FSK-05 — Live RF pickup and native-FSK enablement checkpoint

Date: 2026-11-22. Branch: research/native-fsk-pdm.
Related: [guarded FSK lab](k6-fsk-05-known-vector-lab.md), [RX preflight](k6-fsk-05-rx-preflight.md), [all-path TX roadmap](k6-tx-next-experiments.md).

## Outcome

**One real, bounded RF transmission succeeded:** 400 ms of ordinary FM carrier at 433.500 MHz using the existing firmware-leased PTT API into the user-confirmed dummy load. The RTL-SDR received it **air-coupled**, not through a calibrated coupler. This verifies the pickup, timing and TX de-key path, **not** native-FSK waveform synthesis.

**No native FSK waveform was transmitted.** The single attempt to run installed slot 23, FSK LAB TX, was refused by the live firmware: "Unsupported command, address or frequency." That is a module launch refusal, not a diagnosis of the RF waveform. No flash or library mutation happened.

## Current radio and module

- K6 connected at /dev/ttyUSB0; FD6818B chip previously confirmed 0x4829.
- Live firmware ID f5c33782022b4016, module API max 26, FSK RX service **not advertised**, cable-only loader.
- V1 features include RUN, PTT, RAM_RUN, module capabilities and bulk reads; library mutation **not advertised**.
- Packed library slot 23 contains the 440-byte API-15 FSK LAB TX image. Installed code SHA256: a86e69318a3087ba6af326cbc5cb1a00079bd0e9ecf42fbe2dd0f3e1acecd65c. Local current source compiled to 392 bytes rather than the installed 416-byte code region, so the exact installed implementation is not source-verified.
- Both VFOs started on 144.390 MHz, and both were restored to 144.390 MHz after each operation. No test packet was transmitted on APRS.

The slot-23 attempt passed known firmware identity, channel idle, a temporary VFO tune to 433.500 MHz and RTL-SDR preflight. The firmware immediately rejected RUN and the radio remained TX idle. Although the FSK callback is advertised as unavailable, **the exact RUN rejection needs independent tracing**. Do not assert it was caused by the callback absent proof it reached the module.

## One bounded FM RF transmission: actual measurements

A separate single, short 400-ms TX used firmware-leased PTT, a 433.500-MHz tuned VFO, and a 5.12-second RTL-SDR recording at 1.024 MS/s centered on 433.750 MHz. Receiver gain requested 0 dB. The signal was obvious and fell to the noise floor afterward.

| Measurement | Observed |
|---|---:|
| Commanded PTT duration | 400 ms |
| SDR recording duration | 5.12 s |
| Spectral activity in 50-ms windows | approx. 0.85–1.25 s |
| Peak spectral contrast above nearby median | **53.13 dB** |
| SDR peak frequency (uncalibrated) | **433,469,500 Hz** |
| Requested nominal frequency | 433,500,000 Hz |
| Combined K6/SDR apparent offset | −30,500 Hz |
| Extreme/clipped ADC sample fraction | 0.0 |
| Baseline and post-capture | quiet |
| Radio dekeyed | **yes** |
| Both VFOs restored to 144.390 MHz | **yes** |

The approximately −30.5 kHz combined offset matches the previous Nov 19 SDR observations but **does not establish K6 transmitter frequency error** without independently calibrating the SDR. The 50-ms activity windows are not a precise key-up time measurement. Air-coupled SDR is not a calibrated absolute power meter, harmonic test or regulatory emission-mask instrument.

The offline analyzer tools/k6_fsk_rf_activity.py and its five passing
tests/test_k6_fsk_rf_activity.py make this capture analysis
reproducible. The analyzer replayed the original saved I/Q and measured
**53.183 dB** peak relative spectral contrast, 433,469,500 Hz
receiver peak, relative activity from 0.85 to 1.25 s, zero clipping,
and the identical SHA-256. Replay with a new output filename:

    python3 tools/k6_fsk_rf_activity.py \
      --iq /home/tyler/flux-bench/fsk05-slot23-20261122/ptt-carrier-receiver.npy \
      --out /tmp/flux-fsk-05-carrier-replay.json

The real replay result was also saved locally at
/home/tyler/flux-bench/fsk05-slot23-20261122/ptt-carrier-reproduced.json.

Original captures and analysis, saved on tyler-ldesktop outside Git:

- /home/tyler/flux-bench/fsk05-slot23-20261122/slot23-receiver.npy (refused FSK module attempt) and matching .json
- /home/tyler/flux-bench/fsk05-slot23-20261122/ptt-carrier-receiver.npy (actual FM RF transmission) and matching .json
- /home/tyler/flux-bench/fsk05-slot23-20261122/ptt-carrier-analysis.json

Actual FM RF capture SHA256:
a5289938970d2b6b9e4c1163651c1fc402fdb2c891fc3f409a95a27424c6282b

## Candidate firmware built offline — NOT FLASHED

An initial rebuild attempted in the old research tree encountered previously generated Aura API-26 overlays and an older module-level patch that expected API 16/17. The safe solution was a clean detached source worktree:

/storage5/dev/flux-fsk-candidate-clean

It cloned the pinned baseline Aura firmware and used the pre-existing local Sotto fixed-point library sources. Candidate profile:

- K6_LIVE_SERIAL=1, K6_LEAN_TXDOG=1, K6_FSK_RX_SERVICE=1, K6_CONFIG_FSK=1
- K6_SCAN_SERVICE=0, K6_USER_STORE=1, K6_RAM0=stack
- K6_SERIAL_TRANSPORT=1, K6_NATIVE_FM=0, K6_FONT_SUBSET=1
- Features: blowfang-host,fd6818b,lean,spilib,txdog-quiet

Build **succeeded**, 105,580 bytes, under the 122,880-byte application limit.

Candidate binary:
/home/tyler/flux-bench/fsk05-slot23-20261122/candidate-clean-out/flux-fsk-lab-candidate.bin

SHA256:
79174261706b8a615b40686a1df5933dc620accb29baec3f37b58787f262d222

Build log:
/home/tyler/flux-bench/fsk05-slot23-20261122/candidate-clean-build-retry.log

**This is a compile/size candidate only**. It is not a proven drop-in replacement: the running firmware advertises API 26 and the candidate profile omits some current features, including the experimental MICIN API-26 option. The first broad live-control emulator run timed out with a failure when the packed-library simulator setting was omitted. A corrected, targeted compiled-image rerun using K6_SIM_LIVE_PACKED=1 **passed 2/2 tests**: live status/tuning and a synthetic higher-API module launch. Additional compiled CAT and v2 checks ran without assertion failures. After installing Flask in a temporary /tmp target, the host live-control suite passed **45/45 tests**. The new relative RF analysis also passed **5/5 tests**. These checks are not full feature-equivalence proof, a physical watchdog acceptance test, or a native FSK RF test. No firmware or module was installed.

## Next RF experiment, preserving alternatives

1. Confirm an FSK-capable image that preserves current user-facing features and its independent watchdog/recovery path. Isolate why live RUN returned INVALID separately from the missing FSK service.
2. Verify candidate in emulator and recovery, then ensure known slot/image/code identity before considering a flash. No silent radio update.
3. With the user-confirmed loaded RF path and attended setup, perform one short controlled **native-FSK** packet and record air-coupled IQ, independent RX confirmation and dekey. The 64-word generated FSK-05 vector is still inert-by-default and has not been run on RF.
4. Run the training-prefix / held-out decoder, measure frequency plateaus and phase transitions, then vary a characterized REG_70 gain code. Leave REG_40 alone until verified.
5. Keep MICIN, TCXO, native 4-FSK, tone oscillator, subaudio, digital I2S, PA gating, external DAC/SDR and the other documented TX entry points in the master queue.

**Bottom line:** bounded real TX and receiver pickup now verified; native waveform synthesis remains untested on this firmware.
