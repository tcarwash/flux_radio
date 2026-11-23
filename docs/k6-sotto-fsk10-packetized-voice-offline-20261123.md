# Flux FSK-10 — offline packetized Sotto 1200 digital-voice transport study

Date: 2026-11-23
Branch: `research/offline-sotto-fsk-dv-20261123`
Scope: **OFFLINE ONLY**. No serial, SDR, RF transmissions, source firmware flashing, or edits to the active bench worktree.
Tracking: [Flux #10](https://github.com/tcarwash/flux_radio/issues/10); see also [shared queue #11](https://github.com/tcarwash/flux_radio/issues/11).

## Physical Sotto packet pass (2026-11-23, FSK-11)

**Important new milestone:** Two actual **Sotto 1200 L/64B CRC-protected packet bytes** were transmitted on the dummy-loaded K6's native 4,800-bps FSK modem. The air-coupled SDR decoder recovered both packets bit-exact (0/1024 errors), independently validated both CRCs and sequences, reconstructed 16 genuine Sotto frames, and decoded 640ms of speech to **the exact original PCM** in host software. Read the separate [FSK-11 physical report](k6-fsk11-sotto-over-native-fsk-physical-20261123.md). This does **not** establish blind packet acquisition, long streaming, two-way conversational audio or live on-radio encoder/decoder scheduling.

## Physical L64 timing correction (2026-11-23)

**FSK-10 physical runs now establish an L64 measured start-to-start interval of 151.042ms**, with 44.375ms nonpayload framing/rearm, **0/256 heldout bit errors per frame in four physically captured 64-byte frames**. The prior L = 153.386ms estimate and ~613ms delay throughout this original offline report are **historical extrapolations**, now superseded. See [FSK-10 physical evidence](k6-fsk10-l64-physical-20261123.md).

The committed simulator now sets `RF_RESTART_S_L=0.044375`, `RF_RESTART_S_XL=0.046719`, and predicts **~611ms capture-to-playout delay for L without parity** (160ms configured jitter buffer) or **~1,231ms** for L + depth2 parity with 500ms buffer. These are still simulations, **not a demonstrated voice link**. The two physical repeats used diagnostic fixed payload, not actual Sotto bitstream; long-run packet acquisition remains unproven.

## Result and recommendation

**Sotto 1200 is a plausible compressed voice codec for native-FSK packet DV.** We use its *actual encoded* 48-bit/40-ms superframes (1,200 bps), not fabricated audio or uncompressed PCM, and reuse the existing `modem/pager.py` CRC, PN9, packet header, seq, 1-/2-/4-/8-way interleave and XOR parity machinery.

**Best first experimental options:**

- **Responsiveness:** native-FSK **L** packet (64 bytes, 8 Sotto frames = 320 ms voice), interleave 1, no parity, **160 ms receiver jitter buffer**. Predicted capture-to-playback delay about **613 ms**, provided the unmeasured L-size native-FSK PHY works with the extrapolated 46.719-ms per-frame restart.
- **Resilience:** L, interleave 2 and one XOR parity packet per two data packets, **500 ms receiver buffer**. Predicted delay **1,233 ms**; at simulated 5% independent packet erasures with 0–150 ms extra network delay, **99.61%** of codec frames arrived or were recovered before playback across 200 deterministic 15-second trials.
- **Physically anchored but slower:** native-FSK **XL** packet (128 bytes, 20 Sotto frames = 800 ms voice), interleave 1, 160-ms jitter buffer. Predicted delay **1,200 ms**. The XL airtime model uses the *measured* 128-byte frame timing; sustained transmission and received CRC/packet acquisition remain unverified.

**No native Sotto-1200 transmission or decode through the physical RFIC has been demonstrated here.** This is a host-side transport simulation, not an end-to-end voice/audio quality test. In particular, the 64-byte L radio-frame restart measurement is an **extrapolation** from the proven 128-byte native-FSK frame. The model uses idealized independent frame reacquisition, with frame loss supplied as an exogenous variable.

## Source material and exact timing

- Real sample: `/storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/male_libri3436_15s__1200.bit`.
- Sample length: **2,250 bytes = 375 true Sotto 1200 frames = 15.0 s of speech**.
- SHA-256: `6c3b4ac8905d287ff4b2e52cc49b7d97b31ed664e4aa69dcb6d2cfe5997b2d02`.
- Sotto source checkout: `598f1bce87c22ebada2b9d0d1fcfd89107b33a09`.
- Test script does **not** copy the binary into the Flux repository. Standalone self-contained tests also use synthetic 6-byte slots for deterministic protocol regression, distinct from actual codec validation.
- Physical FSK-09: two **128-byte native 2-FSK** payload frames, 4,800 raw bps, **260.052 ms** between payload starts; 128×8/4800 = 213.333 ms carrying data plus **46.719 ms non-payload** modulation/rearm between starts. That measurement is *two frames only*. See `docs/k6-fsk-09-fifo-boundary.md`.
- In the model, each packet's native data length is equal to its L/XL `pager.Profile` payload length. Non-payload 46.719 ms is added once per frame. Thus **L = 64×8/4800 + .046719 = .153386 s** (unmeasured extrapolation); **XL = 128×8/4800 + .046719 = .260052 s** (from a measured adjacent-frame interval).
- If the real RFIC has additional fixed header/preamble or independent RX acquisition costs that vary with frame length, both the L timing and startup predictions will change.

## How the simulator works

Code: `tools/k6_sotto_fsk_offline.py`
Tests: `tests/test_k6_sotto_fsk_offline.py`

1. Load raw Sotto 1200 6-byte compressed frames (or test-only synthetic 6-byte slots).
2. Use **existing Flux `modem.pager.profile(..., "1200")`** to generate L/XL bytes with whitening, magic, header, sequence number, CRC-16 and optional XOR parity. No replacement or undocumented over-the-air protocol invented.
3. Model the source as generating one Sotto superframe every 40 ms, and holding each RF packet until the **last compressed frame needed for it exists**.
4. Serialize whole packets through a nominal 4,800-bps native-FSK bearer, with the observed XL restart cost (or explicitly extrapolated L cost), no overlap. RX packets are assumed perfectly reacquired if not erased; the model does not predict BER or sync slips.
5. Inject independent erasures, CRC-invalid packets, duplicates, and random *additional* delivery latency uniformly in 0–150 ms. These are chosen trial parameters, not measured channel distributions.
6. Use the **existing `pager.Assembler`** to validate CRC, sequence, data order and XOR group repair. The jitter buffer plays 40-ms compressed frames on absolute deadlines. A missing/bad/late frame is explicitly flagged for *future speech concealment*; audio synthesis/PLC quality is not modeled.
7. Startup/playout time is anchored to the first expected packet's encoder-ready time plus first RF duration, fixed 20-ms delivery cost and the configured jitter buffer. Lost first packets do not shift that schedule (no artificially hiding erasures).

Packet loss is modeled as whole-packet erasure, not equivalent to independent bit errors. A 40-ms voice frame is marked recoverable only if its complete parent packet is present and CRC-valid, or rebuilt from parity before its playback deadline.

## Single 15-second sample, no erased packets, 0–150-ms extra jitter

| Transport | Frames per packet | RF packets | Assumed RF airtime / 15s | Approx. capture-to-playback delay | Underruns with 160-ms buffer |
| --- | ---: | ---: | ---: | ---: | ---: |
| XL / depth 1 / no parity | 20 (800 ms speech) | 19 | 4.941 s (32.94%) | 1,200 ms | 0 |
| **L / depth 1 / no parity** | 8 (320 ms speech) | 47 | 7.209 s (48.06%) | **613 ms** | 0 |
| L / depth 2 / XOR parity, 500-ms buffer | 8 / data packet | 71 | 10.890 s (72.60%) | **1,233 ms** | 0 for zero-loss trials |

For depth 2 parity, 500 ms buffer replaces 160 ms; the table's header applies to the first two rows. The air-time fraction is just summed modem-frame airtime divided by source speech duration; real TX duty, keying, acquisition and end-to-end CPU costs remain unmeasured.

## 200 seeded 15-second Monte Carlo trials per configuration

Every row uses the *same real 375-frame Sotto 1200 sample*, random seeds 0–199, 0–150 ms additional per-packet jitter, and **independent packet erasures** at the indicated probability. The estimates are simulation outputs, not confidence bounds or field BER estimates.

| Transport / buffer | Packet loss | Mean frames available before playout | Perfect 15-s trials | Max single concealment in 200 trials |
| --- | ---: | ---: | ---: | ---: |
| XL d1, 160 ms | 0% | 100.00% | 200/200 | 0 ms |
| XL d1, 160 ms | 5% | 95.21% | 74/200 | 1,600 ms |
| XL d1, 160 ms | 10% | 90.88% | 28/200 | 2,400 ms |
| XL d1, **80 ms** | 0% | **96.81%** | 0/200 | 80 ms |
| XL d2 + parity, 700 ms | 5% | **99.52%** | 186/200 | 1,600 ms |
| XL d2 + parity, 150 ms | 5% | 93.05% | 0/200 | 1,600 ms |
| XL d4 + parity, 1,300 ms | 5% | 98.97% | 178/200 | 80 ms |
| **L d1, 160 ms** | 0% | **100.00%** | 200/200 | 0 ms |
| L d1, 160 ms | 5% | 95.22% | 28/200 | 960 ms |
| L d1, 160 ms | 10% | 90.80% | 3/200 | 960 ms |
| L d1, **80 ms** | 0% | **91.98%** | 0/200 | 80 ms |
| **L d2 + parity, 500 ms** | **5%** | **99.61%** | 172/200 | 640 ms |
| L d2 + parity, 300 ms | 5% | 98.93% | 29/200 | 640 ms |

**Interpretation:** L packets cost more fixed framing overhead but incur **320-ms** gaps when one packet is erased instead of XL's **800-ms**. Depth-2 parity usually reconstructs a single missing packet in a group, but cannot correct missing parity plus data, two missing data packets, or late parity. Enough initial buffering is essential; with too little buffer a packet may be repairable **after** audio's deadline. Depth 4/8 interleaving increases latency and is not a good first conversational setting.

In all validated zero-loss test paths, input Sotto compressed bytes reconstructed via `pager.Assembler` matched the input exactly; corrupted CRC packets were rejected rather than emitted as false speech. No actual PCM decoder, listening test or RF I/Q decoder was included.

## Reproduce

From the isolated Flux branch worktree:

```bash
python3 -m pytest -q -rs tests/test_k6_sotto_fsk_offline.py
python3 tools/k6_sotto_fsk_offline.py --sotto-file \
  /storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/male_libri3436_15s__1200.bit \
  --payload XL --depth 1 --buffer-ms 160 --jitter-ms 150
python3 tools/k6_sotto_fsk_offline.py --sotto-file \
  /storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/male_libri3436_15s__1200.bit \
  --payload L --depth 1 --buffer-ms 160 --jitter-ms 150
python3 tools/k6_sotto_fsk_offline.py --sotto-file \
  /storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/male_libri3436_15s__1200.bit \
  --payload L --depth 2 --parity --buffer-ms 500 --jitter-ms 150 --loss .05
```

Monte Carlo procedure: for each configuration above, call `simulate(source, payload=..., depth=..., parity=..., playout_buffer_ms=..., jitter_ms=150, loss_probability=..., seed=i)` for `i in range(200)`; report mean `frame_good_fraction` and the fraction with `concealed_frames == 0`. The source hash above guards reproducibility.

## Engineering gates before any live-DV claim

- **First physical priority:** confirm nominal 64-byte **native 4,800-bps** RF frame and per-frame rearm/acquisition timing on a leased dummy-loaded K6. The L predictions depend on this missing measurement; do not silently inherit the 128-byte interval as established.
- Independently RX-decode *varying* random CRC-valid Sotto packets through RF captures; held-out BER and loss distribution must be determined without receiver training/test leakage.
- Verify the receiver can detect/CRC-check L and XL packet framing at this native 4,800-bps PHY; legacy Sotto pager F/1800/1200 transport is **not** proof that native FSK-10's 4,800-bps bearer has the same RX reliability.
- Re-evaluate buffering with true measured service timing, oscillator/sync acquisition, MCU scheduling, duty-cycle constraints, inter-frame gaps and real packet losses.
- Demonstrate Sotto 1200 encoder + decoder playback, PLC strategy and latency including M0/host queueing. The 613-ms prediction is **not microphone-to-speaker hardware latency**.
- If 64-byte native-FSK cadence is worse than assumed, compare 128B XL, alternative compact packet formats with corresponding measured RF timing, and store-forward.
- Keep radio tests deferred until an explicit hardware lease; do not touch current TX-15/TX-16 tests.

**Decision:** Proceed with a **two-mode Sotto 1200 DV packet transport**, default L + shallow buffer for best latency, optional L + d2 XOR parity and deeper buffer for difficult channels. These modes are promising **simulation candidates**, not deployed, interoperable, or RF-proven features.
