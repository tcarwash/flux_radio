# K6 GP0 CIC2/R8 — low-cost first decimation stage (offline only)

Date: November 26, 2026 Pacific. Independent research branch `research/sotto-gp0-cic2-decim8-20261126`, based on the previous FIR11/ARM IRQ test branch. **No physical radio changes or RF transmissions.** The working K6 MCU image and signed slot24 Sotto GP0 module are untouched.

## Mechanism

The original 87,260-Hz GP0 density-popcount stream reaches `dec_push` eight times per DMA half-interrupt. The existing fractional decimator and Q8/DC conversion can consume a large fraction of CPU even when the codec and FSK are doing useful foreground work. AA3 adds three high-rate IIR stages and, in the first physical experiment, ran out of PCM queue headroom after 240.11ms.

CIC2 uses **two 16-bit modular integrators** on each of the raw popcounts and **two comb differences** once per eight raw groups. The resulting stream has an effective group rate of 10,907.5Hz and a squared 8-tap moving-average response. The existing fractional converter then runs from that intermediate rate to 8kHz, with exact fractional timing `phase += 64,000` per intermediate group against a retained `fs=87,260`. It computes the original signed Q8 scale with an integer `mag<<2`.

Four u16 state words and a u8 counter occupy 12B after alignment, the **same** decimator state budget as AA3; no new DMA samples, PCM blocks, SPI memory, heap, module scratch, or 64-bit arithmetic. The firmware option `K6_SOTTO_GP0_CIC2=1` is default-off, requires mic + RAM0 + fast ISR, excludes AA3 and either FIR11 option, and refuses non-87,260Hz input.

The mathematical CIC2 output equals a two-stage 8-sample rectangular convolution sampled every eighth high-rate input group. The real Rust decimator was cross-checked bit-exact against an independent Python implementation on seeded binomial, all-ones, all-zeros and impulse-like input. A 100,000-group independent modulo-16bit Python CIC model matched ideal unbounded integer convolution exactly, including wraparound and both extreme DC inputs. CIC2's output is bounded by ±4,096, safely representable as i16; all weighted integration and Q8 scaling intermediates fit 32 bits.

## Linked Cortex-M0 build and interrupt execution

The opt-in full MCU image built successfully for Thumbv6m, with the identical reconstructed feature profile used for AA3/FIR11 offline comparisons:

- **117,380B firmware image**; SHA256 `8f75a78c336d9a85f49dc5fc67f3d65bd44c5f59ab2a666799c9ceddc1390b9a`.
- Protected RAM0 **3,804B used, 36B free**; module arena 14,976B and main stack reservation 2,812B unchanged.
- Linked production Rust `sotto_gp0_dma_irq` symbol size **828B**.
- 96 synthetic DMA half-interrupts executed directly in the linked Cortex-M0 image, with correct PDM→PCM writes and no ring loss: **1,142 mean executed instructions/IRQ** on tone, **1,141.65** on silence.
- Identical harness and feature profile previously measured **1,103.54 instructions/IRQ** in legacy bypass and **1,231.54** in AA3.

This is only about **3.5% more executed instructions than bypass** and approximately **7.3% fewer than AA3**, but it is not clock-cycle-accurate and excludes encoder foreground, DMA bus latency, and actual ROM/flash/SRAM contention. It does not establish an overflow-free physical recording.

## Spectral caveat: multirate aliasing can shift frequency

**Do not score CIC2's 9kHz rejection only at the 1kHz output alias**, as would be appropriate for a single direct resampler. CIC2 first decimates to ~10.9kHz, so 9kHz interference can appear around **1.91kHz**, not necessarily at 1kHz. The initial spectral oracle's simple 8kHz alias calculation would have made a false favorable claim.

A corrected wideband analysis takes the **largest 80–3900Hz output FFT component** after transient removal, not only the direct-decimation alias bin. The machine-readable evidence is `docs/evidence/sotto-gp0-cic2-decim8-offline-20261126.json`, which stores the actual peak frequencies and relative amplitudes. On the deterministic PDM-count model, the approximate changes in the strongest in-band component relative to the unfiltered converter are:

| High-rate input | AA3 | CIC2/R8 |
|---|---:|---:|
| 1kHz | -0.8dB | -0.4dB |
| 2.8kHz | -5.2dB | -3.5dB |
| 3.5kHz | -7.4dB | -5.6dB |
| 9kHz | -22.0dB | **-10.8dB** |
| 15kHz | -16.5dB | -15.8dB |
| 17kHz | -20.1dB | -10.5dB |

The mixed-band behavior and quantized PDM test source prevent interpreting these numbers as a physical audio/SNR measurement. CIC2 is much cheaper in the interrupt, but its **worst-case in-band alias rejection is not sufficient to claim equivalence to AA3**. It remains offline-only.

## Next research gate

Try a **third CIC integrator/comb stage with R=8**. Use bits-32 instead of twice(bits-32) internally so the stage's DC gain of 512 remains within ±16,384 in signed 16-bit arithmetic, then normalize the fractional converter by zero additional shifts. The prospective six 16-bit state words plus counter consume 16B after alignment, versus 12B for CIC2; audit this against the protected RAM0 envelope. Test the actual linked IRQ instructions and the *correct* strongest-band FFT metric, especially for 9, 15 and 17kHz interference. Do not install any experimental image without explicit new hardware qualification and operator approval.

**Hardware remains on its previously working 118,408B MCU image, and the user's signal-generator captures are independent of these offline tests.**
