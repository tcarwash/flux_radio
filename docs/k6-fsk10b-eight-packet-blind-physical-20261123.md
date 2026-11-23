# Flux FSK-10B — eight new Sotto L packets acquired blindly over native-FSK RF

Date: 2026-11-23. Branch `research/offline-sotto-fsk-dv-20261123`.
**Physical result: PASS.** Eight CRC-valid 64-byte native-FSK packets found using only standard preamble/sync, without knowing start times, voice bytes, message ID, packet sequence or expected packet count. The original K6 radio, library slot and VFOs were restored after one bounded session. This is **not** continuous FIFO refill or on-radio real-time DV.

## Prospective verification / separation from the reference data

The independently trained [blind receiver implementation](../tools/k6_fsk_sotto_sync_rx.py) was **committed before this RF capture**, commit `2178e42`. It detects only the native FSK fixed 16-bytes-of-0x55 preamble and `07 6B 3E 36` sync, then makes 512 payload-bit decisions and tries the standard Flux L-profile CRC/whitening parser. It has no access to the transmitted Sotto voice, frame positions or expected message/sequence. Its preflight controls included: recovering both previous FSK-11 CRC packets without knowing their contents, and finding two diagnostic FSK-10 frames but rejecting their invalid Sotto CRCs.

The [eight-packet, single-transmission RF protocol](k6-fsk10b-blind-eight-packet-protocol-20261123.md), exact source, compiled image and packet manifest were **committed prior to the RF run**, commit `5d4c30b`. A *different* Sotto 1200 audio sample, `female_libri198_14s__1200.bit`, was chosen from the existing codec sample archive. The TX manifest is used to freeze the source and post-hoc scoring **only**, never for reception.

## Conducted hardware result

- Radio: K6 FD6818B 0x4829, firmware build `95d6b260d4122287`, known dummy load, air-coupled RTL-SDR capture, 433.500MHz transmit, 433.750MHz SDR center, 1.024 Msps, uncalibrated amplitude/frequency.
- **Exclusive canonical Flux serial lock** — same process-scoped nonblocking `/dev/ttyUSB0` lock as the active TX16N agent; held over slot backup, single flash-free module installation, bounded TX, independent IQ capture, and restoration. No forced acquisition or automatic retry.
- Protected 1900-ms device TX lease; 32 FIFO words per finite frame, **eight separate rearmed 64-byte native-FSK frames**. Module returned **256** (8×32 words), as frozen.
- Before mutating SPI, original signed slot30 module was fingerprinted and backed up. It was restored with fresh serial + verified authenticated-envelope readback. Both VFOs returned to 144.390MHz, final TX state idle, restoration verified true. No MCU firmware flash or calibration alteration.
- Immutable armed source SHA256 `3893db74e037edaa4c55229efc19bbdbc3267fd462e344aa4f149e639c38bb83`; compiled module executable SHA256 `31ef35b4b7810544835c1ea2e105dd834236ff9cc402863fdfaf5e3a7a825c60` (1466B).

## Blind IQ receiver: all packets found and CRC-qualified

| Measurement | One prospective FSK-10B capture |
| --- | ---: |
| Packets transmitted and acquired | **8 / 8** |
| CRC-valid decoded 64-byte packets | **8 / 8** |
| Native modem bits independently reconstructed | **4,096 bits**, zero errors in post-hoc frozen-packet comparison |
| Voice codec | Real Sotto 1200, 6 bytes per 40ms frame |
| Sotto superframes recovered | **64 / 64** |
| Source audio represented | **2.56 sec** |
| Message ID independently parsed | `73` |
| Packet sequence independently parsed | `0` through `7`, in order |
| Correlation with known PHY preamble/sync (not payload) | 0.992519–0.995154 |
| Packet-start intervals, observed | **151.042–151.302ms** |
| Mean packet-start interval | **151.205ms** |
| Nonpayload modem restart, nominal | ~44.54ms per 64B frame |
| Original slot30 restoration and both VFOs | **Verified** |

The receiver used the public sync/preamble to find frame start and set the FSK decision threshold, then accepted only valid CRC16-protected data. Only *after the blind result was frozen*, the eight received packets' SHA256 digests were compared with the frozen manifest; **8/8 matched**. The reconstructed **384 actual compressed Sotto bytes** exactly matched the first 64 superframes of the female sample (`SHA256 06a4a987bd0e106ce10353c01c756eebc18ff6b849d0dc2e02e9b1d557761563`). These are favorable results on one dummy-load/air-coupled fixture, **not** statistical BER/PER or independently calibrated RF sensitivity.

## Captured arrival timestamps and buffered playback

The payload starts found by the blind receiver were:

| Sequence | Capture relative time (seconds) |
| ---: | ---: |
| 0 | 0.8221094 |
| 1 | 0.9733594 |
| 2 | 1.1246615 |
| 3 | 1.2757031 |
| 4 | 1.4269531 |
| 5 | 1.5782031 |
| 6 | 1.7295052 |
| 7 | 1.8805469 |

Using the **independently measured packet-start times**, `tools/k6_sotto_fsk_capture_playout.py` replayed CRC-accepted packets into 40-ms Sotto playback slots with a configured 160-ms initial receive buffer. **64/64 compressed voice frames were available at their deadlines, zero concealed**; a synthetic deletion of one CRC-valid data packet produces a flagged **320-ms gap** in tests rather than silent missing audio. This timing replay assumes the decoded packet is available at its last native-FSK payload bit and does not include host processing latency or new RF jitter.

