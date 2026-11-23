# Flux FSK-13 — prospective guard-word Sotto L64 RF trial

2026-11-23. **FROZEN BEFORE RF; physical run not yet performed.**

## Goal and hypothesis

Previous radio runs show an intermittent unexpected leading 16-bit word
`0x00F8` on FD6818B native FSK. Fully preloaded FSK-09S-G and four
microphone-derived Sotto FSK-12 frames all reproduced it; the physical
payload length remained fixed, displacing the last two bytes including
the L64 CRC. Conversely FSK-10B successfully transmitted **8/8** original
Sotto L64 packets with a nearly identical control sequence.

**New physical-layer mitigation hypothesis:** Program an **extra 16-bit
guard word** in the RFIC's finite frame:

- On nominal behavior, RF yields `64-byte L64 packet || 5a a5 guard`.
- On the observed *one-word leading artifact*, RF yields
  `f8 00 || complete 64-byte L64 packet`, displacing the guard.
- An independent SDR decoder receives all **66 physical bytes**, finds
  modem public preamble/sync, then tries CRC/whitening of exactly two
  *66-byte-local* 64-byte windows: byte offset **0** and byte offset **2**.
  It accepts **only if exactly one** L64 candidate validates by CRC and
  structural checks, and marks any ambiguity/failure. It is never given
  the expected audio bytes to acquire packets.

This is **a workaround, not a silicon root-cause claim**. The artifact
could change or occur twice. This does not prove real-time DV or indefinite
FIFO streaming.

## Frozen source, safety, and radio configuration

- Isolated branch `research/live-dv-fifo-rootcause-20261123`,
  worktree `/storage5/dev/flux-live-dv-fifo-rootcause`.
- Transmitter: `c/k6/mod/fsk_guard33_sotto.c` (inert default);
  armed `c/k6/mod/fsk_guard33_sotto_armed.c`.
- Armed source SHA256:
  `332ee375001bccf688b30db390dee12311b132c4b63d53ff7d06f0f831c2af0a`.
- Armed API3 module: **1478 bytes**, code SHA256:
  `cd4dd68674658cdf5a9b44cc40af32f2679d9f1c09580017beebd4def1a86295`.
- Original eight 64-byte L64 packets are **byte-for-byte unchanged** from
  previously RF-proven FSK-10B and separately CRC validated in offline
  tests; one new last `0xA55A` word added per frame, never inside L64.
- Program `REG_5D=0x4100` (**66-byte frame**), 33-word full preload,
  **8 finite frames**. Constant 4800bps native FSK, 433.500MHz conducted,
  preamble `16*0x55`, public sync `076b3e36`, gain code 21, same
  `REG_59=00F8/80F8/00F8/08F8` control as passing FSK-10B.
- **No in-flight writes or occupancy assumptions**. 33 < physically
  demonstrated FIFO preload capacity 64. One short 1900-ms TX lease,
  individual 700-ms completion timeout, no automatic retry.
- Host tool: `tools/k6_fsk_guard33_bench.py` refuses TX without **both**
  confirmed dummy load and an independently ACKed hardware lease,
  verifies source and code hashes, compiles same signed API3 format and
  delegates to canonical nonblocking Flux serial lock + slot30 backup,
  radio/SDR preflight and exact signed module/VFO restoration.
- Receiver: `tools/k6_fsk_guardword_rx.py`, public framing only,
  **frozen 4800baud clock** (no payload-assisted re-timing); no expected
  packet bytes used in acquisition or CRC-based offset selection.
- Original operator VFO A **440.850MHz** (watched), B **144.390MHz**
  both wide FM; restore them exactly after one test and verify TX off.

## Strict prospective PASS/FAIL

To pass **finite packet delivery despite either prefix state**:

1. Exclusive queue lease ACK, no other active owners, canonical lock and
   expected firmware 0x4829/build/slot30 snapshot. Physical dummy load
   and SDR RF attenuation/preflight independently verified.
2. Exactly one run, **module return 264 (8*33 words)**, eight bounded
   66-byte FSK frames, untouched 64-byte source packets, TX off after.
3. Receiver must locate exactly **eight** RF frames from public sync
   with 0 sync-bit errors at 4800bps; **8/8** unique CRC-valid L64 packets
   at **only one** byte offset 0 or 2 per physical frame, msg_id=73
   sequence 0–7, all original frozen packet SHA256s recovered
   **byte-for-byte**, no other candidates. BER over recovered original
   packets **0/4096 bits**.
4. No false positives from filler/sync; private real-mic voice not used.
   Every raw IQ/metadata/source hash and decoded offset must be recorded.
5. Original authenticated slot30 module and both VFOs independently
   readback restored, TX disabled, lease explicitly released.

Missing any requirement: **FAIL/INCONCLUSIVE**; preserve raw IQ and
original metadata, do not repeat automatically. Offset=2 accepts an
actual CRC-valid full 64-byte packet because the guard absorbs the
displaced word; it is not the old invalid post-hoc recovery of truncated
packet tail. It is a new **precommitted** physical format/receiver test.

## Offline preflight (no radio)

```sh
python3 -m pytest -q \
  tests/test_k6_fsk_guardword.py \
  tests/test_k6_fsk_guard33_bench.py \
  tests/test_k6_fsk_sotto_sync_rx.py
```

## Physical invocation (only with recorded queue lease ACK)

```sh
python3 tools/k6_fsk_guard33_bench.py \
  --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-run01 \
  --dummy-load-confirmed --operator-lease-confirmed
python3 tools/k6_fsk_guardword_rx.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-run01.npy \
  --out /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-analysis-run01.json
python3 tools/k6_fsk_guard33_score.py \
  --rx /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-analysis-run01.json \
  --out /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-strict-score-run01.json
```

## Private K6 microphone follow-on (OFFLINE prepared)

The real K6 microphone sample has independently produced **four CRC-valid
Sotto-1200 L64 packets** under existing private bench storage. The separate
`tools/k6_fsk_guard33_mic_private.py` generator checks the recorded
256-byte packet file's SHA256 and CRC, then constructs **four 33-word
guarded FSK frames**, with the same 1900ms watchdog and register/control
sequence. It emits source and a hash-only manifest **outside Git**, with
private output permissions, and does **not** touch RF hardware.

The current private offline build contains a **1206-byte API3 compiled
module**, original private mic packet SHA256
`d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6`,
generated source SHA256
`fc5e27797dbbd60a564a168fc848f223c668c263e307a4b72166aa24c7d1bb02`,
compiled code SHA256
`a8ad766185df22f0731ebec245bc9a274d5c908a0d1f96bc7e59bac48c536dee`.
The real voice packet bytes, C arrays and encoded voice remain **private**.
This prepares a *future separately authorized* mic-originated RF test after
the public eight-frame guard-word experiment qualifies the physical format.

A successful public FSK-13 run would establish only finite packetized DV
**transport** under this specific observed one-word insertion/absence
behavior. Native capture/real-time encode, PTT orchestration, jitter
scheduling, radio-side receiver and speaker playback require separate testing.
