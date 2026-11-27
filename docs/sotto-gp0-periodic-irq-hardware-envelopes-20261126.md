# Sotto GP0 simulator: bounded periodic IRQ timing based on first AA3 physical regression

Date: 2026-11-26 Pacific. Branch `research/sotto-gp0-hardware-timing-sim-20261126`. **Entirely offline**, no radio transmission, flash, microphone recording, serial query, or bench lock.

## Why the previous virtual GP0 timing was too optimistic

The existing `tools/k6_sotto_virtual_link.py` runs **real compiled ARMv6-M optimized Sotto TX code**, the pinned codebook, virtual FSK and independently decoded RF. However it represented microphone service CPU contention by a *smooth, configurable number of cycles per generated 8kHz PCM sample*. The actual K6 computes 8 PDM-popcount groups in **each of roughly 10,907.5 DMA interrupts per second**, while the encoder runs in the foreground. A small queue can overflow because the interrupts steal time in bursts.

The new opt-in `tools/k6_gp0_irq_timing.py` implements a deterministic fractional interrupt clock, time charged for each IRQ event, carried-over interrupts when preemptions lengthen foreground work, real fractional PCM sample production, and explicit "interrupt runs within a wall sleep" semantics to avoid double charging. Existing legacy `adc_cycles` behavior remains the default and cannot be combined with the new cost option. `trial(...,gp0_irq_cycles=N)` reports virtual IRQ count, charged CPU cycles, and the first modeled overflow time. The feature is valid only for the GP0 TX module.

**It does not execute the linked Rust DMA ISR**, emulate DMA memory transactions, or compute a measured average IRQ cycle cost. The injected cost is a **hypothesis**, not a ground-truth hardware benchmark.

## Physical reference, never confused with simulated predictions

The user first heard intelligible voice on a prior unfiltered MCU image, but the experimental AA3 filter later aborted with `GP0 ENGINE`. Both images used the *same* signed slot24 optimized GP0 module serial `1795751383`. Direct boot-local GPF1/GPT1 taken before cable reboot on the AA3 run established **PCM queue full**, no missing DMA halves, no codec resource errors, and no FSK callback errors. Those physical records are committed separately in `docs/evidence/sotto-gp0-aa3-first-physical-queue-overflow-20261126.json` and the original protected trace is kept in the private recovery directory.

| Measurement | Unfiltered successful run | AA3 failed run |
|---|---:|---:|
| Physical result | 202 encoded frames | -12 GP0 ENGINE |
| GP0 PCM lost | no | yes, queue full |
| Peak TIM17 IRQ ticks | **3,147** | **3,488** |
| Peak IRQ µs at nominal 96MHz | ~32.78 | ~36.33 |
| Approx. live capture before abort | sustained microphone session | **240.11 ms** |
| Resource callback failures | 0 | 0 |
| FSK callback failures | 0 | 0 |
| Resource callbacks | 43,342 over long successful session | 1,034 before abort |
| Accumulated callback elapsed | unknown here | ~72.02ms modulo timer wraps |

The nominal inter-IRQ budget is **~91.68 µs (8,801 CPU cycles)**. Therefore the maximum measured ISR cost was ~35.8% and ~39.6% of *one* IRQ period. **This is NOT measured average utilization**. If the entire 341-tick difference happened on every IRQ, it would cost ~3.87% of total CPU; the observation of a peak alone does not support that extrapolation.

## Actual compiled-module sensitivity test

The simulator runs the exact 9,760B ARM `fast4+RAM-micro` module compiled from pinned Sotto, SHA256 `7c91f220ad6d7577dfac86cd2eaf3d53a6a14b2ad671aa870596031025092121`. It uses a 410 ms ideal PCM fixture containing 1 and 2kHz tones, the measured streaming SPI estimate **6.19213µs + 0.940394µs per byte**, a stated modeling assumption of **60 µs per RFIC register**, and the existing seven-stage cooperative FSK model. The MCU firmware itself and its genuine PDM ISR are *not* executed in this TX/FSK simulation.

