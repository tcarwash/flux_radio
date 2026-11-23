# Flux K6 — native FSK streaming, packetized DV and multi-actuator fallback roadmap

**Added 2026-11-23; side-thread research handoff.** Priority: **high**. Target: FD6818B/BK4829 ID `0x4829` under the existing guarded dummy-load RF/SDR fixture. This is a **testing roadmap, not a claim of completed streaming**, and must not interfere with the TX-15/TX-16 agents' live work.

**Program goals (parallel, not sequential):**
1. **FSK-09S: true continuous 4,800-bps native 2-FSK** — prove B-specific FIFO headroom/interrupt/refill or transparent DMA-like mode without restarting modem/framing, with uninterrupted arbitrary payload and independently acquired RF IQ.
2. **FSK-10: packetized, buffered real-time digital voice** — prove that repeated finite FIFO frames at a predictable duty/payload rate can transport continuous *application-level* audio, even if the modem inserts known ~47ms of nonpayload modulation at each boundary. This does **not** require phase-continuous framing as long as a receiver reacquires frames and a jitter buffer absorbs the intervals.
3. **FSK-11: store-and-forward digital voice** — if low-latency packet streaming fails, encode and queue Sotto frames as a voice message, transmit the chunks on the proven bounded FSK path, reconstruct and play only when correct/complete (or progressively when enough good frames arrive).
4. **FSK-12: independent secondary actuator** — test whether raw DCS/CTCSS, FSK deviation/gain, tone oscillator, or another physical path can coexist with *native FSK mode* to convey additional metadata/FEC/side data or aid gap handoff. **No simultaneous native FSK + any second source has been physically demonstrated.** Do not presume their gross bitrates add or that REG70 can serve two RFIC work modes at once.

## Evidence and current limitation, from physical RF

- **Native 2-FSK FIFO:** physically transmitted at **4,800 raw binary symbols/s**. The recovered native-chip known-vector tests give 0/1,792 pilot-trained heldout bits across four recordings, but **all four use the same data vector**. Other captures at 4,800 saw startup/acquisition errors. This is **not** a field BER, independently randomized payload test or streaming demonstration. See [repeated FSK/PDM study](k6-fsk-pdm-repeats-and-phase-20261122.md).
- **FSK-09 two-frame boundary:** two separate **64-word = 1,024-bit** native frames emitted under a single TX lease, both receiving **0/512 heldout payload errors** with independently trained receiver; **260.052ms** from the first payload start to the second, versus **213.333ms** for 1,024/4,800 bits, leaving **46.719ms** of **nonpayload modulation**. RF envelope did **not** drop significantly; **that does not mean native data or phase continued uninterrupted**. See [FSK-09 physical report](k6-fsk-09-fifo-boundary.md).
- **Estimated gross repeat-frame capacity if this timing repeats without additional delays:** `1024/0.260052 ≈ 3,938 raw bits/s`. This is an **illustrative two-frame measurement**, not a measured sustained net bitrate or trustworthy arbitrary-long timing. Any per-frame preamble/training, CRC, FEC, data header, failure/retry, interframe guard and radio service delay reduce useful throughput.
- **FSK-09 in-frame refill:** one attempted 128-word/2,048-bit frame preloaded 48 FIFO words and awaited the old-family `REG02[14] = 0x4000` almost-empty flag, but **no such interrupt appeared** before the bounded timeout in either run. An unrelated `REG02 = 0x0800` remained set. Both attempts aborted/restored safely. **Not proof that B silicon cannot be refilled**; it invalidates that assumed old-family IRQ path. Never read destructive FIFO register `REG5F` as a status probe, treat `0x0800` as a headroom grant, or write unknown extra words without confirmed FIFO capacity.

## Bitrate budget and why bursts might still carry live DV

| Codec | Compressed speech rate | Stored voice data for 10s | Practical significance |
|---|---:|---:|---|
| **Sotto 1200 slim** | **1,200 bps** | **1,500 bytes** | Existing Cortex-M0 slim *encoder* path; 6 bytes per 40ms |
| **Sotto 600** | **600 bps** | **750 bytes** | Decodable on M0; no comparable small/fast on-radio encoder established |
| **Sotto 400** | **400 bps** | **500 bytes** | Low quality but viable store-and-forward fallback; on-radio encoder still hard |

