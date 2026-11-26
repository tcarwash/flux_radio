# Sotto GP0 streaming SPI — offline prototype (2026-11-26)

**Status: source implementation and offline tests only. NOT flashed, not hardware-timed, not merged to main.**

## Problem reproduced

The compiled ARM Sotto GP0 module aborts with PCM queue overflow in the virtual link when modeled external SPI reads cost 34 microseconds per request plus 2.0 microseconds per byte. At 1.5 microseconds per byte it completes. The failing case stops at 1,280 8kHz samples (about 160ms into PCM capture); combined with the 800ms microphone-bias delay it resembles the observed ~one-second physical abort. This is a modeled bottleneck, **not a confirmed physical failure cause**.

## Implementation

The existing firmware NOR checked-read path waits for TXE, RXNE and BSY to clear for **each byte**. An 8-bit full-duplex read can consume RXNE one byte at a time and wait for BSY **once at the end of the whole CS-low transaction**. The new streaming read uses bounded polls for TXE and RXNE per byte, 8-bit volatile data-register accesses, and a bounded final BSY poll before releasing chip select. Every timeout returns failure.

- firmware/k6-aura/apply-sotto-fast-spi.py: generated Aura source patch that adds SpiBus.try_stream_byte / try_transfer_stream and NorFlash.read_bytes_stream_checked, then connects **only** the Sotto API28 resource-read callback.
- firmware/k6-aura/build-fd6818b.sh: requires explicit **K6_SOTTO_FAST_SPI=1** and K6_SOTTO_RESOURCES=1. Default is disabled.
- When the flag returns to 0, the patch removes its own generated routines and restores the original Sotto resource-read callback. Tested toggles are idempotent and reversible, preventing hidden contamination of subsequent default builds.
- Existing module loading, KUL2 library, normal checked flash reads, flash writes, Sotto resource-open header verification, and RF routines are unchanged.
- No added static RAM, DMA, IRQ, timer or codec state.

## Offline build evidence

Both opt-in and default firmware compiled for thumbv6m-none-eabi release using identical direct Cargo feature flags, including the actual GP0 API resource callbacks, KUL2 user store, watchdog and RAM0 relocation.

| Section | Default | Opt-in | Delta |
|---|---:|---:|---:|
| .text | 101,240 B | 101,384 B | +144 B |
| .rodata | 12,888 B | 12,888 B | 0 |
| .bss | 16,872 B | 16,872 B | 0 |
| .ram0 | 3,752 B | 3,752 B | 0 |
| Estimated flash sum | 114,356 B | 114,500 B | +144 B |

The test driver is deliberately gated. **This is not a field-verified firmware build and there is no measured SPI speedup yet.** After a full enable/disable/reenable/disable round-trip, the restored default configuration was recompiled with matching flags and produced a **byte-for-byte identical ELF** to the initial default baseline. The generated research checkout was left with the experimental SPI overlay disabled.

The existing generated Aura worktree cannot safely undergo another full incremental overlay run: a build-fd6818b.sh attempt stopped on an unrelated earlier UI/API patch. After saving an exact copy of the generated pager.rs, duplicate generated module stack helpers were removed **only inside the ignored generated build tree** for an otherwise matched direct Cargo compile. The tracked source change is reproducible via the new opt-in overlay; a clean full build remains a separate gate.

## Validation

- tests/test_k6_sotto_fast_spi_overlay.py covers enable, idempotence, restore to byte-identical defaults, only-Sotto change, timeout structures, final BSY check, chip-select release, missing service and invalid feature values.
- Existing per-byte SPI sensitivity test and GP0 end-to-end virtual RF, resource/ABI, and PCM tests: **53 passing**.
- Direct baseline and opt-in target ELF files, section reports and regression logs saved under /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery.

## Next gate

Measure real flash read latency (20-byte codebook reads and 128-byte centroid reads) and DMA ISR/PCM producer counters with a non-transmitting, human-attended diagnostic that has adequate memory headroom. Then evaluate whether the new stream path gives the required improvement while maintaining flash CS/FIFO integrity. Do **not** reflash, key the transmitter, erase KUL2, or test another narrow-memory diagnostic on the K6 before a recovery and size audit. The radio remains on the restored known-bootable SOTTO GP0 module.