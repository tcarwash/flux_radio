# Flux FSK-11 — real Sotto voice bytes transmitted and recovered through native FSK

Date: November 23, 2026. Branch: `research/offline-sotto-fsk-dv-20261123`.
**Status: PHYSICAL PASS — prerecorded Sotto payload RF transport and offline decode, not a live microphone-to-speaker digital voice link.**

Precommitted, frozen test protocol/source at commit `29017a6`: [FSK-11 protocol](k6-fsk11-sotto-payload-rf-protocol-20261123.md). Earlier [FSK-10 L64 physical measurement](k6-fsk10-l64-physical-20261123.md) established the 64-byte PHY restart interval separately before this payload test.

## What actually happened

The K6 FD6818B (chip 0x4829), build `95d6b260d4122287`, transmitted **two existing Flux L-profile, CRC-16-protected 64-byte packets** holding **16 Sotto-1200 superframes** (6 compressed bytes per 40-ms frame; 640 ms of source speech). The two 64-byte FIFO payload frames were transmitted under a single 1900ms firmware TX watchdog using native 4800-bps binary FSK. No microphone, real-time Sotto encoder, K6 digital voice receiver or endless FIFO stream was involved.

The module's compile hash, source hash, user-confirmed dummy-load flag, current signed slot30 code, firmware, RX VFO settings and channel status were checked before mutation. The **same Flux `serial_lock('/dev/ttyUSB0')`** as the active TX16N research agent was held from serial preflight through RF IQ capture and restoration. Only **one** FSK-11 test transaction was run; it completed with result 64, two frames, and independent verified restoration of signed original slot30 code and both original 144.390-MHz VFOs. Final TX off and RX idle. No main firmware flash.

## Independent SDR payload and packet checks

The air-coupled RTL-SDR recorded I/Q centered on 433.750 MHz, RF dummy-load TX at 433.500 MHz. The offline receiver separately acquired each 512-bit frame using the **first 256 bits only as its known training/pilot sequence**; independent 256-bit heldout halves were then scored. All 512 recovered bits for each packet were independently reconstructed by frequency-symbol decisions, **not copied from the manifest**, and passed through the **existing Flux L profile CRC, whitening, message ID and sequence parser**.

| Physical measurement | FSK-11 result |
| --- | ---: |
| Native raw bit rate | 4,800 bits/s |
| Finite hardware frames sent / received | 2 / 2 |
| Bytes per frame | 64 |
| Packet 0 training correlation | 0.993784 |
| Packet 1 training correlation | 0.996151 |
| Training and heldout BER | **0 errors / 1024 total received bits** |
| CRC-16 validity for reconstructed bytes | **Both pass** |
| Packet message ID / sequences | 33 / 0, 1 |
| Genuine Sotto 1200 encoded voice superframes recovered | **16 / 16** |
| Source speech represented | 640 ms |
| Payload-start separation | **151.250 ms** |
| Non-payload modem restart time | **44.583 ms** |
| Original signed slot and VFOs restored | **Verified** |

This L restart is within approximately one native bit period of the diagnostic FSK-10's independent two-run measurement (151.042ms, 44.375ms overhead). These are adjacent-frame captures only, and the receiver relied on known training; this is *not* blind packet acquisition, packet error-rate characterization over realistic RF, live voice streaming or a full transmitter duty cycle test.

## Actual PCM decoder confirmation

The 128 **physically decoded packet bytes** were passed through `pager.Assembler`, which reconstructed 16 true compressed Sotto voice superframes (96 bytes). The resulting compressed bitstream was **byte-for-byte identical** to the first 640ms of the pinned source sample. We ran the existing host Sotto fixed-point decoder (`sotto/c/fixed/sotto_dec_fx -1200`): 96 compressed bytes decoded to **10,240 bytes of 8-kHz signed 16-bit PCM**, equivalent to **0.64 seconds**. The PCM is **byte-for-byte identical** to decoding the original pretransmission compressed bits on the same decoder.

This verifies real Sotto decode from the recovered on-air data stream in host software. It does not prove the K6 can encode/decode at real-time speed, implement RF frame acquisition or play synchronized audio without underruns.

## Evidence and reproducibility

Local bench folder: `/home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/`.

| Artifact | SHA-256 |
| --- | --- |
| `fsk11-sotto-run01.npy` — independent SDR capture | `2a2c9bd037b740d55938db89351f15c9955ed1e81159c68b99bfa13a77ef19c5` |
| `fsk11-rf-recovered-640ms-1200.bit` — reconstructed 96B compressed Sotto | `a03155ee6e2d8bc132904569e09732d6c29e6bee11be69e6ffc1eb3ca05940c4` |
| `fsk11-rf-recovered-640ms-8k.raw` — decoded PCM | `6a4e462d3cc43b04653e4b36681af2f7a1454372ecc0f34c67819266270505d5` |
| `fsk11-rf-recovered-640ms-8k.wav` — WAV listening file | `093354d476c7633b7c127a603a2da7c60cb1f51f1cfbb0ce5d0fd8ba789f1d66` |

Evidence: `fsk11-sotto-run01.json` (contains every hardware preflight/restoration and module readback), `fsk11-sotto-analysis-run01.json` (received packet bytes, per-frame RF acquisition, CRC, sequence and BER results). Pinned codec source SHA `6c3b4ac8905d287ff4b2e52cc49b7d97b31ed664e4aa69dcb6d2cfe5997b2d02`, source `sotto/docs/samples/rates/male_libri3436_15s__1200.bit`. Frozen armed module code SHA-256 `2f042fe965d30f0972b80dc61166d03b887902bc556c7557f0434ac7deeb0224`.

Re-analyze saved IQ, no hardware necessary:

```bash
python3 tools/k6_fsk_sotto_payload_analyze.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/fsk11-sotto-run01.npy \
  --out /tmp/fsk11-sotto-independent-reanalysis.json
python3 -m pytest -q tests/test_k6_fsk_sotto_payload.py tests/test_k6_fsk_sotto_l_lab.py tests/test_k6_sotto_fsk_offline.py
```

## Next gates / current limitations

- Send longer sequences of **variable real Sotto packets** with a receiver that discovers sync and checks CRC from IQ without being given the packet truth for training, not just two controlled frames.
- Measure packet loss, clock acquisition, CPU work and audio playout including real microphone Sotto capture and decoder on the K6, and engineer a bounded jitter buffer, sequence aging, FEC and concealment.
- Transport remains **burst-FSK with a real 44–45ms nonpayload restart**; native-FSK FIFO refill (issue #9) and arbitrary RF waveform generation are independent research challenges.
- Capture IQ is from a dummy-loaded, air-coupled fixture, not a calibrated RF emissions/sensitivity/compliance or over-the-air range measurement.

**Bottom line:** FSK-11 moves us from an offline Sotto scheduler simulation to actual 128 bytes of CRC-valid Sotto packet transport over native K6 RF, recovered and decoded offline to the exact original PCM for a 640ms clip. It does not yet move us to a usable live two-way DV radio.
