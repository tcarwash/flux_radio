# K6 Sotto: non-transmitting SPI latency probe (offline candidate)

**Date:** 2026-11-26. **Status:** implemented and compiled for Cortex-M0, NOT installed on a radio and NOT RF-qualified. The user's K6 remains on the restored slot-24 module and original bootable firmware.

## Goal

Previous live Sotto GP0 transmit tests aborted after about one second without decoded RF. The complete software pipeline is reproducible in the virtual RF simulator, but a 2.0 microseconds/byte modeled SPI transfer cost causes PCM ring overflow after 1,280 samples (160ms into capture), while <=1.5 microseconds/byte succeeds. This is evidence of sensitivity, not proof that the hardware SPI is slow.

The normal programming-cable 64-byte flash-read command cannot separate peripheral latency from ~115,200-baud UART framing and host scheduling. We need an *on-device* timing result for representative 20-byte and 128-byte codebook reads.

## New opt-in diagnostic

Files:
- firmware/k6-aura/sotto-spi-bench.rs.inc — read-only device-side benchmark, codebook range 0x100020 onward.
- firmware/k6-aura/apply-sotto-spi-bench.py — reversible generated-source overlay.
- firmware/k6-aura/build-fd6818b.sh — explicit K6_SOTTO_SPI_BENCH=1 gate; requires K6_SOTTO_FAST_SPI=1 and K6_SOTTO_RESOURCES=1.
- tools/k6_sotto_spi_bench.py — host-side parser and command, which requires --ack-reboot.
- tests/test_k6_sotto_spi_bench.py — source overlay/decoder/read-only tests.

The cable-only command is **0x73** (one request with empty payload, wire addr 0). It is only present if built with the benchmark cfg and never accesses the microphone, PTT, RF transmit, codebook installer, library flash writes or firmware programming. It uses the existing memory-safe 0x100020 codebook region. The caller is already in a cable session with RF disconnected.

For each length (20 and 128 bytes), the device times 128 consecutive **baseline read_bytes_checked** operations and 128 consecutive **streaming read_bytes_stream_checked** operations. Addresses sweep deterministic offsets throughout the existing Sotto codebook. Time is measured by TIM14 uptime at 100 microseconds per tick. Each timed loop only folds its first/last byte into a digest; eight separate full-size 128-byte reads at different addresses are byte-for-byte compared between drivers **outside** the timed region. The result is a 24-byte binary SPB1 frame: four-byte magic; u16 sample count; u16 error flags; four u16 timing batches (100us ticks, baseline20/stream20/baseline128/stream128); two u32 digests (baseline and streaming). Errors flag baseline IO failures, streaming IO failures, digest mismatch and full-buffer sample mismatch.

The CLI decodes into JSON with average microseconds/read for both implementations, savings, flags and build ID. It explicitly notes that all loop/validation overhead and flash bus traffic are included in the measured batch times. This is a comparison of two complete resource-read routines; it does not independently measure MHz or PCM DMA latency.

The cable session forcibly interrupts receive mode and **restarts the radio at the end**. The CLI refuses to start without --ack-reboot. Example for later **after intentionally building, reviewing and flashing an appropriate image**:

    python3 tools/k6_sotto_spi_bench.py --port /dev/ttyUSB0 --ack-reboot --save spi-bench-2026-11-26.json

The current installed K6 firmware does **not** implement 0x73. Do not run this command against the current image expecting a measurement. A new cable-only firmware image would be needed for the physical test; do not auto-flash.

## Offline qualification

The instrumented Rust code has compiled for thumbv6m-none-eabi with both Sotto API28 resource callbacks and the independent TX watchdog enabled. The benchmark is gated independently so it is absent from all existing production/default builds.

The prototype's direct release build had 102,224 bytes .text and 12,900 bytes .rodata; no changes to baseline .bss or .ram0 (16,872 and 3,752 bytes respectively). Relative to the prior optimized-without-benchmark build: **+840 bytes .text and +12 bytes .rodata**, no static RAM increase. These are matched direct Cargo profiles in the already generated Aura worktree, not a complete clean-board build or physical instrument result.

## Verified offline gates

The benchmark overlay was enabled twice, disabled and reenabled without duplicate helpers. The combined Sotto GP0 virtual-RF/PCM and SPI overlay/decoder suite passed **58/58 tests**. After removing both the bench and streaming SPI overlays and recompiling with the same flags, the default Thumb ELF matched the earlier baseline **byte-for-byte**. The generated research checkout was left in the default, nonbenchmark configuration.

## Remaining gates

1. Complete clean build from the pinned upstream source (the older generated Aura workspace has an independently documented duplicate UI/stack overlay and is not safe for incremental full overlay reruns).
3. Audit cable-session stack usage, TIM14 counter behavior and watchdog, and flash/RAM headroom. Instrumented code's two 128-byte scratch buffers are **stack**, not static RAM.
4. Only after hardware owner explicitly elects to install a diagnostic image: take full SPI/firmware backups, verify exact build ID and staged image, install firmware with existing recovery procedure; issue the single read-only benchmark request and restore the preferred prior firmware. **Do not key PTT.**
5. Compare measured SPI timing against offline overflow sensitivity before choosing optimized codebook/cache work. GP0 ISR latency during real encoding still needs a separate safe telemetry path.

**No physical RF, flash, serial probe, or slot modification was attempted in this continuation.**