# Flux FDV1 guarded digital-voice talk sessions — target-emulator and offline IQ qualification

Date: 2026-11-23. Branch `research/dv-guard33-rx-playout-20261123`.
**Status: protocol prototype, verified OFFLINE ONLY. Not RF-proven and not flashable.**
No radio, SDR device, PTT, SPI, serial, firmware flash, live microphone,
or physical RF bench lease was used in this work.

## Problem and compatibility boundary

FSK-13 and FSK-14 physically proved **66-byte guarded native-FSK Sotto L64
voice** at 4800baud, with a spurious `0x00F8` word preceding alternate
payloads, safely displacing the sacrificial `0xA55A` guard. FSK-14 was
one fixed ID93 with seq0..3; the new Cortex-M0 bounded live packetizer uses
**ID112..115 with seq0 in each 320ms segment** (last packet can be short).
The earlier receiver required a human-supplied start ID, end ID, and
lost last-packet length. Missing the end could accidentally look like a
complete, shorter conversation if the user guessed wrong.

FDV1 adds **two new control packets**, one before speech and one after the
last speech packet. Voice bytes and original Flux `0xA9` Sotto packet
layout are **unchanged**. FDV1 control is deliberately under **0xE2**,
which differs from established Flux/K6 voice (`A7/A9/AC/AD`), settings
(`A8`), firmware/module transfer (`AA/AB/AE/AF`), and card-air
(`D6`) magic. Older voice-only receivers ignore unknown controls; they
do **not** acquire FDV1 session metadata or claim to play a streamed call.
Neither real B RF delivery nor old firmware behavior with E2 is yet qualified.

## Wire format (PROPOSED, not official K6 standard)

Every control is an unmodified-length **64-byte** PN9-whitened packet with
CRC16-CCITT-FALSE big-endian bytes62..63; a **16-bit LE guard** `5a a5`
is appended (33 FIFO words, 66 RF data bytes). Preceded by the already
physically verified public 16x`55` preamble and `07 6b 3e 36` sync at
nominal 4800baud. A physical `f8 00` prepend can be tolerated by independently
CRC-validating the two possible 64-byte windows at offset 0 or 2, choosing
**exactly one** candidate. No known-transmitted content enters selection.

| Byte(s) | Before whitening | Notes |
|---|---|---|
| 0 | `E2` | New FDV1 control magic, voice remains `A9` |
| 1 | `01` | Version 1 |
| 2 | `01` BEGIN or `02` END | Other codes rejected |
| 3 | `00` | Flags reserved |
| 4..11 | Big-endian 64-bit nonzero public session tag | Avoid accidental overlap; **NOT authentication**, secrecy, or anti-replay |
| 12 | First voice message ID | 0..255 |
| 13 | Last voice message ID (0 in BEGIN) | Monotonic, never wraps during talk |
| 14 | Final segment Sotto frame count (0 in BEGIN) | 1..8 in END |
| 15 | Number of 320ms message segments (0 in BEGIN) | 1..144 in END |
| 16..19 | ASCII `FDV1` | Exact version marker |
| 20..61 | Zero | Reserved; nonzero rejected |
| 62..63 | CRC16 over 0..61 | PN9 restart from `0x01ff` each packet |
| 64..65 | `5a a5` | Sacrificial hardware-FIFO guard |

END is valid only if `last_id == first_id + count - 1`, no wrap,
`1 <= count <= 144`, and `1 <= last_frames <= 8`.
BEGIN never guesses the eventual END. The receiver refuses incomplete
or conflicting control packets. Repeated byte-identical control packets
are allowed; conflicting repeats, session IDs, lengths, and timestamps
are rejected. A lost interior voice packet is precisely 8x40ms erasure.
A lost final voice packet can be reconstructed as an erasure of its
exact advertised 1..8 Sotto slots **if END survives**.
A lost BEGIN or END is an explicitly **incomplete session**, not an
inferred shorter call. A lost first voice packet still lacks an
accurate audio play clock (BEGIN has no established on-radio precise
capture-to-RF timing), so current receiver **fails closed**.
The receiver never invents session boundaries via RF silence.

A session tag is chosen by the transmitter; choose an unpredictable
fresh value for future experiments, but this prototype provides
**NO cryptographic identity or protection against deliberate spoofing**.
CRC16 and the public tag are integrity checks against accidental
transmission errors/collisions only. No RF control packet triggers
configuration changes, PTT, commands, or module execution.

### Timing/capacity implications

BEGIN+END cost two extra 66-byte FSK bursts. The RF data alone takes
`66*8/4800 = 110ms` each; existing 16-byte preamble + 4-byte sync
at 4800baud is ~33.3ms, before any RF key/rearm overhead. The
unproven finite-burst model therefore adds at least **~287ms**
of physical airtime to one call (2 x ~143.3ms; excludes RF key/rearm).
Other users may still be working on shortening the sync/preamble;
do not assume new hardware framing is available. For full speech segments,
the proven over-air voice frame is **33 words**, 320ms speech/packet;
capacity is promising but does **not** prove simultaneous microphone
capture with keyed PA or actual real-time speaker playback.

## Actual files implemented

- `c/k6/mod/dv_guard33_session.h`: zero-allocation freestanding
  little-endian FIFO-word builder; decoder of whitened/CRC-checked 64B
  controls; strict dual-window 66B guard selector. No radio driver,
  SPI, ADC, ISR, PTT or dynamic global state. Output untouched on
  invalid input; tag is represented as two uint32 to avoid costly
  ARMv6-M 64-bit arithmetic.