Twelve bounded scenarios use assumed ARM CPI 2.5 / 2.75 / 3.0 and constant per-IRQ cost set to 75% or 100% of each image's observed **peak**:

| CPI assumption | Working unfiltered (100% peak) | AA3 (100% peak) |
|---|---|---|
| 2.5 | completes, no PCM loss | completes, no PCM loss |
| 2.75 | completes, no PCM loss | completes, no PCM loss |
| **3.0** | **completes, no PCM loss** | **queue overflow after ~400.2ms** |

All scenarios at 75% of either profile's peak passed. The complete structured record is `docs/evidence/sotto-gp0-periodic-irq-timing-envelope-20261126.json`.

This identifies a **sensitivity cliff** consistent with the physical outcome, but does **not** replicate the actual failure time: AA3 overflowed at ~240ms on silicon versus ~400ms in the slowest tested simulation. It is not legitimate to adjust CPI solely until the failure time matches one test. The model still lacks measured mean/variance per IRQ, precise instruction-cycle costs and flash/bus/interrupt contention, real GP0 PDM, analog microphone route/noise and measured Sotto encoder workload per audio content.

## Reproduce

```sh
/home/tyler/.local/share/flux-rf-venv/bin/python -m pytest -q \
  tests/test_k6_gp0_irq_timing.py \
  tests/test_k6_sotto_gp0_hardware_envelope.py \
  tests/test_k6_sotto_gp0_micro_fast_combined.py \
  tests/test_k6_sotto_gp0_spi_latency_budget.py

/home/tyler/.local/share/flux-rf-venv/bin/python \
  tools/k6_sotto_gp0_hardware_envelope.py \
  --cpi 2.5 2.75 3.0 --peak-fractions .75 1.0 \
  --json /tmp/gp0-hardware-envelope.json
```

The CLI refuses to overwrite an existing `--json` target. No serial device is opened. The metadata-only option reads and validates the published AA3 telemetry without launching the compiled ARM code.

## Follow-on accuracy investments, in impact order

1. **Run the *actual linked Rust GP0 IRQ handler* in the MCU ARM emulator alongside the module** (or a cross-checked event trace), consuming the real 1,024-byte DMA buffer and modeling high/low half IRQs. This is the largest missing source of ISR-work and occupancy accuracy. Compare compiled ARM instruction counts and measured TIM17 peaks.
2. **Record low-overhead ISR average/distribution in a future attended MCU-only diagnostic** after freeing RAM0; current AA3 has just **12 protected RAM0 bytes available**, so do not add an unsafe histogram, extend the ring or reduce protected stack. Retain GPF1/GPT1 and the fail-closed dekey path.
3. **Calibrate GP0 PDM→PCM signal quality from signal-generator measurements**: 1kHz and 2kHz at known Vpp and a generator-off baseline. Compare measured output spectra/alias/codec voicing and injected PDM model rather than merely measuring Sotto decoder output. Record exact RF/IF offsets and signal level. A spectral pass still does not imply real-time CPU pass.
4. **Use real codebook-read distributions and foreground encode runtimes** (not only two-point SPI access calibration); account for PA/FSK preload timing, flash wait states, and static module scratch/MCU RAM0 gates before attempting another hardware image.
5. Combine the timing producer and analog model with the existing `rfsim` measured channel profiles; neither RF channel noise nor an ideal decoded PCM waveform can repair an MCU queue overflow.

**Hardware state:** The operator explicitly requested restoration of the known-good 118,408-byte MCU image; its 116/116-block bootloader flash and post-flash KUL2 catalog/status checks succeeded. The same slot24 SOTTO GP0 9,760-byte module serial `1795751383` is preserved, and the bench lock was released. This simulator work performed **no** radio flash, transmission, hardware control, or SPI write.
