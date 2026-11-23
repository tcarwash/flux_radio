# FSK-10 L64 native-FSK physical bench protocol — precommitted

Date: 2026-11-23. Branch: `research/offline-sotto-fsk-dv-20261123`.
This protocol was prepared **before** the first FSK-10 hardware capture. It is a new FSK waveform test, **not** a claim that Sotto speech has been sent over RF yet.

## Question / anticipated result

Does FD6818B physically transmit two independent **64-byte** native 4,800-bps FIFO frames, under one continuously keyed TX lease, at approximately 153.386 ms between payload starts? The L transport simulation previously assumed 64 bytes / 4800 = 106.667 ms of payload plus the earlier 128-byte FSK-09's measured 46.719 ms restart/preamble overhead. That **46.719 ms at L is an extrapolation**, and could differ.

Use fixed, distinct 32-word FIFO payloads. The decoder estimates each frame's start solely from its first **256 known training bits**; its remaining **256 held-out bits per frame** are used only for scoring. Require both held-out tails match before treating the payloads as correctly received. The module does not actually encode Sotto audio; a favorable outcome would merely qualify the **physical bearer** to carry Sotto's 64-byte packet profile.

## Physical safety and expected state

- User-confirmed conducted/dummy-load K6 with an air-coupled RTL-SDR; lab uses 433.500 MHz TX, 433.750 MHz SDR center / 1.024 Msamples/s; no radiated traffic planned. RF power/emissions uncalibrated.
- **Same Flux `serial_lock('/dev/ttyUSB0')` used by active TX16N agent**, held by `tools/k6_fsk_boundary_bench.py` over entire radio+SDR transaction, not the IC-7100/hamlet bench lock. Nonblocking failure is a hard stop. No force, no lock deletion.
- Expected firmware build `95d6b260d4122287` and chip `0x4829`, RX idle, both VFOs initially 144.390 MHz. Trusted original slot-30 PDM module must match exactly, valid authenticated envelope and code bytes; backup is written *before* mutation.
- Temporarily install the frozen **two 32-word frame** module in slot 30. No main-firmware flashing or calibrations. Device-specific watchdog caps TX at 1900 ms, checks power/key/channel. Always restore original code and VFOs from exact verified bytes and read back.
- **One guarded run only**, no automatic repeat on uncertain RF/serial status. Use fresh output filenames. If the owner lock is occupied, do not transmit.

## Frozen binaries and source identifiers

- New inert default source: `c/k6/mod/fsk_sotto_l_boundary.c` (cannot key unless explicitly compiled with flags).
- Gated, exact known source: `c/k6/mod/fsk_sotto_l_boundary_armed.c`.
- Exact gated source SHA-256: `0efc961b43954dd3ad72f82558458a3c136ff3bfb79ac39878a63646b3531a93`.
- Expected compiled module code SHA-256: `1ee09901e3d224493124b58ac4bd16e992b60c43fcb061b5b394756bf7299970`; code 1128 bytes, entry zero, module API level 3.
- `REG_5D` frame-byte count: `(64 - 1) << 8`, or `0x3f00`; FIFO loads exactly **32 16-bit words per frame**. Fixed preamble/sync and UART timing otherwise inherit previously proven FSK-09 setup.
- Sample `tools/k6_fsk_sotto_l_bench.py` verifies exact source hash and compiled image hash before invoking the existing guarded transaction. The existing FSK-09 script's slot backup/readback and restoration logic remains unchanged.
- Analyzer: `tools/k6_fsk_sotto_l_analyze.py`. No radio access; it uses existing `k6_fsk_lab_decode.py`, with separate 256-bit training and 256-bit heldout sections per frame.

## Precommitted acceptance / failure criteria

1. Safe transaction must preflight and restore the original slot 30 code and both original VFOs; any restoration failure means **STOP**.
2. Firmware must report two frames of 32 words complete, result 64. SDR capture files must be preserved even when the decoder fails.
3. Each acquired frame must have independent training correlation >=0.93, no more than 4 bit errors in 256 heldout bits, and physically plausible payload-start ordering. Measure actual start gap and contrast it with 153.386 ms. Report 0-bit errors as a stronger result if achieved; no decoder false-success based on the test bits.
4. Do not claim streaming, true continuous RF phase, Sotto sound quality, or absence of interference. This test can validate *short native FSK frame reception* and *measured replay/restart timing* only.
5. A negative result is documented without silently changing acceptance criteria or retrying RF.

## Repro / run when lock becomes available

```bash
python3 -m pytest -q tests/test_k6_fsk_sotto_l_lab.py tests/test_k6_sotto_fsk_offline.py
python3 tools/k6_fsk_sotto_l_bench.py --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk10-l64/fsk10-l64-run01 --dummy-load-confirmed
python3 tools/k6_fsk_sotto_l_analyze.py --iq /home/tyler/flux-bench/tx-injection-20261122/fsk10-l64/fsk10-l64-run01.npy --out /home/tyler/flux-bench/tx-injection-20261122/fsk10-l64/fsk10-l64-analysis-run01.json
```

Physical run must be performed **only when the shared Flux lock is free**. The physical result and exact captured hashes go to the shared GitHub experiment queue before proposing longer bursts or physical Sotto packets.