At the **hypothetical** ~3,938-bps gross repeated-frame rate, a rate-1/2 FEC layer leaves ~1,969 bps **before** frame overhead or losses: potentially enough for Sotto 1200 **on paper**, and generous for Sotto 600. A 1,024-bit FIFO frame spans ~213ms and the 47ms restart overhead means a simple jitter buffer of a few hundred milliseconds could absorb that gap if frame decoding and CPU/audio capture/playback keep up. **Streaming user experience does not require continuously streaming native symbols.** It requires stable **average goodput, bounded packet delay, a decoder with independent reacquisition, and no missed playback deadlines**.

A 10-second Sotto 1200 clip is ~1.5KB before FEC and framing. Store-and-forward into 128-byte frames requires **at least 12 data-bearing frames** with no protection, so roughly >3 seconds of illustrative RF time at that measured 260-ms frame spacing; add realistic overhead/retries and measure the actual whole-session airtime. For 600 bps, at least 6 frames; for 400 bps, at least 4. SPI flash/module memory and retransmission state must be measured, not assumed. Sotto voice frames can be recorded, stored, delayed, reassembled and decoded even if raw RF between frame chunks is not continuous.

**Distinguish four terms in all reports:** `native 4800-bps chip clock`, `repeated-frame gross bits/s`, `error-protected application goodput`, and `real-time speech playback success / codec bitrate`.

## Experiment tracks

### FSK-09S — actually continuous native FIFO refill (top hardware discovery)

**09S-A: source/ABI audit, RX/read-only telemetry first.** Trace **0x4829-specific** behavior for `REG02, REG0C, REG3F, REG59, REG5E, REG5F, REG70, REG72` and possible modem status/packet-start/payload-length/whitening/CRC/interrupt mode. Cross-check actual B driver sources and traces, not only original BK4819/FD6818 notes. Map all potential FIFO watermarks and *which* interrupt flag reports them. Verify whether `REG5E` threshold meaning and `REG3F` interrupt gate match our hardware. Any ambiguous register is **not a license to write blindly**.

**09S-B: guarded FIFO-occupancy state observation.** Design short **status-only** physical experiment with selected payload lengths and read-only samples at precommitted times while native FSK runs; avoid `REG5F` reads, premature writes and dangerous IRQ clearing. Freeze timing thresholds and positive/negative controls before RF. Need a B-specific *changing occupancy/headroom or completion signal* before considering refill.

**09S-C: one incremental in-frame refill.** Only if 09S-B qualifies headroom, append a **small** predeclared known payload strictly below a proved free-space bound; instrument firmware timing, error exits, and raw-IQ receiver; measure whether previously scheduled bits are preserved, whether new bits emit without preamble, and whether RF phase/modulation continues. Stop on any ambiguity; no blind repeated FIFO writes or unbounded retry.

**09S-D: prospective independent continuous data.** Increase from two to ≥4 independently generated segments **within the existing authorized finite TX lease**. Then controlled longer sequences only after separately designed, approved bounded watchdog/lease coverage (never silently extend the existing 1.8-second lease). Two independent physical captures of new PRBS+framed payloads; pilot-trained receiver must decode heldout segments, track clocks, FIFO underrun and occupied spectrum, and report *application goodput* and intersymbol gaps.

**09S-E: alternate FIFO APIs if IRQ dead end.** Safely investigate *documented or clearly B-audited* transparent/unframed mode, completion interrupts, packet-length reload semantics, FIFO busy/free signals and state transitions. Re-key/restart under TXON is **not** automatically gapless just because RF PA remains enabled. Keep FSK-09 negative result intact.

### FSK-10 — packetized near-real-time DV even with frame restarts (highest near-term system payoff)

**10-A offline (partial, 2026-11-23):** `tools/k6_fsk_dv_model.py` now models fixed 64-word native-FSK frames, measured 46.719ms restart time, explicit header/CRC capacity, an idealized rate-1/2 repetition bound, packet erasures and a fixed-delay playout deadline. Unit tests cover the measured 3.94kb/s illustrative gross rate, fit, loss and jitter determinism. A selected Sotto-1200 geometry (8 codec frames/packet) carries 48 payload bytes in 108 protected bytes, fits 1,024 bits, and models 1.694kb/s application goodput; a 500-packet run at 5% loss, ±150ms jitter and 600ms delay missed 31 deadlines (all modeled packet erasures). **This is only a capacity/deadline model.** It does not yet ingest prerecorded compressed frames, implement a packet/FEC decoder, independently reacquire actual new-payload RF, or simulate jitter-buffer occupancy/PLC. Complete those before treating 10-A as qualified.