The RF-recovered 384B compressed stream was decoded using the existing **host fixed-point Sotto decoder**, yielding **40,960 bytes / 2.56s** of 8-kHz signed 16-bit PCM, SHA256 `186d036e803e4c7d0c949f46e844936b5471ceee86f58e75a16a90652d811844`. PCM was **byte-for-byte identical to independently decoding the original untransmitted input sample** with the same host codec. This is offline host playback, **not** speaker playback on a K6, and no K6 microphone or DSP encoding was exercised.

## Reproduction and local evidence

Fixture folder: `/home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/`.

| Artifact | Location / SHA256 |
| --- | --- |
| Raw I/Q | `fsk10b-l8-run01.npy`, SHA256 `0da3f204fcc66a6909fdef79261003e1b954e4545293523e0564453a87ac3413` |
| Full guarded bench / restoration record | `fsk10b-l8-run01.json` |
| Payload-independent RX capture analysis, including 8 recovered 64B packets | `fsk10b-l8-blind-rx-run01.json` |
| Playback / packet arrival scheduling report | `fsk10b-l8-rf-recovered-2p56s.json` |
| Recovered Sotto bitstream | `fsk10b-l8-rf-recovered-2p56s.bit` |
| Recovered audio listening file | `fsk10b-l8-rf-recovered-2p56s.wav` SHA256 `3f86065e93ddb5ef12e3f58bb32490c3147b90cc3e44b9b1140c52543cb199e8` |

```bash
python3 tools/k6_fsk_sotto_sync_rx.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-run01.npy \
 --duration 3.0 --out /tmp/fsk10b-blind-independent.json
python3 tools/k6_sotto_fsk_capture_playout.py \
 --blind-report /tmp/fsk10b-blind-independent.json \
 --out-base /tmp/fsk10b-recovered-playout --frames 64 --buffer-ms 160 \
 --decoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_dec_fx
```

## Offline frequency offset and additive-IQ-noise replay

A separate offline-only [robustness script](../tools/k6_sotto_fsk_iq_robustness.py) runs the **same already-captured** FSK-10B IQ through digitally injected complex Gaussian noise with amplitude defined relative to the median of the clean capture in a fixed 0.85–1.83s survey window, plus a constant baseband frequency offset. The receiving algorithm remains payload-blind and unchanged. There is **no new RF emission** for this step, and the artificial noise ratios are **not calibrated radio SNR measurements**.

| Injected complex noise amplitude relative to clean IQ | Applied offset −1000Hz | 0Hz | +1000Hz |
| --- | ---: | ---: | ---: |
| 0 | 8/8 CRC-valid | 8/8 | 8/8 |
| 0.25 | **8/8** | **8/8** | **8/8** |
| 0.50 | 0/8 | 0/8 | 0/8 |
| 1.00 | 0/8 | 0/8 | 0/8 |

At 0.50 noise ratio the preamble/sync detector still found eight candidate frames, but all contained uncorrected payload bit errors (CRC rejection); at 1.00 no frames were acquired. The observed failure is therefore an **error correction/reliability issue**, not merely a sensitivity to moderate frequency-offset changes. One random realization per noise setting was tested and shared across three offsets; no confidence interval or link-budget claim follows. Full local numeric report: `fsk10b-l8-iq-robustness-v1.json` in the same evidence directory.

**Implication:** FEC/parity, CRC-gated erasure handling, a real receiver jitter buffer and more realistic channel testing remain necessary before calling this an operational digital voice mode.

## Remaining limitations and recommended next steps

1. **True mid-frame FIFO refill is still not working.** Eight finite native FSK packets require per-frame modem rearm with ~44.5ms nonpayload overhead. Concurrent research's FSK-09S physical register status probes (16/32/48-word read-only captures) did **not** identify a safe occupancy or almost-empty signal, so no appends were attempted. See `research/native-fsk-pdm:docs/k6-fsk09s-status-probe-physical-20261123.md` and shared queue #9.
2. **Real-time voice source remains missing on K6.** This eight-packet test used **prerecorded 2.56s of speech**, sent in approximately 1.2s of RF packet burst. It therefore proves short compressed packets and packet recovery, but does **not** prove microphone Sotto encoding can keep pace, nor radio speaker DAC/PCM output, jitter concealment or full duplex.
3. **Robust receiver qualification:** one clean captured physical burst, publicly known preamble/sync, self-checking CRC. Evaluate intentional frame loss, weak SNR, frequency offset, clock drift, noise-only false positives and dynamic message IDs on **new independent captures**, and clarify how packet clock is reacquired after modem restart. Existing synthetic noisy/changed header tests passed, but not a measured RF channel threshold.
4. **Next physical/application milestone:** make a bounded *live-capture-to-burst* host implementation (short microphone sample encoded into Sotto, then packet transmission) and a software receive/player; only after robust scheduling, move portions onto the K6 MCU. Preserve finite TX lease and shared serial lock throughout.

**Decision:** packetized Sotto 1200 with 64-byte finite FSK frames has graduated from simulation to physically demonstrated **blindly decoded, CRC-valid eight-packet bursts and exact offline audio reproduction**. It is a viable research foundation independent of true transparent FIFO streaming, which remains outstanding.
