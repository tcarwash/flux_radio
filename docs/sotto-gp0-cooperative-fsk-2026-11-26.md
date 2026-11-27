# Sotto GP0 cooperative FIFO preload: opt-in host prototype

**Date:** 2026-11-26 Pacific. **Branch:** `experiment/sotto-cooperative-fsk-preload-20261126` based on the four-opt codec branch `83e75d79`. **State:** offline-only, research build flag **OFF** by default; not hardware qualified or installed.

## Motivation and mechanism

Read-only live diagnostics previously measured an approximately **14.8 ms** synchronous FSK preload during GP0 PCM collection and repeated 4/4 queue overflow. The default API29 host performs one synchronous `fsk_async(op=0,words,33)` call, saving nine FD6818B registers, enabling PA, writing mode setup, resetting FIFO and pushing 33 words before returning to codec work. Its **subsequent** 20 ms FIFO-settling interval was already nonblocking.

The new `K6_SOTTO_FSK_COOP_PRELOAD=1` opt-in host behavior retains the existing ABI, 66-byte FSK payload, `0xa55a` guard, one PA session and `sl_peek` queue ownership until ACK. It checks the packet and saves register state in `op=0`, then performs the following bounded operations in successive `op=1` polls (the module normally polls at 5 ms intervals):

| Poll | Register operations | Max writes |
|---|---|---:|
| 1 | Disable FSK, reset control, first 4 profile registers | 6 |
| 2 | Remaining 5 profile registers, reset FIFO and disable FSK | 7 |
| 3–6 | Write FIFO words 0–7, 8–15, 16–23, 24–31 | 8 each |
| 7 | Write guard word 32, confirm TX-done status is clear | 1 |
| Later | After another full 20 ms, enable FSK transmission; await real RFIC TX-done | 1 |

Every poll revalidates the stable scratch pointer and guard; each write invokes existing power/cancel/key/lease checks. The 200 ms startup watchdog spans all steps and settles, and the 700 ms burst watchdog still applies once RF is started. Error paths call existing PA-off and reversed-register restoration, clear the staged pointer, and dekey. A repeated `op=0` while active fails closed. `run_module` explicitly clears stale preload state before starting. No background ISR, third-party buffer, module-side memory allocation, new RF mode or API version is introduced.

**Pointer ABI constraint:** the optional host can read the 33-word packet *after* `op=0` returns. Existing GP0, live-mic, tone and fixture Sotto clients keep their sealed `sl_peek` slot pinned until the host acknowledges `op=1` completion. External clients must also retain an immutable, aligned authorized-scratch buffer across polls; an ephemeral stack packet or reuse before ACK would be unsafe. This is now documented in `c/k6/k6_mod.h`.

The reference default host callback (`k6_sotto_fsk_coop_preload` **unset**) retains its original synchronous register sequence, including PA enabled during FIFO preload. Build validation rejects cooperative mode without API29 FSK + GP0 mic + expanded RAM0. The GP0 timing trace still counts a preload once per `op=0`, but its *peak callback* metric includes the staged `op=1` operations when this variant is enabled (it is **not** the elapsed time for the entire seven-phase preload).

## Native exact-host RFIC mock (not real hardware)

`tests/test_k6_sotto_fsk_coop_native.py` compiles the **actual production Rust host include** with `rustc --cfg k6_sotto_async_fsk --cfg k6_sotto_fsk_coop_preload`, and exercises seven cases on an in-memory RFIC. All seven pass: 33-word FIFO order and guard, PA-before-FIFO and RF-after-full-preload, three frames sharing one PA lease, cancel midway, corrupt guard, missing-poll watchdog, duplicate start and explicit abort. The baseline host's original nine tests are separately compiled without the new cfg and continue to pass.

At a model-only cost of **0.4 ms per RFIC register read/write**, the largest cooperative callback was **40 × 100 µs = 4.0 ms**, versus the previously measured *physical* 14.8 ms contiguous synchronous preload. The model's SPI latency is a sensitivity assumption, not calibrated FD6818B timing. This comparison therefore does **not** prove an exact on-hardware reduction.