**10-B physical two-to-four frame burst:** Replace the repeated fixed vector with *new, independent* payload frames under the current firmware TX lease and bounded known RF fixture. Retain original signed module/VFO restore. Decode each frame and measure actual nonpayload gap and timestamps from raw SDR IQ. No new high-power or over-lease RF.

**10-C host-sourced live-ish voice:** Stream preencoded Sotto 1200 frames from the PC into a bounded queue; send them in consecutive FSK frames; **play from a receiver jitter buffer** with explicit latency. Host-assisted streaming can prove the *radio bearer* while microphone-capture + M0 encoding remains a separate problem. An orderly dropped/CRC-failed frame should trigger PLC/concealment, not false speech. Declare success only for sustained real-time playback with no underruns over a precommitted time span.

**10-D native K6 endpoint later:** Integrate mic->MCU capture, Sotto slim encoder, receiver decode and speaker output under measured CPU, RAM, library-slot SPI and UART/PTT concurrency. Current tone state and mic routing are not yet a proven live capture + TX combination. Do not conflate an SDR/host voice demo with standalone K6 DV.

### FSK-11 — store-and-forward / voice-pager fallback (can ship earlier)

**11-A store:** Generate exact Sotto 1200/600/400 frames from a host voice WAV as already supported by the Flux Sotto lab. Split into packet `message_id, sequence, total/length, codec_mode, CRC`; fixed end-of-message; optional parity/ARQ. Define airtime limits and flash/RAM capacity. Model power failure, duplicate chunks, out-of-order, and partial reception; no malicious payload execution.

**11-B RF and replay:** Transmit ≥2 independent stored voice clips in bounded native FIFO bursts, receive/CRC/reassemble and compare exact codec frame bytes; decode to WAV and inspect speech. At least two captures per qualifying condition and realistic 1–10% loss tests; voice must not be falsely accepted if chunks are corrupt.

**11-C progressiveness:** Once proven, play a message while later chunks are arriving only when buffered and checksummed. Implement missing-chunk concealment and optional parity; **store-and-forward is not the same as live real-time voice**, but offers useful practical voice messaging immediately if the FIFO never refills.

### FSK-12 — extra RF modulation vector alongside native FSK (separate research; *not* assumed)

| Candidate | What it might add | Why it might fail / go-no-go |
|---|---|---|
| **DCS polarity REG51 + native FSK FIFO** | Low-rate independent FM offset / side channel for stream clock, ID, CRC or rare control bits, possibly through frame gaps | The TX16M standalone polarity oscillator is fast enough for 2.5ms alternating **spectral** test but faster chosen-payload work not established. In native FSK mode `REG51` generator may be disconnected, reset or distort FSK. **Static work-mode audit before a joint RF trial.** |
| **CTCSS REG07/51 + native FSK FIFO** | Continuous low-band pilot for acquisition or control | TX16C confirms CTCSS+**normal tone oscillator**, **not** CTCSS+native FSK. CTCSS 100-Hz pilot isn't itself a high-bitrate channel; extra deviation can worsen FSK BER or legal spectrum. |
| **REG70 FSK deviation/gain timed updates** | 2 gains × 2 native FSK signs could in principle give *four effective frequency states* / robust side information | `REG70` is shared by native FSK and tone generator; gain steps may have delay/nonlinearity and symbol-clock misalignment. The two-valued gain actuator is **not** yet demonstrated at 4,800 updates/s. First separately verify with **slow known schedule** and frozen discriminators; cannot claim 4-FSK throughput. |
| **REG71/72 main audio oscillator while native FSK** | Alternate audio-FM basis or handoff tone during restart intervals | **REG72 is used for native FSK baud**, so changes may destroy modem timing. FM audio work-mode exclusivity may prevent summing. **No simultaneous proof.** |
| **Alternate native multilevel/digital baseband** | True 4-FSK or continuous signed deviation without FIFO framing | B-chip TX 4-FSK/DMR and digital TX sample ingress not proved; preserve TX11/TX18 audits and hardware reference/TCXO routes. |
| **External isolated filtered MICIN / daughterboard** | Host/MCU driven higher-order audio FSK; alternate parallel signal | Physical MICIN pinout and shared serial cable remain blockers; requires reversible hardware fixture and calibrated TX, not a software-only register magic. |

