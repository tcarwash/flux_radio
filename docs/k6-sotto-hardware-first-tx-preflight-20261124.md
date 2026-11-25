# K6 live Sotto first physical TX — staged offline, no RF approval

2026-11-24. This is a *preflight package*, **not** a flashable production release or authorization to key the radio. It targets one controlled dummy-loaded live-microphone Sotto test after a hardware-installable image and a fresh exclusive bench lease have been verified.

## Pinned software evidence

- Sotto integration source `9330afc3a25785ee37437bb4c18aae376a111b8f`, pinned upstream Sotto `598f1bce87c22ebada2b9d0d1fcfd89107b33a09`.
- Full-feature API28–31 image with RAM0/live serial/remote UI/memory bank: **122,180 / 122,880 application bytes**, 700 B flash spare; RAM arena 14,976 B, free RAM0 88 B, stack reservation 2,796 B.
- Development image SHA256: `fb9bd4e953dd9d977e74472ad42fa69da185047651704131c6e30cdda7d3d75d`.
- Actual live TX module: 9,756 B, SHA256 `da7ccf20a63b63516e62b6c08111b297a1d0a488a02b5e50a36d426ed70e80c4`.
- Codec deck: 4,182 B, SHA256 `5d05fa9fb0194eee04356c3cb4b15471c780d0a915ee67063ed91a54e64ce9f6`.
- Paired-ARM virtual live tests: 181 passed, 2 skipped across the Sotto suite (published); 8-block replay independently rerun: 3/3 virtual RF packets recovered, no RX FIFO under/overruns, PA restored, receiver codec output host-side only. This is *not* physical RF evidence.
- Private local staging folder: `/tmp/flux-sotto-live-tx-preflight-20261124/`, with copies `firmware.bin`, `firmware.elf`, `module.bin`, `sotto-ivf.k6sc`, `manifest.json`. Do not assume /tmp survives reboot.

## Must resolve before any radio flash or on-air run

1. **Development loader**: the tested full-feature image uses CRC32-only emulator cable upload. That does not establish a production signed/SipHash module or SPI-library install, nor a suitable recovery path. A hardware-installable **keyed** image/package, verifiable module/resource install and readback, and original flash/SPI/VFO snapshots are required. The ordinary `~/.config/sotto/k6-mod.key` was absent when staged; **do not generate a production key or expose any key in a report** without deliberate provisioning.
2. **Hardware ownership**: consult the latest GitHub issue #11 claims and the canonical `~/.cache/flux/bench.lock`, and the current K6 serial lock. A legacy `k6_bench.py owner` returning “nobody” is not sufficient to prove another autonomous agent is idle. Do not touch another agent's running test or stop its serial session.
3. **Physical setup**: operator confirms rated 70-cm dummy load, SDR sampling path/isolation and legal test frequency; radio firmly connected to load, TX watchdog armed, bounded one-shot keying, no repeat-on-error.
4. **Mic/cable mux**: PA11 microphone capture conflicts with the live serial/programming cable. Upload/verify with cable present, finish serial session, remove cable, then press physical PTT deliberately. Reconnect only after TX has stopped and radio has returned to RX.
5. **Receiver is not release-ready**: the second ARM RX decodes virtual RF and *host-PC* speaker model reconstructs PCM, but **on-device Sotto decoder + TIM1/DMA playback is not qualified**. Do not present a successful TX-only test as bidirectional voice.
6. **State restoration**: preserve every preexisting library slot hash and VFO state, verify PA off and normal RX in postflight; protect backups. Stop immediately on deviation.

## Proposed single short test after sign-off

- Use a fresh authorized one-shot bench session. Confirm the radio firmware build ID, proper API31 tail, K6CB/IVF content hashes, and the live module image before PTT. Verify original contents can be restored.
- Start an independent RTL-SDR or SDRplay raw-IQ capture with headroom and no RF overload.
- Initiate one **0.32–0.8 second** physical PTT speaking interval on dummy load only (initially a short controlled stimulus). The module is not designed to transmit at install/entry and should reject absent PTT, invalid B-chip, stale mic/cable conflict or failed codec resources.
- Analyze capture blind for native 4,800-baud FSK with valid BEGIN/VOICE/END metadata, consecutive frame IDs, CRC and exact expected codec framing. A live mic result cannot be assessed as sample-for-sample audio identity without a separately recorded known input; compare waveform plausibility, frame timing, decoded PCM and queue health instead.
- Confirm no PA/FSK ownership after PTT release, normal RX and VFO/library restoration. On failure, preserve failure trace and **do not automatically retry**.

Issue #30's originally proposed six fixed FDV1 packets are a *separate* physical framing qualification (not live microphone voice). Do not silently substitute this new live-mic test for #30. Record outcomes on issue #10 and hardware experiment issue chosen for this run, with a fresh #11 lease.

## Offline SDR capture scoring (available now)

The independent scorer `tools/k6_sotto_live_capture_score.py` reads a **saved** interleaved uint8 RTL-SDR `.npy` capture (`npy-u8:`) or complex-IQ `.npy` (`npy:`). It cannot open an SDR, serial port, or transmitter. It runs the compiled Cortex-M0 FSK receiver on recorded IQ, then requires a complete, unambiguous CRC-correct FDV1 BEGIN/VOICE/END epoch before optionally decoding local host PCM. Use the **actual calibrated center frequency** (the receiver offset is not automatically corrected). Example, with placeholders for an authorized recorded session:

```sh
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_live_capture_score.py \
  --source "npy-u8:/private/captures/live-sotto.npy" \
  --rate 1024000 --center 440600000 --tune 440850000 --duration 4 \
  --json "/private/captures/live-sotto-score.json" \
  --wav "/private/captures/live-sotto-decoded.wav"
```

**Example numbers above are not a recording instruction or assurance of an SDR offset/frequency.** Do not put private microphone WAV/PCM captures in a public issue. The scorer's output validates framing and native IQ reception, not speaker HAL, true microphone fidelity, antenna/load safety, or permission to TX. The three tests in `tests/test_k6_sotto_live_capture_score.py` check a pinned six-frame public control+voice session and fail closed on corrupted, truncated and missing-END input; 3/3 passed offline.
