# Flux FSK-12 — prospective K6-microphone-origin Sotto RF qualification

Date 2026-11-23. **Status: SOURCE FROZEN / OFFLINE PASS / NO PHYSICAL TRANSMISSION.** This is a new hardware test plan, **not a lease claim**. Worktree `research/offline-sotto-fsk-dv-20261123`; shared queue issue #11. The other agent has requested a bench lease for FSK-09S-G (fully preloaded 60-word FIFO control). **We explicitly acknowledged `HARDWARE FREE` and will not touch the K6 until its lease is completed and another separately acknowledged lease is granted to FSK-12.**

## Goal and isolation from earlier tests

Earlier K6 native-FSK tests physically demonstrated **8/8 Sotto L64 packets acquired blindly with CRC 8/8** for a prerecorded external voice sample. Separately, FSK-12 host/offline work encoded the **actual 1.0261-second K6 PA11 microphone capture** (`micvoice-speech-band.wav`, sample rate 7956.3395Hz, no clipping) to **26 compressed Sotto 1200 superframes**, packed into **four 64-byte L-profile packets, all CRC-valid**, and reconstructed exact compressed Sotto bytes and 1.04s host PCM. [Offline report](k6-fsk12-k6mic-to-sotto-packets-offline-20261123.md).

This frozen experiment's one new question is: can those **four specifically microphone-originated Sotto L64 packets**, not a reference sound clip, survive one bounded, dummy-loaded K6 native-FSK RF transfer, be discovered by the host SDR with **only public native-FSK preamble/sync** and decoded to the same compressed speech/host PCM?

## Privacy and source immutability

**Actual spoken/audio/compressed voice bytes and C source embedding those bytes remain in the local private bench directory** `/home/tyler/flux-bench/micvoice-20261123/fsk12-private-20261123/`, outside the Git repository. Do **not** commit the generated `fsk12-k6mic-armed.c` or `fsk12-k6mic-inert.c`, `.bit`, `.wav`, `.raw` or unredacted receiver JSON to the public project. Git contains only:

- `tools/k6_fsk12_private_fixture.py`, an offline source generator based on the already-physically-demonstrated **FSK-11 four-word-register sequence**, carefully adapting it to four 32-word frames.
- `tests/data/k6/fsk12_k6mic_public_fingerprint.json`, with SHA256 commitments, **not voice/payload bytes**.
- `tools/k6_fsk12_mic_bench.py`, an opt-in exactly one-RF-transaction harness with three explicit user/queue gates and a mandatory **canonical nonblocking `serial_lock('/dev/ttyUSB0')`** in the existing proven FSK-09 `run_transaction`, which makes/restores the signed slot30 module with readback, verifies VFOs and retains SDR I/Q.
- `tools/k6_fsk12_mic_rx.py`, a **hardware-unaware, archived-IQ-only receiver** using the known 4800-baud modem symbol rate and the public `16 × 0x55` preamble followed by `07 6B 3E 36`, never using expected microphone bytes for symbol decisions. CRC-accepted packet hashes and the assembled compressed speech hash are compared **only after RF recovery**. Optionally produces local PCM WAV using fixed-point Sotto decoder.
- `tests/test_k6_fsk12_private_mic.py`: source/hash/CRC/C-word matching tests, negative output tests, and a 4-packet synthetic IQ positive and a 1-packet corruption/CRC rejection control. Synthetic signal tests are **not RF qualification**.

**Frozen public SHA-256 gates** (all full digests are in `tests/data/k6/fsk12_k6mic_public_fingerprint.json`):

| Artifact | SHA256 / property |
| --- | --- |
| Four source L64 packets, 256 bytes | `d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6` |
| Compressed Sotto superframes, 156 bytes | `60c8478cf6da655a62dd2317104d79ea30a6f655f73545fb152dd87d49a59ad3` |
| Private armed C source | `82920dfdc5fde037d70b553f332eb9ad7fec55a8475ab571fc0a34faa6199368` |
| Compiled Cortex-M0+ module, 1206 bytes | `ec3143edf9b0c55fcbfae6384eff6457b05459af289317a44929f87320ad401b` |
| Existing FSK-11 source template | `77444b8c0c795141034b2cf1579db9f78b37cf9ce2d2f6631c95858286ee01b4` |
| Packet message ID / sequence | 93 / 0–3 |
| Actual source voice superframes | 26 × 40ms, 1.04s after 111-sample end padding |

