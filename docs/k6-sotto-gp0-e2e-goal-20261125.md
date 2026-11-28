# Goal: End-to-end flashable and flushable Sotto GP0 firmware

**2026-11-25** — `feature/sotto-mic-continuous-pa-20261125`.

## Scope and state

The user asked for a complete, flashable and *flushable* Sotto implementation. The present deliverable is a **cable-only K6 B-chip GP0 microphone/TX firmware test candidate**, not yet an on-radio-verified digital voice transceiver. The previous physical K6 GP0 microphone experiments established microphone-dependent 8kHz PCM and continuous DMA1 IRQ9 sample capture with **6,826 half interrupts, 5,006 samples, 62 processed blocks, zero lost buffers/overflows/clipping, and peak 2,365 timer ticks** during a synthetic foreground load. Those measurements are not evidence that Sotto's actual SPI codebook encoder and FSK transmitter can run concurrently on the real MCU.

## Implemented in branch

- `firmware/k6-aura/sotto-api30-gp0-capture.rs` is a firmware-owned high-rate GP0/PB1 DMA1 channel1/IRQ9 capture producer and signed-8kHz PCM decimator, with bounded queue and IRQ cleanup.
- `c/k6/mod/sotto_gp0_irq_tx.c` is a separate **9,796-byte** `SOTTO GP0` loadable voice TX module. It requires a real physical PTT, enables the microphone route, allows 800ms bias settling, starts the firmware GP0 PCM service, encodes with the pinned Sotto codec, and passes voice packets to the existing API29 nonblocking FSK service.
- On PTT release `sl_engine_release` first stops/join-closes capture and **pads the incomplete final 80-sample PCM block with zeros**; the foreground drains the remaining 10ms blocks, calls `slim_flush` until the real codec reports completion, emits the end-of-session FDV1 control packet and waits for the actual FSK `poll` completion before `tx_off`. All loss/timeout paths abort and dekey rather than claiming success.
- Target build omits the space-expensive optional **on-radio 4800-baud RX service** but preserves the API31 host tail, API28 resources, API29 nonblocking FSK TX, API30 microphone service and mode/mailbox interfaces. This is a **TX-only** candidate; independent SDR and native Sotto decoder provide receive verification. It is not a native two-way K6 image.

## Evidence and reproducibility

- Actual Cortex-M0 firmware image in isolated build: **119,928 bytes**, SHA256 `9db7dcf05c5f8c85e386798354ced76250676e9243493f2c49036a5f4d136e21`; flash application cap **122,880** bytes. Protected RAM0 **3,840/3,840** bytes; expanded module arena **14,976** bytes; audited main-stack reservation **2,780** bytes. The 9,796-byte module gets only **4,116** bytes scratch after the callback-stack reservation, against its checked **4,080**-byte requirement (**36-byte margin**). Thin headroom remains a risk and motivates a later optimization pass.
- Existing connected, attended real-radio **`MIC IRQ`** test was on a dummy load; not the current `SOTTO GP0` TX module. No new image was flashed or transmitted by this Goal step.
- Compiled module virtual-RF test used **8,293** injected PCM samples, intentionally ending 13 samples into the final 80-sample block. The actual compiled K6 `SOTTO GP0` module transmitted **26** exact codec frames and **6** FSK frames through virtual RF. The independent decoder recovered all six, confirmed CRC, complete FDV1 session and final control, and decoded the exact independent native reference voice bytes. No PCM loss, modeled TX lease expiry or un-drained RF remains. Repeated with a two-byte receive offset. The stress test deliberately injects excess IRQ load and confirms clean failure rather than false session completion. Regression `tests/test_k6_sotto_gp0_end_to_end.py` **4 passed**.
- Firmware-linked Cortex-M0 API30 GP0 start/reentrancy/stop and PA11 legacy teardown tests passed **3** opt-in emulator tests with the exact linked ELF/image. These gates separately validate firmware memory, vector and DMA setup/cleanup, not real concurrent codec operation. Main focused regression suite **63 passed, 7 skipped**; expanded GP0 virtual link plus package suite **16 passed**.
- The connected K6 resource preflight was **read-only**: at 7.79V and idle, 1200-mode K6CB at SPI 0x100000 matches the package SHA256 `6c6e6d728b54c69d9cf89dd3dbf5618613a59c85fb059a5c397d5d05b77fc72a`, and existing IVF deck in slot15 matches SHA256 `5d05fa9fb0194eee04356c3cb4b15471c780d0a915ee67063ed91a54e64ce9f6`. Current slot25 `MIC IRQ` and slot30 `SOTTO MIC` were not modified. The radio currently has **no empty module slot**.

## Flashable staged deliverable (NOT installed)

`/home/tyler/flux-bench/sotto-gp0-e2e-20261125/`, tar archive `/home/tyler/flux-bench/sotto-gp0-e2e-20261125.tar.gz`.

Includes `firmware-gp0-tx.bin`, `module.bin`, `module-bundle/{module.bin,manifest.json}` (slot-independent `k6_install.py load` format), `sotto-1200-k6cb.bin` and `sotto-ivf.k6sc` reference resources, `release.json`, `README.md` and SHA256 checksums. The build is **cable-only**, without a personal module signing key embedded. `tools/k6_sotto_gp0_release.py` recreates the package while enforcing firmware size, module scratch and hash/manifest checks; rejects oversized/ambiguous images. The module installer still requires deliberate slot selection/replacement and real hardware validation.

## Definition of done remaining

1. On a newly authorized attended dummy-load bench run, back up all current firmware SPI and the selected occupied module slot before any flash/overwrite; verify PTT/serial arbitration and actual RF restoration. Avoid overwriting slot25/30 recovery diagnostics.
2. Flash the exact linked candidate and test **real GP0 PCM ISR while the actual Sotto encoder performs external SPI codebook reads and asynchronous FSK TX**. Inspect IRQ peaks, lost halves, PCM queue high-water, module return status, and SDR IQ capture. If the 90µs half-buffer deadline is missed, optimize the encoder/cache/ISR before acceptance.
3. On actual speech, verify complete independent SDR Sotto decoding into audible PCM, including a mid-block PTT release flush, FDV1 end-of-session, proper TX idle/dekey and restored RFIC/audio state. The measured 1kHz vs 2kHz gain asymmetry needs audio fidelity characterization.
4. If the user requires **standalone K6 receiver** rather than the existing RTL-SDR decoder, implement and memory-qualify the native 4800-baud RX/decoder/speaker path in a follow-up feature; it is excluded from this TX-only image to fit flash/RAM.

**Do not label these offline tests as a physical two-way Sotto voice pass or silently merge to main.**
