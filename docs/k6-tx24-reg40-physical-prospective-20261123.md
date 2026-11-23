# TX-24 / FSK-03C — frozen prospective REG40 deviation RF protocol

**Status at freeze, 2026-11-23: PREPARED OFFLINE; NO TX24 PHYSICAL RF.**
[Queue issue #17](https://github.com/tcarwash/flux_radio/issues/17);
[bench coordination issue #11](https://github.com/tcarwash/flux_radio/issues/11).
See [source audit](k6-tx24-reg40-modulation-vector-offline-20261123.md).

**Resource blocker when written:** a higher-priority FSK-13 Sotto agent
posted an active OPERATOR APPROVED LEASE START in issue #11. Even if the
operating-system serial lock becomes momentarily free, TX24 must yield
until that lease is explicitly RELEASED and a **new exclusive TX24
bench lease is explicitly ACKed**. No manual Claim UI exists yet.
This document and GitHub queue comments do NOT grant a radio lease.

## Falsifiable question

Does changing only REG40 low8 on the FD6818B B-chip while the carrier
is keyed and REG71 tone source / REG70 gain stay fixed cause repeatable
FM deviation scale changes? Test on the user's verified conducted dummy
load, SDR air-coupled. It is *not* independent IQ synthesis, a signed
PLL-offset DAC or RF amplitude control.

## Frozen source and one-shot parameters

- Expected chip ID **0x4829**, Flux live firmware build
  **95d6b260d4122287**, cable-only API3, original signed slot30 code
  SHA256 **6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522**.
- The current operator baseline is **watched VFO A 440.850 MHz**
  and **VFO B 144.390 MHz**, both wide FM, memory disabled, TX idle.
  Any deviation at start is a refusal, not permission to retune it.
- Armed source: c/k6/mod/tx24_reg40_tone_armed.c
  SHA256 **de786e08e3bc8d6c1c57a2f53be7d55c77deb1f7ca3babbe79e9c103bb40360e**.
- Underlying guarded probe c/k6/mod/tx24_reg40_tone_probe.c
  SHA256 **be3496e0d2dbbe63cc86fabe34e8275d1eff667d652262972305f7181c16d9d4**.
- Compiled B-chip API3 module: **808 bytes**, SHA256
  **e792a8bd77d2f055a16f9e1da025a4f7a4555b6dbb9d3417817612e5e21024f0**.
- Modulation source **REG71=11264** (~1091 Hz);
  REG70 **0x9800 gain24** held constant;
  only REG40[7:0] changed, upper byte permanently **0x35**.
  Expected original REG40 before mutation **0x3516**; otherwise fail
  closed with no deviation or tone writes.
- Hardcoded **433.500 MHz conducted RF**, SDR center 433.750 MHz,
  1.024M complex samples/s, gain 0, raw IQ saved. Lease **1800 ms**,
  initial clear-channel check, 80-ms tone preparation lead,
  **13 × 90-ms states**, then 85-ms non-scored tone-off tail.
- Hex REG40 low8 sequence:
  **16, 40, 16, 80, 16, 00, 16, 40, 16, 80, 16, 00, 16**.
  Slots **0 and 2** are the only training pilots.
  The module returns **13** on full completion; any unexpected result
  is negative/inconclusive, never an invitation to retry.
- No REG59/5F/72 FIFO changes, no APC REG36, no PLL retune while keyed,
  no MCU firmware flash or permanent calibration writes.

## Frozen offline receiver scoring

Analysis file: tools/k6_tx24_reg40_deviation_analyze.py.

1. Detect raw RF-IQ carrier onset from a fixed SDR amplitude gate.
   Use existing Flux complex-IQ FM-discriminator extraction.
2. From only the two reference slots 0/2, fit source audio frequency
   nominal ±4 Hz, common phase and reference FM deviation amplitude.
   Separate their DC frequency offsets. All test slots use that
   same **frozen pilot** frequency and amplitude.
3. Discard a 19-ms settling margin at each edge. Fit a fixed sinusoid
   at the frozen source frequency plus DC within every heldout slot
   to measure FM deviation magnitude, phase and residual.
4. **Locked engineering positive criterion**: median deviation
   monotone **00 < 16 < 40 < 80**, each adjacent difference
   ≥3.5% reference; reference-amplitude spread ≤15% and
   heldout reference phase RMS ≤18°; repeated nonreference
   levels agree within 15%; each nonreference settled
   amplitude-to-noise ≥4.
5. Publish every per-slot observation even if a test fails.
   A nonlinear, muted, phase-reset, latched, distorted or
   non-monotonic response is a scientifically useful *negative
   under this protocol*. Any later alternative scorer is
   exploratory on this recording until validated on new emissions.

This is RF-frequency-deviation control, **not RF power,
regulatory spectral-mask compliance, independently controlled
carrier-phase absolute commands or an IQ DAC**.

## Physical safety/coordination gate — currently blocked

Before executing the one-shot physical harness:

1. Review newest issues #11, #9, #15, #10, #16 and #17. Confirm the
   FSK-13 operator-approved lease has an explicit LEASE RELEASE
   (not just a free USB device). Record a NEW operator-approved,
   exclusive TX24 K6/SDR lease ACK in #11 and ensure all other
   active owners yield. This GitHub ACK is coordination evidence,
   NOT an operating-system mutex.
2. Confirm the dummy load, air-coupled SDR, independent firmware
   watchdog, recorded baseline VFOs and chip build. Do not infer
   safe hardware from old photos or RSSI alone.
3. Use the canonical nonblocking k6_port_lock.serial_lock('/dev/ttyUSB0')
   for the **entire serial + SDR + SPI + radio restore** transaction.
   The old IC-7100 k6_bench.py lock is NOT the K6 mutex.
4. Exact source and compiled executable hashes, chip/firmware,
   original signed slot30 image and monotonic serials are
   checked **before writing anything**. Refuse existing IQ,
   receipt and slot-backup filenames; never overwrite.
5. Back up exact original authenticated module slot30 before
   one temporary install. Tune only watched VFO A to 433.500 MHz,
   verify VFO B remains 144.390, check channel RSSI and
   independent SDR non-overloaded baseline, then capture once.
6. If module completion is missing, do NOT retry. Confirm TX
   is off before touching flash, and only roll back a positively
   identified temporary code image. Never overwrite an image
   changed by a third party. Restore original signed slot30 and
   VFO A 440.850/B 144.390 in wide FM, verify readback/idle.
   A restoration failure blocks ALL further bench actions.
7. Preserve raw IQ, complete JSON transaction receipt and
   saved original signed envelope with SHA256. Run the unchanged
   read-only analysis once to a new JSON name and post both
   positive or negative findings to #17 and #11; release
   the queue lease after verified full radio restoration.

After independently recorded ACK, the guarded invocation is:

    cd /storage5/dev/flux-tx24-reg40-deviation-offline
    BASE=~/flux-bench/tx-injection-20261122/tx24-reg40
    python3 tools/k6_tx24_reg40_rf_bench.py \
      --prefix "$BASE/tx24-reg40-run01" \
      --dummy-load-confirmed --operator-lease-confirmed
    python3 tools/k6_tx24_reg40_deviation_analyze.py \
      --iq "$BASE/tx24-reg40-run01.npy" \
      --out "$BASE/tx24-reg40-run01-locked-v1.json"

**Do not run these commands before a separately acknowledged TX24
lease.** Passing emulator tests or compiling RF-enabled code is
not permission to transmit.

## Offline acceptance before freeze

    PYTHONPATH=/tmp/flux-fsklab-unicorn-deps python3 -m pytest -q \
      tests/test_k6_tx24_reg40_rf_bench.py \
      tests/test_k6_tx24_reg40_tone_probe.py \
      tests/test_k6_tx24_reg40_deviation_analyze.py

Missing dummy-load or lease-confirmation flags must refuse **before any
serial connection or SDR access**. Old-FW / unexpected VFO baseline
must refuse before module mutation. Module source/emulator tests
verify 13 writes, exact upper-byte persistence and register restoration
after both success and user-key abort.

The scientific state remains **OFFLINE PREPARED; RF BEHAVIOR UNKNOWN**.
