# GP0 ISR + Sotto SPI optimization: physical-overflow follow-up

**Date:** November 26, 2026. **Branch:** `optimize/sotto-gp0-isr-20261126`. **Scope:** opt-in Cortex-M0 DMA ISR acceleration + already measured faster resource-only SPI reads, with the verified firmware-owned GPF1 trace. No change to the signed 9,784-byte SOTTO GP0 module, its 48-byte arena scratch margin, KUL2 catalog, radio bootloader, DMA configuration, TX timing guard or on-air waveform format.

## Motivation

The previous attended dummy-load test captured a valid GPF1 error:
- GP0 capture had started; 1,310 DMA interrupts.
- `gp0_dma_error=3`, `pcm_lost=true`; zero missed DMA halves, zero codec resource callback errors, zero FSK callback errors.
- Peak ISR **4,866 TIM17 ticks**, about 50.7 µs nominal at 96 MHz; **peak, not average**.
- Code 3 occurs when the 4×80-sample PCM queue cannot accept a new sample or when the decimator receives invalid input. Valid sampled `fs=87260` and bounded 0–64 PDM bit counts support queue overflow as the strongest explanation.
- The measured original SPI driver takes 36.72 µs for 20-byte reads and 193.75 µs for 128-byte reads, versus 25.00 and 126.56 µs with the streaming driver. The earlier hardware benchmark showed **32–35% faster reads with matching data**; no SPI flash writes.

Evidence already committed:
`docs/evidence/sotto-gp0-gpf1-physical-20261126.json` and
`docs/evidence/sotto-gp0-calibrated-replay-20261126.json`.

## Changes

Opt-in `K6_SOTTO_GP0_FAST_ISR=1` requires `K6_SOTTO_MIC=1` and `K6_RAM0=1`; default **0**, preserving legacy ISR behavior and all builds that do not enable the flag.

1. **Exact fixed-point decimator division:** For the physical `fs=87260` rate, replace the repeated variable-length software unsigned divide `udiv(abs(area)<<8, fs)` with a proven exact reciprocal algorithm, using two 16×16→32 multiplies and one remainder correction. `MAGIC=floor(2^32/87260)=49220`. It returns `floor(n/87260)` for all unsigned 32-bit inputs. No 64-bit multiply, floating point, table or persistent state. Any other PDM sample rate still uses the original `udiv`.
2. **Unrolled volatile PDM popcount:** Keep all 16 masked volatile 32-bit reads per 64-bit PDM group, in exactly the original order, but unroll their inner loop. This removes a repeated branch/index operation from the eight groups processed per DMA-half ISR without changing bit semantics or DMA ownership. The default ISR retains the original 16-iteration loop.

Only `firmware/k6-aura/sotto-api30-gp0-capture.rs` and an opt-in feature gate in `firmware/k6-aura/build-fd6818b.sh` changed; the GPF1 and fast SPI code is reused unchanged from prior validated commits.

## Verification

- `tests/test_k6_sotto_gp0_fast_divider.py`: the exact function from the real firmware source is compiled with host Rust and tested at every quotient discontinuity over the entire u32 range, 500,000 deterministic random values, selected physical extremes, and 960,000 full state-tracking decimator groups across silence, extrema, random audio, and transitions. Optimized output and internal phase/area/DC/clipped/sample-count state must be **bit-identical**.
- `tests/test_k6_sotto_gp0_fast_popcount.py`: the exact volatile-read function from the real firmware source is host compiled and compared with the original 16-read loop on 350,512 PDM word groups, including single-bit, all-ones, all-zero, alternating and deterministic random patterns.
- Full focused GP0/Sotto SPI/capture/RF regression: **80 passed, 2 skipped**. Existing GP0 end-to-end virtual RF decode remained functional.
- Complete pinned Aura+Sotto build with exact recovered field profile, opt-ins `K6_SOTTO_FAST_SPI=1`, `K6_SOTTO_GP0_FAST_ISR=1` and `K6_SOTTO_GP0_TRACE=1`: **117,628 B MCU firmware**, **5,252 B remaining**. GPF1 signature present once; SPB1 timing-bench signature absent. Signed SOTTO GP0 module remains unchanged.
- RAM audit: arena base `0x20001930`, arena **14,976 B**, main stack reservation **2,812 B**, protected RAM0 **3,792 B used / 48 B free**. No arena or stack shifts.
- Independent offline `tools/k6_sotto_gp0_fault_gate.py`: **READY_FOR_ATTENDED_REVIEW**, zero failures (not permission for automatic flash).
- Full saved diagnostic firmware: `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fast-isr-spi-trace/gp0-fast-isr-spi-trace-offline-only.bin`; SHA256 **89437801bbb3c6bdce3ed1ee4c232086f0de5340a3abd20ee419b4f2b9d22458**.

The actual ISR code in a pre-optimization pinned ELF occupied 648 bytes; the fast reciprocal-only code compiled to 696 bytes, and combined reciprocal+unrolled version to 808 bytes. **Code size is not execution-cycle measurement**. The hardware timing and PCM overflow outcome of the new ISR have not yet been measured.

## Safety and next measurement

The current radio firmware is the previously installed corrected GPF1 diagnostic unless explicitly reflashed. Preserve:
- Known-good fielded firmware: `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin` (SHA256 `8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d`).
- Full independently calibrated 2 MiB SPI backup: `recovery/k6-spi-before-sotto-spi-latency-test-20261126.bin` (SHA256 `18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd`).
- Existing slot24/25/30 modules remain untouched.

A new complete clean uninstrumented build with fast ISR, fast SPI and GPF1 switches all OFF **passed** the original-image comparison: 116,796 B, unchanged RAM0, arena and stack audit, and bitwise identity to the fielded firmware except its exact eight-byte GNU linker build ID. Baseline SHA256: 1594fad82e9456ef5d83bc8699bcc94ebcca1273f2057273b7d487f35f1ca8b6. Evidence is under recovery/gp0-isr-default-baseline. This confirms the default firmware is unchanged.

Install the opt-in candidate only after successful build and memory gate. Then check cable-protocol boot, catalog and an empty GPF1 report, without transmitting.

For a meaningful live test, the operator must keep the radio on a dummy load, unplug the programming cable, launch SOTTO GP0 and hold physical PTT for 3–5 seconds, release, reconnect **without battery power cycle**, and retrieve the boot-local `GPF1` report as the **first** cable command. Compare error, IRQ count, and peak ISR ticks to the pre-optimization measured 4,866-tick peak, along with SDR decoding. If no fault, success still requires correct BEGIN/VOICE/END RX packet decoding rather than merely long TX LED time.

**Do not claim the PCM overflow is fixed from offline simulation alone.**
