# Flux live DV: incremental 10ms Slim Sotto + existing-wire L64 talk segments (OFFLINE)

Date: 2026-11-23, branch `research/offline-sotto-fsk-dv-20261123`. **Verified host C API with real saved K6 microphone**; packetizer/scheduler modeled offline. No K6 USB, radio, SDR, PTT, firmware flash, or shared hardware lock access. The other FSK-12-RF agent owns a separately acknowledged hardware lease on the same original mic voice clip, so no duplicate RF was attempted here.

## Verified streaming encoder API — actual C, not a codec simulator

Our reproducible C wrapper `tools/k6_sotto_slim_streaming_host.c` links the unmodified upstream `sotto_fx_slim.c` and `fx_tables.c` as a host executable. The incremental API is `slim_enc_init`, `slim_enc_bytes`, `slim_encode10(e, int16_t[80], out6)` and `slim_flush` at end-of-call. This consumes exactly **80 PCM samples/10ms** at 8000 samples/s, **not** an entire 40-ms block, and outputs a 6-byte Sotto-1200 superframe whenever ready. With the actual K6 PA11 mic audio already filtered, rate-corrected and padded to 1.04s:

- **104** consecutive 10-ms input blocks were accepted.
- **26** genuine 6-byte compressed superframes were produced (156 bytes), **bit-exact to the earlier batch `sotto_slim_enc` output**, SHA256 `8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5`.
- The **first superframe was emitted after 9 input blocks (90ms)**; subsequent superframes at 130ms, 170ms ... through the 24th at 1010ms, and **two final superframes were flushed at the 1040ms end of capture**.
- The exact stream event table is private `sotto-slim-cm0-build-20261123/mic-incremental-timeline.csv`; actual voice sample, compressed bits, radio packets and WAV remain local only and are NOT committed to Git.
- A test freshly compiles this wrapper and verifies **bit-exact equality against batch slim encoder** on both a synthetic waveform and real K6 microphone PCM, including expected first output time and two frame flushes. This checks *incremental API correctness*, NOT CPU timing: the host executable can process recorded samples faster than real-time, and no actual K6 MCU/Cortex cycle budget has yet been measured.

## Live packetization snag and compatible way forward

**The existing Flux L64 `pager` profile expects a known total superframe count in the header of every packet.** A live PTT session cannot supply that total at frame 0. Simply precomputing all packets after releasing PTT works for a stored voice message but does not make a real-time call.

A lightweight solution requiring **no new RF on-wire format** is to treat each 8-frame/320ms speech segment as one independently CRC-protected Flux L64 *message*:

- Accumulate eight 6-byte slim superframes = **48 bytes of speech** and pack with existing `pager.profile('L','1200',depth=1,parity=False,short_last=True)` into exactly one **64-byte** frame (header, 8 reserved bytes and CRC). The frame's `total` field is 8, not the unknown duration of the entire PTT session.
- Assign a monotonically increasing **`msg_id`** to each 320ms segment; each packet's **packet seq is 0**. Receiver order uses the session's msg_id progression instead of a continuous per-message packet seq. Each segment can be independently CRC-checked, reassembled, and decoded with the existing Sotto decoder. A short last chunk sets `total=1..7` while keeping its fixed 64-byte RF frame.
- No stream/pager wire-format change is needed. However, **the protocol still needs explicit session framing** for message-id rollover, packet loss, and **end-of-PTT when the last segment happens to contain exactly eight frames**. Increasing `msg_id` without session IDs indefinitely would become ambiguous after 256 segments (~82s). A first operational implementation should limit calls to bounded durations or add a session-control envelope.
- One erased 64-byte chunk loses 320ms contiguous speech with depth 1 and no parity. Production needs CRC erasure + short fade/concealment and perhaps a second parity packet protecting adjacent chunks, at the cost of duty cycle and latency.

Our new `tools/k6_sotto_slim_live_segments.py` uses **the actual recorded C-encoder output arrival times** from the private timeline; it packs and CRC-verifies every self-contained segment, then models packet departure/arrival using the previously measured **~151.042ms** 64B frame interval (106.667ms data plus 44.375ms rearm). It assumes one ideal radio channel, no extra key-up cost or RF packet loss, and does **not** pretend that the microphone PCM sampler can run concurrently with the FD6818B FSK modulator.

## Measured input timing and modeled output — 1.04s K6 microphone fixture

