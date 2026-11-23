# FSK-13 — guard-word Sotto native-FSK RF PASS (FD6818B)

Date: 2026-11-23. **Physical RF result: STRICT PASS for eight finite
guarded Sotto L64 packets**, not continuous live DV.

## Predeclared procedure and result

Transmitter and frozen source SHA256 were committed **before RF** as
[FSK-13 protocol](k6-fsk13-guard33-sotto-prospective-20261123.md)
(commit `c37fc66`), receiver phase selection at `4bc18d7`, independent
strict whole-session scorer at `47de35b`. The operator expressly
authorized one physical run under shared queue #11. Other research
threads' most recent status showed no active K6/SDR lease; a fresh
`/dev/ttyUSB0` fuser/process check was empty. The bench held the
**canonical nonblocking Flux serial lock** for the guarded transaction.

The B-chip K6 0x4829, firmware build `95d6b260d4122287`, transmitted
at **433.500MHz** to an air-coupled RTL-SDR with confirmed dummy load
and the existing bench watchdog. No MCU firmware flashed. Slot30
authenticated module was backed up before installing the temporary
1478-byte API3 transmitter. The module returned **264 = 8 x 33
physical FIFO words**; one guarded invocation only.

Eight complete 66-byte native-FSK frames were independently acquired
using the **fixed public 16x0x55 preamble + 076b3e36 sync**. Modem baud
was frozen at 4800bps; there was no expected-voice-data training. For
each frame, a unique existing Flux L64 CRC-valid packet was located at
byte offset 0 or 2; ambiguity and CRC failure reject the frame.

**Prospectively required 8/8 pass: achieved.**

| Frame | RX first physical word (LE) | L64 offset | Sync correlation | Result |
| ---: | --- | ---: | ---: | --- |
| 0 | `f8 00` | 2 | 0.992564 | CRC pass, exact |
| 1 | `56 a8` | 0 | 0.995123 | CRC pass, exact |
| 2 | `f8 00` | 2 | 0.995240 | CRC pass, exact |
| 3 | `56 a8` | 0 | 0.995674 | CRC pass, exact |
| 4 | `f8 00` | 2 | 0.994380 | CRC pass, exact |
| 5 | `56 a8` | 0 | 0.995796 | CRC pass, exact |
| 6 | `f8 00` | 2 | 0.995979 | CRC pass, exact |
| 7 | `56 a8` | 0 | 0.994717 | CRC pass, exact |

All eight sync-bit-error counts were **zero**. Exact original Sotto
message ID **73**, sequence **0–7**, eight codec frames per packet,
all **eight SHA256s equal the immutable original source packet hashes**.
Total Sotto L64 payload data recovered: **4,096/4,096 bits exact**.
Receiver: **8/8 unique CRC-valid**, **0 ambiguous**, strict scorer
`STRICT_RX_PASS`, **no failed criteria**.

Of the eight RF frames, **four exhibited the unexpected extra 0x00F8
control-valued leading FIFO word**. In all four, the 33rd FIFO word was
the sacrificial guard that was displaced past the declared RF length,
so the full *64-byte CRC-protected voice packet* remained intact.
The other four frames transmitted the original 64-byte packet starting
at offset zero, followed by the guard.

This is genuine PHY format **mitigation**, not a retrospective read of
truncated L64 data and not a claim to have fixed the underlying silicon
startup problem. The periodic every-other-frame pattern is a new
interesting observation but causation is not established.

The older unguarded FSK-12 mic-origin test failed 0/4 CRC because its
packet's 64-byte physical frame lacked space for displaced CRC tail.
FSK-13 solves **that known one-word displacement case** within these
eight radio frames. It does not prove robustness against a larger
insertion, RF noise, loss, out-of-order packets or other silicon states.

## Preservation and restoration

Hardware transaction evidence confirms:

- Original signed module slot30 fingerprint:
  `6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`.
- Original `.k6m` signed backup created and verified. Original code
  restored with authenticated envelope/slot readback `verified=true`.
- Initial watched VFO A **440.850MHz**, B **144.390MHz**.
  Final exact same A/B, both wide FM, `watching=0`.
- Radio `transmitting=false`, `standby_idle=true`, RX idle.
- 1900ms guarded hardware TX lease. Canonical serial lock released.
- No automatic retries, firmware flash or permanent SPI alteration.
- The compiled module still says `fsk10B: eight packets sent` in its
  *cosmetic report string*, because FSK-13 intentionally retained the
  original radio-proven Sotto L64 transmitter template; source and
  compiled hashes plus return **264** identify the FSK-13 experiment.

## Original bench artifacts (read-only, never overwrite)

Location: `/home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/`

- `fsk13-guard33-run01.npy`, raw IQ:
  SHA256 `71113f0fdebf8927a1c78bc340b74de1c95e316e3cb78bcbb8dbf296681349e8`.
- `fsk13-guard33-run01.json`, signed slot, radio/SDR
  transaction, restoration: SHA256
  `17024f5d6a490ab69ed58b248f30daacf31c340dc4b1750ffaf38db560c88b38`.
- `fsk13-guard33-run01-slot30-before.k6m`, signed original
  slot backup: SHA256
  `ccdfb65f2e50447fc4ca26aee6e6a8b5ef2a0f2a184f5dbc56fc1e6068f38e39`.
- `fsk13-guard33-analysis-run01.json`, public-framing/CRC analysis:
  SHA256 `9e3a5b677e2d4e598b7461e3e73e9831697a0f09acca05598ead5e154f3420fd`.
- `fsk13-guard33-strict-score-run01.json`, independently frozen
  strict eight-packet scorer: SHA256
  `a432506ef7f0cd6774f9d6ad240e07c7012bbb2f0ea6644ef3f6f0dfdc37c526`.

RF transmitter source SHA256
`332ee375001bccf688b30db390dee12311b132c4b63d53ff7d06f0f831c2af0a`.
Compiled code SHA256
`cd4dd68674658cdf5a9b44cc40af32f2679d9f1c09580017beebd4def1a86295`.

Reproduce offline without hardware:

```sh
python3 tools/k6_fsk_guardword_rx.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-run01.npy \
 --out /tmp/fsk13-rf-public-reanalysis.json
python3 tools/k6_fsk_guard33_score.py \
 --rx /tmp/fsk13-rf-public-reanalysis.json \
 --out /tmp/fsk13-rf-strict-rescore.json
```

## Where this leaves live DV

**Proven:** a reversible native-FSK radio transmitter carries eight
Sotto-1200 L64 speech packets intact over RF under both presently
observed prefix conditions; independent SDR restores exact digital
voice bytes. This removes the immediate single-word framing failure
for finite packets *under the tested conditions*.

**Next separately queued gates:** genuine *K6 microphone-derived*
four-packet Sotto RF with the same guarded physical frame, then measured
incremental 10ms slim on-MCU encoding/packet scheduling,
independent RF receive/decode and K6 speaker audio, and eventually a
sustained half-duplex two-way conversation. FIFO >64-word streaming,
reliable IRQ/headroom and the origin of `0x00F8` remain unresolved.
