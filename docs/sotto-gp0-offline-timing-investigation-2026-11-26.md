# GP0 Sotto physical engine abort — offline timing investigation

**Date:** 2026-11-26. **Work mode:** read-only physical radio; only offline Unicorn RF/PCM simulation and repository changes. **Do not flash these experiments.**

## Physical baseline and unresolved question

- K6 B-chip, latest KUL2 firmware with GP0 microphone service. The `SOTTO GP0` module previously returned `GP0 ENGINE` during a human-held dummy-load PTT test, after the firmware `mic_stream(op=8)` successfully accepted the DMA startup. The SDR decoded zero valid or rejected Sotto packets. `sl_engine_tick` returns a generic negative result for GP0 PCM lost, SPI codec failure, queue-state errors, and FSK start/poll failures; **the physical abort reason is still unknown**.
- A later over-tight stage-3 diagnostic hid the initial module display and removed early keypad cancellation checks to fit memory, making launch appear hung (a real freeze cannot be excluded). It was rolled back on hardware to the stage-2 version. Exact slot-24 library readback and peer slots 25/30 passed. The user has been advised **not** to transmit again with the diagnostic at present.
- Previous physical `MIC IRQ` test recorded 6,826 DMA half interrupts, 5,006 8kHz samples, 62 completed PCM blocks, no loss and 2,365 peak timer ticks **under a synthetic workload, not the Sotto encoder's live SPI traffic**. These are not evidence of simultaneous streaming correctness.

## Reproduced model failure under more realistic flash timing

The actual compiled Cortex-M0 module, pinned codec and codebook, virtual GP0 capture, FSK engine and independent virtual receiver were run with a 3,280-sample 0.41s synthetic audio input; a continuous 800ms mic-bias delay is present in the module, although the reported `samples` counter starts after it. Model assumed CPI=1.5, 2,400 modeled ADC/interrupt cycles per output PCM sample, 34µs fixed SPI request overhead, and varied only additional SPI per-data-byte cost (`spi_byte_us`). These values are **not measured physical latencies**.

| SPI per byte (µs) | Session completed | PCM high-water (4 blocks total) | PCM lost | Sample count | Codec frames | RF BEGIN/VOICE/END |
|---:|:---:|---:|:---:|---:|---:|---|
| 0.0 | Yes | 2 | No | 3,280 | 11 | Complete |
| 0.5 | Yes | 2 | No | 3,280 | 11 | Complete |
| 1.0 | Yes | 3 | No | 3,280 | 11 | Complete |
| 1.5 | Yes | 3 | No | 3,280 | 11 | Complete |
| 2.0 | **No** | **4** | **Yes** | **1,280** | **0** | Incomplete, 0 valid packets |
| 4.0 | **No** | **4** | **Yes** | **960** | **0** | Incomplete, 0 valid packets |

At 2µs/byte, the virtual PCM queue overflowed after 1,280 8kHz samples (160ms). With the module's 800ms bias settling interval, that's ~960ms from start of TX setup, remarkably similar to the operator's ~one-second indicator before abort. The virtual modulator actually began a partial RF burst ~125ms into PCM capture but produced **no CRC-valid packets** before abort. Therefore zero decoded SDR packets alone cannot distinguish missing RF from a short incomplete burst.

A complete 0.41s modeled transaction reads **97,104 resource bytes overall**, **62,170 bytes while keyed**, using 2,562 SPI resource calls. The 62KB/0.41s active read rate is ~152KB/s. With a 4-block PCM queue (~40ms of audio) and heavy Sotto encoding, modest increases in SPI byte-transfer/driver costs can make the producer overrun the consumer. Actual flash SPI1 uses software-polled `try_transfer_byte()` (checks TXE, RXNE and BSY for every byte) and `read_bytes_checked()`; it does not explicitly disable interrupts. Firmware initializes SPI1 with divider 4, 96MHz system clock. This is **not a clock/latency measurement**: APB prescaler and software/reg latency need verification. Even uninterrupted SPI clock time is only a lower bound, not the callback wall time.

For the simulation, a **2µs/byte** resource transfer penalty caused loss even when modeled ADC load was only 2,400 cycles/sample. This is a plausible but unverified performance bottleneck. A passing `spi_byte_us=0` regression is **not** a meaningful hardware-realtime gate.

### Hypothetical exact-range flash cache sensitivity

With the same 2µs/byte failure case, a modeled exact-range LRU cache (12 bytes metadata per entry, 2µs hit cost) yielded:

| Cache budget | Result | Cache hits | PCM high-water |
|---:|---|---:|---:|
| 256 B | PCM overflow | 2 | 4 |
| 512 B | PCM overflow | 3 | 4 |
| 1,024 B | PCM overflow | 7 | 4 |
| 1,280 B | Session completed | 99 | 3 |
| 1,536 B | Session completed | 240 | 3 |
| 1,792 B | Session completed | 464 | 3 |
| 2,048 B | Session completed | 493 | 3 |

This identifies a **model-specific working-set cliff between 1,024 and 1,280 bytes**, not a deployable cache solution. The current 4,096-byte scratch allowance is already consumed by 4,080 bytes of codec+engine, leaving only 16 bytes in the experimental diagnostic. Adding a 1,280-byte cache requires a separate audited SRAM allocation, an algorithmic memory reuse/reduction, or a substantially different layout. It is unsafe to append a cache to the loadable module without reclaiming that memory. Evidence: `recovery/gp0-offline-cache-effects.json` and `recovery/gp0-offline-cache-threshold.json`.

## Tests added

`tests/test_k6_sotto_gp0_spi_latency_budget.py` executes the actual ARM module under the pinned virtual setup and explicitly establishes both completed sessions at 0.0–1.5µs/byte and safe fail-closed PCM overflow at 2.0µs/byte. It does not assert physical operating limits. The recorded reproduction JSON is at `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-offline-sensitivity.json` and `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-offline-byte-threshold.json`.

## Prioritized next steps

1. **No further blind module swaps.** First establish actual `resource_read` latency distributions and GP0 ISR counters in a *non-transmitting*, human-attended diagnostic with safe serial-safe host access. Avoid the current 16–48-byte margin diagnostics and preserve the current rollback point. Preserve exact no-bypass keypad/PTT cancellation.
2. Measure flash SPI throughput during audio capture, including 20-byte codebook and 128-byte centroid reads, and verify DMA IRQ9's peak latency, missed-half count, `SOTTO_GP0_ERROR`, PCM producer/consumer high-water, and encoder `io.fault` **independently**. The firmware already maintains several RAM0 ISR counters; surfacing them without adding module scratch is more valuable than more LCD strings. There is no deployed readback path for these specific counters in the current cable-only build; implementing one requires an audited firmware change, and no hardware upgrade has been approved here.
3. Optimize the *observed* bottleneck. Candidate directions: reduce external SPI transaction count, batch repeated codebook/vector accesses, improve the polled SPI byte loop, or reorganize external tables into sequential reads. A small hypothetical cache alone is not guaranteed to help; prior sweeps had low hit rates. Retest against realistic measured per-byte costs and an external PCM queue before changing hardware firmware.
4. Check the 90µs GP0 DMA half-buffer interrupt deadline under contention with flash bus and the RFIC, not just mean end-to-end throughput. Keep a short clear error path that dekeys and preserves RX. Only after a measurable fix and a safe recovery path should a new attended dummy-load/SDR trial be considered.

**Current status:** offline susceptibility reproduced, primary physical cause not proven. Base firmware and restored radio module are left untouched. Do not claim full Sotto live TX/RX success.