`tools/k6_fsk12_mic_bench.py::frozen_build()` confirms **ALL** hashes match at runtime, exact little-endian FIFO words in private C match each committed packet hash, every packet CRC and sequential header validates, and the compiled temporary radio module is byte-identical to the prospectively frozen digest. The *raw audio* is never loaded by the bench TX step, only four pinned compressed packets. Future tests may not substitute voice content or a modified RF module without a new frozen protocol.

## Prospective safety and acceptance criteria

1. First require a **separate explicit ACK of the FSK-12 exclusive radio/SDR bench lease in issue #11** following the FSK-09S-G test, plus current dummy-load/operator confirmation. Never force/retry a busy serial lock. The runner refuses to operate without all three flags `--dummy-load-confirmed --operator-lease-confirmed --queue-lease-acknowledged`; all flags OFF by default. Merely being able to open the USB port does not constitute a lease.
2. Same previously proven RF setup: FD6818B chip ID `0x4829`, build `95d6b260d4122287`, both RX VFOs at 144.390MHz idle and original trusted signed slot30 module; one short 433.500MHz dummy-load TX, independent RTL-SDR IQ at 433.750MHz/1.024Msps. Reuse existing preflight RSSI, restored signer serial, trusted original module and original VFO readback, air-coupled SDR channel occupancy preflight, no main flash, no calibration writes.
3. Exactly **four separately rearmed 64-byte native FSK frames** under **one** 1900ms TX lease, 700ms per-frame timeout, final module result `4 × 32 = 128`. FSK baud set 4800, programmed 32 words/frame; finite-FSK modem restart ~44.5ms (earlier physical measurement), **no continuous refill** attempt. The radio must return to TX OFF/RX idle and exact original signed slot/VFO before scoring any RF result.
4. Independent IQ decoder must discover **four** public-synced frames, recover all 512 raw payload bits each *without* using expected voice bytes, and accept **exactly 4 CRC-valid L64 packets**, message ID 93, seq0–3, 26 total codec frames. **Post-hoc** compare each recovered packet's SHA with the frozen hash list; require all four matches. Reassembly must produce **26/26 valid genuine Sotto 1200 superframes**, exact compressed-source SHA. Host decoder PCM is expected to match the separate offline decoder output byte-for-byte, but **lossy codec PCM is NOT supposed to equal original microphone PCM**.
5. A missing frame, CRC reject, hash mismatch, unexpected radio state, lock busy, module timeout or any restoration issue is **negative/inconclusive**; save IQ and transaction evidence privately, do not retry without a fresh queue entry. Even full success would prove **a short prerecorded K6-microphone clip transported over K6 native-FSK and decoded on host**, NOT a K6 real-time mic encoder, speaker playout, two-radio contact or indefinite FIFO stream.

## Offline preflight and future RF commands (DO NOT EXECUTE WITHOUT LEASE)

```bash
# Safe offline verification only; creates no RF activity
python3 -m pytest -q tests/test_k6_fsk12_private_mic.py
python3 -c 'from tools.k6_fsk12_mic_bench import frozen_build;print(frozen_build())'

# FUTURE single RF attempt ONLY AFTER the shared queue ACK & fixture checks:
python3 tools/k6_fsk12_mic_bench.py \
 --prefix /home/tyler/flux-bench/micvoice-20261123/fsk12-private-20261123/fsk12-mic-rf-run01 \
 --dummy-load-confirmed --operator-lease-confirmed --queue-lease-acknowledged

# Analysis from IQ only, may be repeated without radio (new prefix each time):
python3 tools/k6_fsk12_mic_rx.py \
 --iq /home/tyler/flux-bench/micvoice-20261123/fsk12-private-20261123/fsk12-mic-rf-run01.npy \
 --out-base /home/tyler/flux-bench/micvoice-20261123/fsk12-private-20261123/fsk12-mic-rx-run01 \
 --decoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_dec_fx
```

**Status:** all source/hash/packet/synthetic IQ validations are offline, with zero new hardware operations. The radio belongs to the active other agent's FSK-09S-G experiment until acknowledged released. The FSK-12 physical result is currently **NOT RUN**.
