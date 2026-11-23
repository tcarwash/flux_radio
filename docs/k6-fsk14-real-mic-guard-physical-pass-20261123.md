# FSK-14 — physical RF PASS: **real K6 microphone Sotto-1200 audio**

Date: 2026-11-23. **Strict physical test PASSED.** A genuine
microphone recording made by this Baofeng K6 was encoded to Sotto-1200
by a host, transmitted through the K6's native-FSK TX module, captured
as RF by an independent air-coupled RTL-SDR, fully CRC/reassembled,
and finally **host decoded as 1.04 seconds of microphone audio**.
The source was the earlier speaker-tone microphone capture, not a live
spoken on-air conversation. All original audio bytes remain PRIVATE.
This is an important DV bearer result, **NOT autonomous live on-radio DV**.

## Prospective protocol and freeze

FSK-14 was separately filed as [issue #18](https://github.com/tcarwash/flux_radio/issues/18) and frozen **before this run** at commit
[`627503c`](https://github.com/tcarwash/flux_radio/commit/627503c);
the [prospective protocol](k6-fsk14-real-microphone-guard-prospective-20261123.md)
requires exact 4-frame/4-CRC/2048-bit success and code/slot/VFO restoration.
It inherits the already RF-proven 33-word guard workaround from
[FSK-13's 8/8 RF PASS](k6-fsk13-guard33-sotto-physical-pass-20261123.md)
at commit `ecfad13`. This is an **independent second one-shot
lease/test**, not a repeat of FSK-13.

FSK-14's source is **genuine K6 microphone audio**, captured through
PA11 previously and encoded offline to 26 genuine 40ms Sotto-1200
frames (156 encoded bytes) in 4 CRC-valid 64B Flux L64 packets.
Original private 256B packet file SHA256:
`d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6`.
Source private C file SHA256:
`fc5e27797dbbd60a564a168fc848f223c668c263e307a4b72166aa24c7d1bb02`.
Compiled 1206B API3 module SHA256:
`a8ad766185df22f0731ebec245bc9a274d5c908a0d1f96bc7e59bac48c536dee`.
These match the frozen hash-only manifest; no voice payload was
committed, attached to a GitHub issue, or printed.

## Conducted physical experiment

- One user-authorized, separately claimed research-queue radio lease;
  other owners reported no active radio/SDR claim. Canonical
  `serial_lock('/dev/ttyUSB0')` held for entire transaction.
- FD6818B chip ID 0x4829, Flux build `95d6b260d4122287`,
  conducted 433.500MHz into verified dummy load,
  air-coupled RTL-SDR recording at 433.750MHz center.
- Eight-byte Sotto audio packet contents were not read by the
  independent SDR detector or used for symbol synchronization.
- Exactly **4 native-FSK physical frames**, 33 words (66 bytes) each,
  **entire FIFO preload**, independent modem rearm, 4,800bps. Original
  64-byte L64 Sotto + one sacrificial `0xA55A` last word.
- **Firmware module returned 132 = 4*33**; one single bounded invocation,
  1900ms TX watchdog, individual frame timeout 700ms.
- Original signed slot30 code `6580529c...` backed up, replaced only
  temporarily, **restored and independently readback verified**.
  Original VFO A=**440.850MHz** watched and B=**144.390MHz** were
  exactly restored in FM wide mode, final TX **off**, radio RX idle.
  Shared serial lock was released. No MCU flash, no calibration writes,
  no automatic retries.

## Independent receiver and strict predeclared acceptance

The SDR detector acquired the standard chip 16×0x55 preamble and public
sync `07 6B 3E 36` at a **fixed 4800-baud clock**. Only then
did the offline receiver test the two possible 64-byte packet positions,
offset 0 (no anomaly) and offset 2 (one extra leading FIFO word),
requiring a **unique valid CRC/whitened L64 packet**. Finally a
separately pinned score compared original packet SHA256s **after**
independent RF recovery.

| Frame | First RF word | L64 byte offset | Sync correlation | Full CRC |
| ---: | --- | ---: | ---: | --- |
| 0 | `f8 00` | 2 | 0.992458 | PASS |
| 1 | `56 bc` | 0 | 0.994819 | PASS |
| 2 | `f8 00` | 2 | 0.995574 | PASS |
| 3 | `56 bc` | 0 | 0.995961 | PASS |

**4/4 CRC-valid, zero sync errors, zero ambiguity, 0/2048 bit
errors** against the exact original 4 64-byte private source packets.
Message ID **93**, packet sequences **0,1,2,3**, codec total **26**.
Both frames with the stray `0x00F8` had their disposable final guard
displaced and their **complete original CRC-protected Sotto packets
preserved**. Scorer `STRICT_RX_PASS` with an empty list of failed
requirements. Unlike old unguarded FSK-12, this is not post-hoc
reconstruction of missing CRC bytes: **all full L64 packets arrived**.

## Independent host Sotto decode of RF-recovered voice

Additional **OFFLINE** verification (no further radio TX) used
`tools/k6_fsk_guard33_mic_playout.py` to feed the *SDR-recovered*
L64 packets to `modem.pager.Assembler`, recover all 26 6-byte Sotto
frames, and decode through the **same pinned fixed-point Sotto-1200
host decoder** SHA256
`408b4f4a8c1c5999bf66d20c72f104835a071a969fde8470dee221501121c741`.

- **26/26** compressed 40ms superframes reassembled, exactly
  **156 compressed bytes** and all source bits exact.
- Decoder returned **8,320 PCM samples at 8kHz**, **1.04 seconds**;
  wrote a private 16-bit mono WAV.
- The RF-recovered WAV bytes are **identical to the original
  host-decoded microphone recording** made before any transmission.
- Private recovered WAV SHA256:
  `097aa1bed7eafa1ed4d462d67398ae4346edb3f329a0414aeb2f0c81b00e190f`.
- **No voice bytes, WAV, packet hex or source speech data are public.**

This verifies the actual decoded audio endpoint, not merely four valid
headers.

## Privacy-preserving original evidence (no public uploads)

Root directory (mode **0700**), every file mode **0600**:
`/home/tyler/flux-bench/micvoice-20261123/fsk14-mic-guard-private-20261123/`

| Private filename | SHA256 |
| --- | --- |
| `fsk14-mic-guard-run01.npy` (raw RF IQ) | `774b67310f7962ce6e1802e46076bd90301c24a8105b2f2a80df87d180709bff` |
| `fsk14-mic-guard-run01.json` (full radio transaction) | `484320fab82f72890639b1b1c471c61f7c03598ca979d33d10a2fc069ff341e9` |
| `fsk14-mic-guard-run01-slot30-before.k6m` (signed backup) | `36bc1116b497b02e7d26780a8bc6e7c5dec41d157c5aca2aa5437720fa3c1d9b` |
| `fsk14-mic-guard-analysis-run01.json` (private RF recovered packet hex) | `6759168754e296e5d61c10dcfed04ac4d3efcefbbc5bcc3f9a8e37029217f835` |
| `fsk14-mic-guard-strict-score-run01.json` (hash-only PASS) | `d4f61fd9650f3c6eda09c8967dd61d64ada2ecf92fe1ac95d406d521f9006f6f` |

The private directory also contains
`fsk14-rf-reassembled-sotto1200.bit`,
`fsk14-rf-decoded-8k.raw`,
`fsk14-rf-decoded-8k.wav`,
`fsk14-rf-audio-replay-report.json`, all mode 0600.

Run read-only reproduction into separately created protected private
outputs (original files must never be overwritten); the frozen RF
receiver, strict scorer, and host playout tools are committed on
`research/live-dv-fifo-rootcause-20261123`. No physical device is
needed to reproduce this result.

## What FSK-14 does — and does NOT — prove

**PROVEN:** K6 PA11 mic capture → offline host Sotto-1200 encode →
real FD6818B native-FSK TX with guard →
independent SDR receive/CRC/byte-exact reconstruction →
host Sotto-1200 decode to the original reconstructed audio.

**NOT YET PROVEN:** microphone sampling while the programming cable is
connected; on-K6 incremental 10ms/40ms codec execution with real RAM,
SPI codebook and ADC timing; autonomous transmit packet queue;
radio-side FSK receive/Sotto decode/speaker audio; low-latency
conversation; full duplex or sustained half-duplex live DV.
The radio's physical MICIN/pin arrangement requires a cable-out or
standalone-CPU workflow. The native FSK frame restart still costs
nonpayload airtime and the unexplained `0x00F8` prefix remains.
Nothing here establishes sustained FIFO refill beyond the verified
64-word capacity or an almost-empty IRQ. The guard PHY tolerates only
the observed single-word insertion.

**Recommended next step:** With this PHY frozen, prioritize
Cortex-M0 incremental slim Sotto encode and packet scheduling in a
standalone module, then separately integrate on-radio FSK receive and
speaker output. Retain all private mic data and exact restore
practices for subsequent bench work.