## Cortex-M0 codec + virtual RF ablation

`tests/test_k6_sotto_fsk_coop_rf.py` uses the compiled four-optimization ARM module (9,784 B, 48 B scratch margin), actual Sotto IVF/codebook deck, independent native codec oracle, synthetic virtual RF and separate wire decoder. The **host callback scheduler** is a behavioral model `CooperativeFskLiveTx`, not an execution of the Rust firmware. The baseline model remains unchanged; enabling `fsk_cooperative_model` reproduces the seven phases and 20 ms settling interval. Fixed model inputs: 8 kHz 1.20-second 320/870-Hz mixed tone, ADC cost 4,000 cycles/sample, SPI table read setup 6.1921296 µs plus 0.9403935 µs/byte, CPU CPI and RFIC register cost varied. `fd_us=330` or `200` are deliberately injected per-register costs, **not direct silicon measurements**.

| CPI / modeled FD reg cost | Original synchronous | Cooperative |
|---|---|---|
| 1.5 / 330 µs | Pass, queue 3/4, RF start 131.9 ms | **Pass**, queue **2/4**, RF start 193.6 ms |
| 2.0 / 330 µs | Pass, queue 3/4 | Pass, queue 3/4 |
| 2.5 / 330 µs | Overflow at 1,120 samples | Overflow at **1,600 samples** (+43%) |
| 3.0 / 330 µs | Overflow at 1,120 samples | Overflow at **1,360 samples** (+21%) |
| 2.5 / 200 µs | Pass, queue 3/4 | Pass, queue 3/4 |

The three-second deterministic mixed-speech/noise virtual fixture also passed under CPI 1.5 / 330 µs: **24,000/24,000** microphone samples, **75 exact codec frames**, **12 valid RF packets**, zero invalid, independent wire-decoder agreement, no capture loss, GP0 start/stop once, and peak queue **2/4** (synchronous baseline **3/4**). This is a separate longer run and is covered by a repeatable test.

Completed 1.20-second sessions returned 30 exact 6-byte codec frames, six valid received RF packets, zero invalid packets, identical wire-decoder reassembly, one GP0 capture start/stop, and TX inactive at exit. Failed runs produced `-12`, PCM highwater 4/4, incomplete sessions, and dekey/stop; **these are not sustained-throughput successes**. The tradeoff is significant: polling seven steps plus 20 ms settle delays first RF by approximately 62–99 ms in the modeled completed runs. This can increase FSK packet backlog and needs longer mixed/voice evaluation.

Machine-readable sensitivity sweep: [`docs/evidence/sotto-gp0-cooperative-fsk-20261126.json`](evidence/sotto-gp0-cooperative-fsk-20261126.json).

**Offline validation:** 55/55 tests passed in the combined host/overlay/GP0 fault-timing/virtual-RF/encoder-regression selection. This includes seven Rust-native cooperative host cases, the nine-case default-host regression, the three-second mixed-signal RF test, and unchanged prior encoding/GP0 behavior. `sh -n`/Python syntax checks and `git diff --check` also passed. No ARM firmware image was built or installed; available tests do not establish full firmware flash headroom.

## Qualification boundary and next work

1. **Do not use on physical radio yet.** The cooperative host has not had a full ARM flash-budget compilation, a real serial timing capture, an RFIC FIFO-integrity readback, an over-air/SDR packet comparison, or power-loss recovery qualification. The known 32-word FIFO truncation possibility remains and must not be masked by the virtual RF decoder.
2. If later testing is permitted, measure each `op=0`/`op=1` slice, 20 ms settle and total packet start latency separately; confirm PA behavior, 33-word guard visibility, bounded 25 s session, module cancellation and RX-register restoration on the dummy load. Preserve proven recovery firmware.
3. Cooperative preload reduces the longest foreground stall but **does not** reduce total SPI work or fix worst-case `slim_analyse10`/envelope throughput. Keep the opt-in separate until both CPU and FSK start-time margins are supported by measurements.