| Chunk | Sotto frames | Ready after mic input | Modeled TX start | Modeled RF complete | Chunk header |
| --- | --- | ---: | ---: | ---: | --- |
| 0 | 0–7 | **370ms** | 370ms | 521ms | msg112 / seq0 / total8 |
| 1 | 8–15 | **690ms** | 690ms | 841ms | msg113 / seq0 / total8 |
| 2 | 16–23 | **1010ms** | 1010ms | 1161ms | msg114 / seq0 / total8 |
| 3 | 24–25 | **1040ms** | 1161ms (queued) | 1312ms | msg115 / seq0 / total2 |

All **four new L64 packet CRCs validate**, reassembly returns **26/26 original slim compressed superframes, bit-exact**. At an ideal receiver with a **160ms start buffer after packet 0 completes**, the first playback begins at modeled time **681ms** from start of capture; **26/26 frames meet their playout deadlines** in this synthetic zero-loss scenario. Maximum observed model TX queue wait is ~121ms for the short final chunk, while actual 40ms frame input events remain 10ms apart. A separate injected 500ms encoder stall causes late frames in tests, proving the deadline model does flag overruns; artificially accelerated encoding creates TX queue buildup but no fake late audio.

**This is a scheduling feasibility model, not 681ms of measured end-to-end physical audio latency.** It ignores any extra key-up/PA ramp, platform CPU jitter, microphone/FSK mux conflict, interrupt starvation, SDR acquisition variation and RF packet errors.

## MCU memory and scheduling implications

Our [actual Cortex-M0 -Oz ELF section audit](k6-sotto-slim-mcu-static-budget-20261123.md) estimates only 1,094B in the 15,408B K6 module arena after externalizing the 30,720B codebook and accounting for encoder state and upstream stack estimates. Instead of double-buffering full 640-byte/40ms PCM blocks, stream **two 160-byte/10ms buffers** through `slim_encode10` to leave a modeled 774B before RF queues, or **646B after two 64-byte L64 buffers** (still too tight for unmodeled ISR/SDK). Externalizing at least another 1KB of readonly tables into a signed SPI library is recommended before implementing live audio.

A 10ms PCM input chunk arrives every 10ms; the MCU must service 80 samples at 8kHz/125µs each while concurrently processing the prior chunk and using SPI for codebook reads. The documented slim computational model is ~27/29MHz (Cortex-M0, -Oz, in-RAM), plus around 5ms SPI per 40ms. Real K6 cycle/stack/SPI and uninterrupted ADC sampling are still **untested**. The full-Sotto 1200 host encoder in the separate queued FSK-12 RF trial is **not** a proxy for on-radio slim CPU feasibility.

## Files and reproducibility

- `tools/k6_sotto_slim_streaming_host.c`: actual 10ms C API adapter, no radio functions.
- `tools/k6_sotto_slim_live_segments.py`: existing Flux L64 segment packetization + optimistic measured-link timing model (no new PHY).
- `tests/test_k6_sotto_slim_host_incremental.py`: builds host C and checks exact full/batch streaming audio on real mic and synthetic source.
- `tests/test_k6_sotto_slim_live_segments.py`: unit tests CRC/sequence, final partial segment, message ID wrap refusal, positive real mic event timing, queue overrun, stalled encoder playout errors.

Compile offline host proof without modifying the upstream worktree:

```bash
SOTTO=/storage5/dev/flux-mic-digital-voice/sotto/c/fixed
clang -O2 -DSOTTO_V36 -DSOTTO_PITCH_THIRD -DFX_NARROW_MUL -I "$SOTTO" \
  tools/k6_sotto_slim_streaming_host.c "$SOTTO/sotto_fx_slim.c" "$SOTTO/fx_tables.c" -lm \
  -o /tmp/k6-slim-streaming-host

/tmp/k6-slim-streaming-host \
  /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw \
  /tmp/k6-mic-incremental.bit /tmp/k6-mic-incremental-timing.csv

python3 tools/k6_sotto_slim_live_segments.py \
  --bits /tmp/k6-mic-incremental.bit --timeline /tmp/k6-mic-incremental-timing.csv \
  --out-dir /tmp/k6mic-slim-segments
python3 -m pytest -q tests/test_k6_sotto_slim_host_incremental.py tests/test_k6_sotto_slim_live_segments.py
```

**Decision:** 10ms chunks via the actual slim encoder provide a **correct live-compatible compressed-bit pipeline**, and per-320ms segment messaging avoids the unknown-total call-length problem *without changing the existing FD6818B packet wire protocol*. Real MCU microphone ISR/SPI coexistence and RF transport need physical validation via a **new explicitly queued experimental plan**, not a blind transmission.
