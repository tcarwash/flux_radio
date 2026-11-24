# Flux guarded digital voice: receiver/planner + Sotto playout offline result

Date: 2026-11-23. Branch: `research/dv-guard33-rx-playout-20261123`.
This is **OFFLINE** work only; no K6 RF/serial/SPI/flash/SDR device was
accessed, no bench lease was taken, and there was no new transmission.

## Why another receiver implementation?

FSK-14's *physical* K6 microphone / host-encoded Sotto experiment recovered
four CRC-valid guarded L64 payloads with **one message ID 93 and
sequences 0..3**. The later Cortex-M0 on-radio-compatible live queue
(`c/k6/mod/dv_guard33_stream.h`) instead produces **one distinct message ID
per segment**, sequence zero, eight Sotto-1200 40-ms frames (320 ms) per normal
segment. A final packet carries 1..7 frames. The original
`tools/k6_sotto_fsk_capture_playout.py` replays the former layout; it
**cannot simply be reused for live segments** without erasing or reordering
speech. Message ID is not yet a session ID.

## Implemented

`tools/k6_dv_guard33_playout.py` consumes the existing *independent blind
SDR* `tools/k6_fsk_guardword_rx.py` JSON. It never selects candidates using
known transmitted microphone bytes. It verifies the complete CRC16/PN9 L64
payload again; checks bit-identical SHA metadata if present, public sync,
fixed nominal 4800-baud, guard displacement offset 0/2, consistency of msg
ID/sequence/total and finite RF times. Corrupt/incomplete RF observations
are ignored; contradictory CRC-valid duplicates are explicitly rejected.
Valid identical repeated packets use the earliest arrival time.

Its caller **must explicitly select** one of two wire layouts:

- `legacy --start-id 93`: a single message ID with packet sequences
  0..N-1 and a consistently advertised total codec-frame count, as proven
  physically in FSK-14. Packet 0 is the playout-clock anchor.
- `stream --start-id 112 --end-id 115`: consecutive nonwrapping message IDs,
  each packet sequence 0, total 8, and a possible final short packet. The
  start/end IDs are **externally supplied** and are *not* inferred from RF
  loss. If the last segment is missing, its original length must be provided
  separately through `--last-frames` or the replay refuses to guess.
  Missing interior segment IDs account for exactly **320 ms** of erasure.

Max 144 observations/packets per run. Messages cannot silently roll over
after ID 255. The receiver holds only the selected one-session clip; it is
not a radio-deployable streaming decoder. It builds the correct 6-byte Sotto
compressed superframe stream plus an explicit one-byte-per-frame good/erasure
mask for the existing pinned Sotto host decoder. CRC failures and late
arrivals are concealment, not accepted as speech. The additional playout
reserve is caller-selected (default 160 ms), **not measured hardware
jitter**. Physical packet end time is computed from SDR
`payload_start_s + 66*8/4800 = payload_start_s + 110 ms`, including the
sacrificial 16-bit guard. Consequently `first_playout_s` is a position
within the recorded IQ timeline, **not capture-to-speaker latency**.

Optional WAV creation uses an explicitly provided decoder, checks PCM length
at 8 kHz, and creates audio/codec/good-mask/JSON exclusively in an existing
0700 private directory, with 0600 files. Old artifacts cannot be replaced.
No raw compressed microphone bytes or audio are printed or committed.

## Reproducible results

1. **Physical FSK-14 RF evidence replay, without recapturing:** archived
   independent SDR result for msg 93, seq 0..3 (alternate observed `f8 00`
   prefix faults). 4/4 CRC-valid payloads -> **26/26** compressed
   superframes -> pinned Sotto host decoder -> **8320 PCM samples at 8kHz**
   (1.04 s). With the explicit modeled 160ms buffer, **zero late/erased
   Sotto frames**. The newly written private WAV is **byte identical** to
   the archived independent FSK-14 audio reconstruction, WAV SHA256
   `097aa1bed7eafa1ed4d462d67398ae4346edb3f329a0414aeb2f0c81b00e190f`.
   The first playback clock is SDR time `1.0911198` s; this is **not**
   measured user speech latency. New private artifacts:
   `/home/tyler/flux-bench/micvoice-20261123/dv-rx-playout-private-20261123/`
   with prefix `fsk14-legacy-rx-playout-v01`. All written files 0600,
   directory 0700.
2. **Genuine compiled C live transmit queue integration, OFFLINE:** the
   actual `dv_guard33_stream.h` code takes all 26 real archived K6
   microphone-derived Sotto superframes and emits 4x66B complete packets,
   message IDs **112..115**, frame counts **8,8,8,2**, with the correct
   `0xA55A` little-endian guard word. Artificial `f8 00` leading words
   are injected into alternate frames to model the *physically observed*
   FSK-13/14 silicon fault. `select_l64` independently CRC-recovers each
   payload; the new explicit `stream` replay restores the **complete
   original 156 codec bytes / 26 frames, zero concealment**. This is
   synthetic RF fault injection, **not a new on-air native-FSK result**.
3. **Loss/fault tests:** missing middle segment -> **8 erased frames /
   320 ms**; missing initial packet -> no playout anchor, refuse;
   missing final partial -> refuse unless separately specified;
   late packets -> concealment instead of false recovered speech;
   altered packet CRC/headers -> fail; duplicate identical packets ->
   earliest timestamp; divergent duplicate packets -> refuse;
   out-of-order packets -> correct sequence; no message-ID wrap;
   private output O_EXCL/0600 permissions and no-overwrite guards.
4. `81 passed` in the focused combined tests:

   ```sh
   python3 -m pytest -q \
     tests/test_k6_dv_guard33_playout.py \
     tests/test_k6_fsk_guard33_mic_playout.py \
     tests/test_k6_dv_guard33_packet.py \
     tests/test_k6_dv_guard33_stream.py \
     tests/test_k6_sotto_fsk_capture_playout.py
   ```

## Blocking gates before a real radio DV call

- There is **no over-air session-start/end or ID epoch protocol**.
  Without an authenticated start and end, the receiver cannot infer
  exactly how many missing segments belong to a call or the last packet's
  length. It intentionally fails closed instead of producing a seemingly
  valid shorter recording.
- There is no demonstrated **B-chip receiver FIFO integration** for the
  guarded live segment protocol, and the Sotto decoder and 8kHz speaker
  playout here run on the **host**, not on the radio.
- Autonomous live K6 microphone 8kHz TIM17 sampler + actual SPI-backed
  incremental slim Sotto and simultaneous/alternating PA/FSK timing
  remain unproven on this B-chip. The separate emulator-qualified
  integration branch `research/live-dv-guarded-segment-scheduler-20261123`
  is a starting point, **not** a flashable firmware binary.
- Loss concealment and static buffer scheduling are useful and
  independently tested, but no robust RF forward-error correction,
  end-to-end real-time delay, jitter under CPU contention, or
  bidirectional voice call is claimed.

## Next engineering step

Freeze a **separate backward-compatible talk-session framing design**
(start/end/epoch/fragment length and rejoin behavior, with bounded replay
and wrap protection), validate it in the target emulator against this
receiver, then independently qualify native RX and on-radio Sotto
decode/speaker output under the GitHub shared bench queue. Existing
P0 #9 / DV #10 / mic #12 priority must be respected; an offline
engineering result is **not** a radio lease.
