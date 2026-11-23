# Flux FSK-10B — prospective eight-packet blind Sotto L64 RF trial

Date: 2026-11-23. **Frozen before any FSK-10B physical emissions**. Branch `research/offline-sotto-fsk-dv-20261123`.

## Hypothesis

Using only native 4,800-bps 2FSK, the K6 can transmit **eight independently framed 64-byte packets** (256 FIFO words total), with approximately 151ms spacing, within a single existing bounded 1900ms TX lease. The receiving host should acquire **all eight** frame boundaries without being told frame times or voice/packet payload bits, using only the public hardware framing `0x55` x16 + `076b3e36` sync. Each independently demodulated 64-byte packet must pass the existing Flux L Sotto1200 CRC/whitening/sequence validation; 64 actual compressed Sotto 1200 superframes should then reconstruct the *first 2.56 seconds* of a different source clip (female LibriSpeech 198), byte-exact to that original compressed sample. Results must be evaluated without altering the precommitted receiver algorithm.

This is a **prerecorded, accelerated packet burst**: 2.56s of voice data transmitted as approximately 1.2s of finite RF frames, not a microphone-fed live call or a general continuous FIFO. Receiver uses known framing/symbol rate, not known payload/seq/message ID. Source material has been frozen prior to test, and may be used **only for post-test scoring**.

## Preconditions and independent acceptance criteria

- Same user-confirmed dummy-loaded K6, 433.500MHz TX, air-coupled RTL-SDR at 433.750MHz, 1.024Msps. Preflight expects `0x4829` FD6818B and firmware `95d6b260d4122287`, both VFOs at 144.390MHz, radio RX idle and original authenticated slot30 PDM code with SHA `6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`.
- **Exclusive Flux serial lock** acquired nonblocking via `tools/k6_port_lock.py`. The prior FSK-09 guarded transaction owns it across slot backup, one module load, SDR capture, short bounded TX, exact original slot restoration and signed readback, both VFOs and final TX idle. No force, no automatic retry. Other TX16 research keeps priority.
- Immutable armed module source and output code hashes confirmed by wrapper, matching C FIFO words to manifest bytes. Module config: `REG_5D=0x3f00` (64B), exactly 32 words per packet and 8 fixed packets; 1900ms independent TX lease and fresh output paths.
- **Pass**: module returns result `256`, all restore verification true, two VFOs 144.390MHz after; blind receive picks **eight** packets without any reference to `tests/data/k6/fsk10b_sotto_l8_payload_manifest.json`, all eight CRCs pass, msg ID and sequence internally consistent, and subsequent **post-hoc** comparison to frozen source yields zero bit differences. Report first-to-last packet-start intervals and total RF airtime. RX WAV from recovered compressed data is byte-exact to decoding the source on the same reference host codec.
- **Partial or negative**: record all observed CRC valid frames, acquisition confidence, missing/erased frames, unexpectedly long inter-frame gap, watchdog or restore issue without falsely claiming pass. If lock is busy, decline test. Any RX miss is evidence to analyze offline, **not** permission to silently retrain on the expected packet bytes.

## Pinned sources

- Fixed sample: `/storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/female_libri198_14s__1200.bit`, SHA256 `584cdbd7844052962ad469224812e67ebe1af52a01d008c8628fe11d1a13e4ec`.
- Exact eight CRC-valid Sotto L64 packets, msg ID 73, sequences 0..7: `tests/data/k6/fsk10b_sotto_l8_payload_manifest.json` (tx manifest, **never read by blind receiver**).
- Inert/armed C source: `c/k6/mod/fsk_sotto_l8_burst.c`, `c/k6/mod/fsk_sotto_l8_burst_armed.c`.
- **Frozen armed source SHA256:** `3893db74e037edaa4c55229efc19bbdbc3267fd462e344aa4f149e639c38bb83`.
- **Frozen expected module code SHA256:** `31ef35b4b7810544835c1ea2e105dd834236ff9cc402863fdfaf5e3a7a825c60` (1466B, level3 entry0).
- Hardware wrapper: `tools/k6_fsk_sotto_l8_bench.py`, canonical `serial_lock` and existing `k6_fsk_boundary_bench.run_transaction`.
- Frozen **payload-independent** decoder: `tools/k6_fsk_sotto_sync_rx.py`, already proven on previous FSK-11 2-packet capture (2/2 CRC valid) and separate diagnostic FSK-10 capture (0/2 false Sotto CRC), and synthetic eight unknown packets. No FSK-10B payload truth, time coordinates, message ID, or frame count is used for sync or symbol decisions.

## Safe procedure

```bash
python3 -m pytest -q tests/test_k6_fsk_sotto_l8_bench.py tests/test_k6_fsk_sotto_sync_rx.py
python3 tools/k6_fsk_sotto_l8_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_sotto_sync_rx.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-run01.npy \
 --duration 3.0 \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-blind-rx-run01.json
```

One physically guarded transaction only in this protocol; no main firmware flash or RF retries. Do not infer emissions compliance, full duplex, packet-loss rates or actual conversational audio latency from the result.
