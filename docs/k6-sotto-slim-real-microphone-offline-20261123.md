# Flux DV: Slim Sotto-1200 on real K6 microphone recording — offline result

Date: 2026-11-23. Branch `research/offline-sotto-fsk-dv-20261123`. **Offline host result: PASS** for codec + packet interoperability; no new radio/SDR/PTT operation, and no claim of on-radio encoder performance. The FSK-09S-G experiment's other agent currently owns the shared RF bench lease, and this work was done entirely on archived microphone audio.

## Why test slim instead of only the full encoder?

Earlier FSK-12 work passed with the general fixed-point Sotto-1200 host encoder, but that **full encoder is much too resource-intensive for the small K6 MCU**. The upstream [sotto slim encoder](https://github.com/baitisj/sotto) (our local `sotto/c/fixed/sotto_fx_slim.c`) is a separate implementation designed to produce the same 48-bit/40ms Sotto 1200 wire format with less computation and smaller working state. The repo's [sotto-slim.md](https://github.com/baitisj/sotto/blob/main/docs/sotto-slim.md) reports **modeled Cortex-M0 27 MHz mean/29 MHz worst** with `-Oz` and code in RAM, **3,148B encoder state, 676B stack**, **10,684B code/tables excluding external codebook**, and about 150 codebook SPI vector fetches per 40ms, modeled as approximately 5ms. The K6 firmware/module arena is about **15,408B**, making front-end buffering and codebook access a significant remaining constraint. Those estimates come from the codec repo's cycle model, **not our new physical measurement**.

The local upstream document still says the K6 microphone path is unproven; our **separate successful FD6818B microphone capture** supersedes that particular limitation, but it **does not** prove microphone sampling can run simultaneously with CPU-intensive slim encoding, or that codebook SPI accesses can be scheduled without starving the 8kHz audio sampler.

## What was actually measured

Input source: **read-only 8,320-sample, 8kHz signed 16-bit PCM** from the *real K6 PA11 microphone* recording, [FSK-12 mic conversion](k6-fsk12-k6mic-to-sotto-packets-offline-20261123.md). No new audio capture. Executed local `sotto_slim_enc` host binary, then `sotto_dec_fx -1200` fixed-point host decoder. Encoded and recovered using the unchanged `pager.profile('L','1200',depth=1,parity=False,short_last=True)` with 64-byte CRC16/PN9 packets.

| Measurement | Offline result |
| --- | ---: |
| Mic audio (resampled) | **8,320 samples, 1.04s @ 8kHz** |
| Codec | K6-oriented slim Sotto-1200, 6B per 40ms |
| Codec superframes | **26** |
| Compressed bytes | **156** |
| L64 packets | **4** |
| Packet CRC, sequencing | **4/4 pass**, msg94 seq0–3 |
| Reassembled compressed Sotto bytes | **156/156 exact** |
| Reassembled voice frames | **26/26 exact** |
| Fixed-point host decoded PCM | **8,320 samples (1.04 s)** |
| Full encoder vs slim byte-exact match | **0/26 superframes** — **EXPECTED; codecs use different quantizers** |
| New physical RF or Cortex-M0 performance test | **NONE** |

The slim codec produced compressed SHA256 `8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5`. After packet CRC/whitening assembly/disassembly, the compressed bytes are bit-exact. Slim decoded PCM SHA256 `471d241311a8caa92fa15027d1b13df85e989838e69cbc7468203453c25b2a1f`; the audio is *lossy* relative to the original microphone PCM, and this test does not measure intelligibility/quality. Slim and full codec both use the same L64 air packet format and existing fixed decoder; neither needs a wire-format change.

The actual voice, encoded bits, 256 packet bytes and WAV remain **private and local**, not committed to Git, under:

`/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/`

The file `slim-recovered-8k.wav` is available for local listening. `slim-mic-packet-report.json` contains non-reversible hashes, packet counts and explicit limitations.

New reproducible offline driver: `tools/k6_sotto_slim_k6mic_offline.py` (no radio interfaces). It refuses to overwrite a previous bench output directory. Tests `tests/test_k6_sotto_slim_k6mic_offline.py` include a synthetic 8kHz clip, CRC-negative mutation, sample-shape rejection, actual captured mic clip and non-overwrite behavior.

```bash
python3 tools/k6_sotto_slim_k6mic_offline.py \
  --pcm /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw \
  --encoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_slim_enc \
  --decoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_dec_fx \
  --out /tmp/k6mic-slim-verify --msg-id 94
python3 -m pytest -q tests/test_k6_sotto_slim_k6mic_offline.py
```

## What still blocks *live* on-radio DV?

1. **MCU execution integration**, not just codec compatibility. The slim encoder's **modeled** 27–29MHz CPU time and about 5ms/Sotto-frame SPI codebook access must be reproduced in the real K6 program under actual interrupt timing, module scratch allocation, flash and SPI contention. The 15.4KB arena is very tight: about 10.7KB code/tables +3.1KB state +0.7KB stack leaves under 1KB for audio buffering and message queue unless read-only tables move to an external library store or code is optimized.
2. **Continuous microphone sampling while encoding.** The previously proven microphone path uses a bounded autonomous PA11 sampler; it was not a ring-buffered interrupt/DMA capture concurrent with Sotto encoding. Each new 8kHz PCM sample arrives every 125µs, and 40ms means 320 samples/640 bytes. Missing a sampled interval during CPU/SPI work corrupts voice, regardless of average CPU headroom.
3. **TX FIFO streaming and RF scheduling.** Issue #9 proves one in-flight FIFO word, but sustained >64 words, multi-append exact framing and reliable B-specific headroom remain unresolved. Meanwhile our finite L64 packet mode can transport speech with ~44.5ms nonpayload restart, as previously physically measured.
4. **Actual physical mic-sourced RF validation** is separately frozen as [FSK-12 queued experiment](k6-fsk12-k6mic-private-rf-prospective-20261123.md), using the **full fixed-point encoder** output. The slim codec stream is intentionally a **separate candidate** and must get its own prospectively frozen hardware test if selected. Don't alter the full-codec pinned RF fixture or use another agent's bench lease.

**Decision:** the K6 microphone's recovered audio is compatible with the **specific slim Sotto-1200 format intended for embedded use**. Next offline priority is an MCU-side sampling/encoding/buffering feasibility plan with bounded memory and time budgets, then an actual Cortex-M0 model/profiling test and hardware trial when the queue allows.