- `c/k6/mod/dv_guard33_session_m0_probe.c`: **inert / NOT FLASHABLE**
  Cortex-M0 ABI wrapper. No firmware entrypoint.
- `tools/k6_dv_guard33_session.py`: independent Python protocol oracle,
  both voice and control CRC-blind physical offset selector,
  bounded `replay_onair`, and an optional raw-IQ receiver using
  public 4800baud preamble/sync only. It reuses the existing
  `tools/k6_dv_guard33_playout.py` private good-mask/Sotto path.
- `tools/k6_dv_guard33_session_unicorn.py`: builds the real ARMv6-M
  `-Oz -ffreestanding -fno-builtin` ELF and executes the **actual Thumb
  builder/parser/guard** functions in pinned Unicorn, comparing all
  bytes to independent Python; also tests the `f8 00` displacement.
  This is not a host C or Python-only emulation.
- `tools/k6_dv_guard33_session_iq.py`: offline converter for
  previously recorded IQ to private CRC-checked voice/control JSON.
  Refuses output overwrites, requires existing 0700 folder and writes
  private packet hex via O_EXCL 0600; never opens hardware.
- `tests/test_k6_dv_guard33_session.py`: invalid lengths/IDs, tag
  mismatches, CRC corruptions, BEGIN/END loss, repeat conflicts,
  partial final, whole-segment loss, out-of-order, privacy and
  actual Cortex-M0 Unicorn test plus a **complex native-2FSK synthetic
  IQ** blind-demodulation six-burst session.

## Measured OFFLINE results

1. **Real target emulator**: actual compiled Cortex-M0 Thumb instructions
   via Unicorn 2.1.4, six valid distinct BEGIN/END descriptors with
   byte-identical Python and ARM physical payloads, 12/12 normal or
   2-byte-prefix-displaced controls, corrupted control rejected. Linked
   target probe `text+rodata = 792B`, `.data/.bss = 0B`, 16B descriptor,
   highest modeled call 22,314 cycles and observed emulator stack
   highwater 152B. These are standalone probe measurements, **not
   incremental on-radio memory/RAM/CPU totals**; real ISR + SPI + Sotto
   still needs integration and measured headroom.
2. **Six-burst synthetic complex SDR IQ**: BEGIN, four Sotto packets,
   END with alternate modeled `f8 00` prefix fault. Same independent
   public-sync/fixed-4800baud receiver found **6/6 CRC-valid bursts**,
   two correct controls + four voice, offsets `0,2,0,2,0,2`,
   reconstructed **26/26** Sotto frames, no concealment at
   hypothesized 160ms jitter reserve. This IQ is generated in
   software and **is not a physical RF measurement**.
3. **Original REAL FSK-14 physical SDR IQ**, read offline by new unified
   parser: **4/4 previously RF-proven Sotto packets** ID93 seq0..3,
   exact original offset pattern `2,0,2,0`, **zero new session controls**
   (as expected: they did not exist in the experiment).
   New private receiver JSON under
   `/home/tyler/flux-bench/micvoice-20261123/dv-rx-playout-private-20261123/`
   prefix `fsk14-legacy-fdv1-unified-iq-rx-v01`, 0600 file, 0700 parent.
   Legacy audio remains byte identical to earlier FSK-14.
4. **109 passing related tests, zero skips** including new target Unicorn,
   native C queue/receiver parity and archived FSK-14. Missing midcall
   320ms segment and missing final 2-frame segment each generate the
   exact corresponding erasure, never guessed/passed as complete.
   The start/end-control loss cases correctly fail closed.

### Reproduce without radio or network

```sh
python3 -m pytest -q \
  tests/test_k6_dv_guard33_session.py \
  tests/test_k6_dv_guard33_playout.py \
  tests/test_k6_dv_guard33_packet.py \
  tests/test_k6_dv_guard33_stream.py \
  tests/test_k6_fsk_guard33_mic_playout.py \
  tests/test_k6_sotto_fsk_capture_playout.py

# This host's cached Unicorn/Capstone runtime plus system NumPy:
PYTHONPATH=/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/lib/python3.13/site-packages \
  python3 tools/k6_dv_guard33_session_unicorn.py \
  --build-dir /home/tyler/flux-bench/micvoice-20261123/dv-session-cm0-20261123
```

`tools/k6_dv_guard33_session_iq.py` can process old IQ, then
`tools/k6_dv_guard33_session.py --rx-json ...` can complete a talk
**only when** matching FDV1 BEGIN+END controls are actually present.

## Bench gate / not yet claimed

One independent **future** dummy-loaded FD6818B experiment could send
BEGIN, 4x guarded Sotto packets, END with frozen source + code SHA,
all fully preloaded 33-word frames, and require **independent raw-IQ**
public-sync/CRC recovery of exactly all six frames with matching tag,
ID range, final frame count and zero speech data errors, plus firmware
return, radio/VFO/slot restoration and RX idle. The FSK-13/14 result
is relevant but does not count as physical proof of 0xE2. Other
parallel research owns #9 P0 FIFO and #12 PA11. Such a hardware
experiment requires a separate prospective protocol, exclusive GitHub
queue #11 ACK, canonical serial lock, and user confirmation; **no
hardware lease is held**.

After this, FD6818B **on-radio RX FIFO/IRQ, Sotto decoder, speaker
DAC**, actual autonomous mic/encoder+TX concurrency, and real
capture-to-audio latency are still mandatory. The no-wrap segment
epoch currently tops out at 144x320ms = **46.08 seconds**; longer
calls require a tested rollover/rekey/continuation design, not a
silent ID reset. Genuine two-way DV is **not** yet demonstrated.
