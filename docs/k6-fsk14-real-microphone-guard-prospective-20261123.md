# FSK-14 — prospective **real K6 microphone Sotto** guarded native-FSK RF test

Date 2026-11-23. **FREEZE THIS FILE, SOURCE HASHES AND SCORER BEFORE RF.**
Physical execution status on initial commit: **NOT RUN**.
Project [Flux shared RF research queue #11](https://github.com/tcarwash/flux_radio/issues/11).
Separate one-shot hardware reservation required; preceding FSK-13 lease
**released after strict 8/8 physical PASS** ([measured report](k6-fsk13-guard33-sotto-physical-pass-20261123.md)).

## Scientific question

Do the **four real physical K6 microphone-derived** Sotto-1200 packets
survive real native-FSK RF using the newly validated sacrificial one-word
frame guard, even when the FD6818B inserts the unexpected `0x00F8`
first payload word?

Earlier physical FSK-12 (the same private voice bytes, **un**guarded
64-byte PHY payload) detected 4 RF frames but delivered **0/4 CRC valid**:
the inserted word consumed the last two bytes including CRC. FSK-13
fixed *this particular problem* for eight public Sotto packets by using
66-byte RF frames = **64 packet bytes + 2 disposable guard bytes**.
All 8 FSK-13 packets were byte-identical, including 4 prefix-anomaly
and 4 nominal cases, and source/signed module/VFO were restored.

FSK-14 has a **different original microphone packet fixture** and an
independently frozen 4-frame source. Successful FSK-13 does NOT by
itself prove mic-derived packet RF.

## Frozen physical configuration, security and privacy

- B-chip ID `0x4829`, firmware build `95d6b260d4122287`.
- Preceding source: real PA11 captured K6 microphone samples,
  independently resampled to 8kHz and host-encoded using Sotto-1200.
  **26 authentic compressed 40ms superframes = 1.04sec encoded audio**
  in **four CRC-valid L64 packets**; original private 256B packet SHA256
  `d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6`.
- Real microphone audio/encoded packet bytes/source C data **never
  checked into public Git** or emitted in public reports. The
  hash-pinned private C and manifest are under mode-0700:
  `/home/tyler/flux-bench/micvoice-20261123/fsk13-mic-guard-private-20261123/`.
- Source SHA256:
  `fc5e27797dbbd60a564a168fc848f223c668c263e307a4b72166aa24c7d1bb02`.
- Compiled private API3 module SHA256:
  `a8ad766185df22f0731ebec245bc9a274d5c908a0d1f96bc7e59bac48c536dee`,
  **1206 bytes**, entry zero. All source and compiled code SHA
  checked again **immediately before any SPI mutation**.
- `REG_5D=0x4100` means 66-byte native-FSK frame, 33 FIFO words
  entirely **preloaded before TX**, four independently rearmed frames.
  Each starts with the **original** 64-byte mic L64 packet, then the
  spare last 16-bit `0xA55A` guard. Whole original packet plus CRC
  must remain present whether RF emits `packet || guard` or
  `0x00F8 || packet`.
- Standard PHY: 4800bps native 2FSK, 433.500MHz into user-confirmed
  dummy load; chip 16×55 preamble, hardware 4-byte `076b3e36` sync;
  same register configuration as physically proven FSK-13.
- Bounded **1900ms** independent TX watchdog, **700ms per-frame**
  timeout, no in-flight FIFO writes, no destructive FIFO reads, no
  speculative chip IRQ/AE, no automatic retries.
- Bench tool: `tools/k6_fsk_guard33_mic_bench.py` requires
  **operator-lease-confirmed**, **dummy-load-confirmed** and private
  output directory mode-0700, then invokes the shared
  `tools/k6_fsk_boundary_bench.py` signed slot30 full transaction.
  This uses canonical **nonblocking** Flux `serial_lock('/dev/ttyUSB0')`.
- Operator watched VFO A=**440.850MHz** / VFO B=**144.390MHz**, wide FM;
  save/readback/restore exact state before release. No MCU firmware
  flash or unapproved calibration writes.
- SDR IQ and independent receiver JSON **contain private microphone
  RF/voice bytes**, so store under private mode-0700 directory, NOT
  under the public research IQ directory or GitHub.

## Independent predeclared RF PASS (ALL required)

1. Fresh shared-queue hardware-lease approval/owner coordination,
   canonical serial-port lock acquired; no competing SDR/radio users;
   correct radio firmware/chip/slot30 original signed module,
   watched VFOs, standalone RX idle, operator dummy load confirmed.
2. **Exactly one** armed module invocation, report **132 (4x33)
   physical words**, no watchdog violation, no extra radio TX.
3. Offline public-sync-only SDR receiver at a **fixed, frozen 4800bps**
   finds **exactly four 66-byte RF frames** with **zero sync bit errors**.
   It searches for a CRC-valid Sotto L64 packet at only byte offsets
   zero and two. **Exactly one valid offset per frame**, zero ambiguous.
   Crucially, no original voice data, expected packet bytes or expected
   hashes are consulted during sync/acquisition or CRC selection.
4. Must recover **4/4 full L64 packets** with
   msg_id **93**, seq **0,1,2,3**, total **26** Sotto frames, 8 packet
   slots, **all 4 exact SHA256 hashes matching the source AFTER
   reception**. This equals **0/2048 original packet bit errors**.
   Any missing tail/CRC, wrong frame length/sequence, retry or
   expected-data-based synchronization = **FAIL**.
5. Trusted original slot30 module restored with signed envelope
   readback; original A/B VFO state exact; TX off, standby RX idle.
   Preserve IQ/metadata and release lease even on RF failure.

A **PASS** is precisely microphone-captured digital speech **host
encoded, radio transmitted, SDR received and independently validated**
(offline playback possible), not live on-MCU encoding or radio
speaker playout. It does not prove ≥64-word FIFO refill, continuous
DV, radio-to-radio interoperability, or root cause of `0x00F8`.

## Tools and strictly bounded procedure

Precommit frozen safety/scorer tests:

```sh
python3 -m pytest -q \
  tests/test_k6_fsk_guard33_mic_private.py \
  tests/test_k6_fsk_guard33_mic_bench.py \
  tests/test_k6_fsk_guard33_score.py \
  tests/test_k6_fsk_guardword.py
```

**ONLY with a separately recorded and approved FSK-14 radio/SDR
lease**, operator-confident dummy load and exclusive canonical lock:

```sh
PRIV=/home/tyler/flux-bench/micvoice-20261123/fsk14-mic-guard-private-20261123
install -d -m 700 "$PRIV"
python3 tools/k6_fsk_guard33_mic_bench.py \
  --prefix "$PRIV/fsk14-mic-guard-run01" \
  --dummy-load-confirmed --operator-lease-confirmed
python3 tools/k6_fsk_guardword_rx.py \
  --iq "$PRIV/fsk14-mic-guard-run01.npy" \
  --out "$PRIV/fsk14-mic-guard-analysis-run01.json"
python3 tools/k6_fsk_guard33_mic_score.py \
  --rx "$PRIV/fsk14-mic-guard-analysis-run01.json" \
  --out "$PRIV/fsk14-mic-guard-strict-score-run01.json"
```

Afterward verify all output files remain private, capture hashes and
strict verdict; post only hash/count/timing results publicly. Do **not**
auto-proceed to additional TX or flash without another dedicated lease.