**Priority and definition of 'additional magic':** a secondary signal is useful **only** if physically separable without corrupting the primary 4,800-bps native bits, yields independently decoded *new data/control* under a prospective source/receiver, and improves *measured net goodput or resilience*. Adding theoretical raw rates (e.g., 4,800+250) without proved coexistence is invalid. If the secondary source works only during each 46.7-ms restart, even a hypothetical 250 bps would provide about **12 extra raw bits per frame gap**, or ~45 average bps—more apt for control/synchronization than making or breaking DV rates.

**Controlled qualification ladder:** B-specific source-register work-mode audit -> emulated conflict/restore guards -> static one-source reference and independent combined RF capture -> pilot-trained separate-band/symbol recovery + B-chip FIFO bit errors + spectrum -> only then dynamically timed joint payload and true net goodput. No APC/PLL experiments piggyback on this RF test.

## Common engineering deliverables and acceptance gates

- **Transport codec API independent of RF:** Sotto mode/version, 40/80/120ms superframe boundaries, monotonic audio timestamp, message/session ID, sequence, CRC, FEC, end-of-stream, jitter/replay/duplication handling. Codec layer must not assume carrier-phase continuity.
- **Three scores for every physical result:** 1) radio's module/FIFO result, 2) independently acquired RF bit/frame correctness (no truth-based holdout fitting), 3) recovered voice-frame data and audible playback. Do not report a successful MCU write as decoded RF success.
- **Two modes of success:** **gapless PHY streaming** = no added 46.7-ms nonpayload restart and indefinitely extendable data under an explicitly guarded TX; **packetized streaming DV** = application delivery >= codec + FEC/framing overhead and stable bounded jitter/latency, even if PHY starts each frame anew.
- **Store-and-forward pass:** one precommitted new voice clip >10 s with byte-exact compressed-frame reassembly and intelligible playback from new RF, plus a failed/erasure control; air time, overhead, radio memory and cleanup reported.
- **Near-real-time pass:** ≥10 s of independent voice frames, plus separately measured airtime, effective rate, max interframe gap, BER/PER, transmit/receive buffers, listener latency and no receiver playback underruns. **This requires a newly approved long-TX watchdog design**, since current radio TX lease is ~1.8 s.
- **Minimum hardware safety:** dummy load and air-coupled/attenuated SDR, verified 0x4829 identity and known 433.500-MHz fixture, channel idle checks, exact source hash, signed slot-30 code backup/readback and both RX VFO restores, RF TX timeout / key abort and bounded no-autoretry, two independent captures. No flashing, serial ownership seizure, or TX from this side chat.

## Relevant local reports and code

- [FSK-09 hardware FIFO boundary and refill negative](k6-fsk-09-fifo-boundary.md)
- [Native FSK / PDM background and rate tests](k6-native-fsk-pdm.md)
- [Repeated FSK 4,800-chip RF decode](k6-fsk-pdm-repeats-and-phase-20261122.md)
- [B-specific multilevel TX source audit](k6-tx11-multilevel-source-audit-20261122.md)
- Flux Sotto encoder feasibility: `/storage5/dev/flux-mic-digital-voice/docs/k6-sotto-slim-feasibility.md` (**separate worktree**, not a relative file here).
- [FluxID repeater audio and PSK plans](k6-fluxid-repeater-voiceband-afsk-dbpsk-handoff-20261123.md)

**Main recommendation:** run **09S source+read-only occupancy research** and **10-A packetized voice simulation** in parallel now. If a bounded *two-to-four-frame* direct-RF packet transport is qualified, build **11 store-and-forward** without waiting for a mythical gapless FIFO. Keep **12 secondary-actuator** as a distinct source-audit and physically gated option. If hardware FIFO refill never works, that does **not** preclude useful DV voice messages or eventually buffered real-time voice over short native-FSK frames.
