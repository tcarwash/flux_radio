# FSK-12-RF: real K6 microphone-derived Sotto-1200 packets over native FSK

Date: 2026-11-23. **Frozen prospective physical experiment; NOT YET RUN.**
Research branch: `research/live-dv-integration-20261123`, isolated worktree
`/storage5/dev/flux-live-dv-integration`.

## Existing measured prerequisites

- The K6 built-in microphone capture from the other thread is physically
  recorded, 8164 samples at 7956.3395 Hz over 1.0261 s; the capture includes
  a speaker-played tone. The actual microphone fixture is private.
- [FSK-12 offline pipeline](k6-fsk12-k6mic-to-sotto-packets-offline-20261123.md)
  resamples to 8 kHz and produces **26 real 40ms Sotto-1200 frames**,
  **156 compressed bytes**, packed into **4 CRC-valid Flux L64 packets**.
  Packet reassembly and lossy host decode to 1.04s of PCM are proven offline.
- Earlier [FSK-10B](k6-fsk10b-eight-packet-blind-physical-20261123.md) sent
  **eight different prerecorded Sotto packets** over native K6 FSK, all eight
  recovered by payload-blind public frame synchronization and valid CRC.

## New prospective question

Can the same already-tested finite FSK carrier send **the four actual
K6-microphone-derived L64 packets**, and can the independent SDR recover all
four CRC-valid packets, sequences 0–3, without using their contents for
receiver synchronization?

This connects two separate demonstrations but **is not live microphone
transmission**, does **not** run a codec on the radio, and does not validate
continuous FIFO refill. It cannot substitute for a two-radio live DV test.

## Frozen transmitter and evidence boundaries

- Reuse the prior FSK-10B 4800bps finite 64-byte packet transmitter, exactly
  **4 frames x 32 FIFO words**. Native hardware modem resets/rearms between
  frames. **No FIFO appends after start** and no speculative status read.
- Frequency 433.500MHz into rated dummy load, air-coupled RTL-SDR
  center 433.750MHz / 1.024Msps, same controlled bench as earlier tests.
- Bound to **one 1900ms TX lease** and existing 700ms per-frame timeout,
  conservative total frame airtime ~0.604s. Exact radio B chip 0x4829,
  trusted firmware build, original signed module slot30 and 144.390MHz
  VFOs must match. Canonical nonblocking Flux serial-port lock acquired for
  the ENTIRE operation, including SDR capture and slot/VFO restoration.
- New source generator/driver:
  `tools/k6_fsk12_mic_rf.py` (prepares, preflights, optionally performs
  one explicitly authorized TX, and performs independent SDR analysis).
- **Actual audio, mic Sotto bytes and generated armed C source stay in private
  bench folders** and are NEVER committed to Git. No base64 payload, sample
  waveform or encoded audio is published; only cryptographic hashes and
  validation methodology are in the repository.
- Original microphone packet file SHA256:
  `d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6`.
- Source generated with original known-safe precommitted packet TX sequence:
  SHA256 `b1cf48083d990a853054a4ec36797d5c5ffbc53e0a7c9b690193d4d66131dd56`.
- Clang/thumbv6m compiled module: **1196 bytes, entry 0, API level 3**;
  SHA256 `46a7c785297f91428b09496bf251ca96fe744cf6794464e0ca23503020ffd77a`.
- Private frozen source/manifest:
  `/home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123/`.
  The script refuses to transmit if any current source, codec packet,
  manifest, compiled code, size, or active safety invariant differs.
- No automatic retries. The default CLI requires both
  `--operator-lease-confirmed` and `--dummy-load-confirmed` for RF.
  The existing bench transaction separately checks firmwares, module
  backup/readback, RX, RSSI, SDR baseline, and restores slot/VFOs.

## Pass/fail (prospective; exact and measurable)

PASS only if the bounded module reports **128** words written across four
independently framed 64-byte bursts; exactly four RF frames are found using
**only public 16x55 preamble + 076b3e36 sync at fixed 4800baud**; all
4 payloads pass Flux L64 CRC16 and whiten/sequence checking with message ID
**93**, sequence **0–3**; the recovered payloads subsequently compare bit-exact
to the private source (**0/2048 bit errors**); and original module, radio
VFOs and TX state restore and readback pass.

Any missing/extra frame, incorrect CRC/header, received bit errors,
unexpected source/code SHA, watchdog abort, incomplete slot or VFO restore,
or occupancy/lock collision is **FAIL/INCONCLUSIVE**. Preserve original
IQ/transaction output; do not auto-retry or reinterpret the test post hoc.
An RF pass still does not establish on-radio encode/decode timing, speaker
playback, PTT interaction, longer calls, live transport robustness or
two-way DV.

## Reproduce offline preparation and validation

The private source was created with:
```sh
python3 tools/k6_fsk12_mic_rf.py prepare \
 --packets /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin \
 --private-dir /home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123
```
Do **not** rerun `prepare` against an existing directory; it fails safely.

Current pinned binary checks (OFFLINE; no radio):
```sh
python3 tools/k6_fsk12_mic_rf.py preflight \
 --packets /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin \
 --private-dir /home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123
python3 -m pytest -q tests/test_k6_fsk12_mic_rf.py
```

## Operator VFO preservation update (committed before first FSK-12 RF)

The physical FSK-09S-G control was run on the same K6 bench and
restored its actual operator VFO state, **A=440.850MHz watched** and
**B=144.390MHz**, both FM/wide/nonmemory. The older generic RF harness
incorrectly assumed both VFOs were at 144.390MHz and safely aborted
before modifying the radio. FSK-12's isolated `k6_fsk_boundary_bench.py`
now refuses any initial VFO state other than this *exact observed*
baseline, temporarily tunes only the currently watched VFO for the
433.500MHz conducted RF fixture, and demands exact two-VFO
restoration/readback before declaring success. This harness change
does **not** alter the source or 1196-byte compiled TX module hashes.

## Physical test (BLOCKED until bench lease acknowledged)

**The user's broad authorization for radio flashing/TX does not override
other researchers' exclusive hardware leases.** Before physical execution
reread GitHub issue #11 for a fresh explicit bench ACK and ensure no
active conflicting hardware operation. Operator must verify dummy load and
approve this specific frozen test. Only after those gates:

```sh
python3 tools/k6_fsk12_mic_rf.py tx \
 --packets /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin \
 --private-dir /home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123 \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk12-mic/fsk12-mic-run01 \
 --dummy-load-confirmed --operator-lease-confirmed
```

Offline capture decoder after gated bench completion:
```sh
python3 tools/k6_fsk12_mic_rf.py analyze \
 --packets /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk12-mic/fsk12-mic-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk12-mic/fsk12-mic-analysis-run01.json
```

## Next engineering gates after an RF pass

1. Characterize whether live microphone PA11 capture can coexist with native
   FSK mode/TX and keypad/PTT without losing serial-control recovery.
2. Compile and bench the **Sotto encoder on actual K6 Cortex-M0**, measuring
   cycles, stack, ROM, RAM and uninterrupted sample acquisition. Host encode
   success is not MCU encode feasibility.
3. Integrate radio-owned bounded 40ms audio buffers, L64 packet scheduling
   (8 frames / 320ms per packet), RX CRC/sequence/erasure handling, and the
   existing K6 Sotto decoder + TIM1/PA11 PWM speaker output.
4. Independently demonstrate radio-received audio playback, then an actual
   half-duplex microphone-to-speaker call using two endpoints. Only later
   decide whether >64-word in-flight FIFO streaming is worth its additional
   risk; finite packet DV is already a viable candidate transport.
