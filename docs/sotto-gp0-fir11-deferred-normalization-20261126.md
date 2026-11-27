# GP0 FIR11 deferred normalization — real linked ARM IRQ instruction profiling

Date: November 26, 2026 Pacific. Branch `research/sotto-gp0-fir11-late-20261126`. **Offline only; no radio flash, PTT, UART, SDR, or microphone input accessed.** The K6 has already been returned to the previously working 118,408-byte MCU image. Signed slot24 Sotto GP0 remains unchanged and is not modified by this experiment.

## Observation motivating this candidate

The AA3 3-pole filter was physically installed on K6 and returned **GP0 ENGINE** after ~240.11ms of active capture. Boot-local GPF1/GPT1 independently established **4/4 PCM queue full** with zero DMA-half errors, zero codec resource errors and zero FSK failures. AA3's observed peak was 3,488 TIM17 ticks vs 3,147 on a successful, unfiltered run using the *same* 9,760-byte module, but peak ISR ticks alone cannot establish mean CPU utilization or isolate AA3 as the root cause.

FIR11, the alternative 11-tap moving average, preserves speech energy better than AA3 at 2.8–3.5kHz but previously performed signed division by 11 **at the GP0 raw-group rate (~87,260/s)**. It might be too expensive for an already overcommitted Cortex-M0.

## Algorithm: accumulate unnormalized FIR11 and normalize only on output

Preserve the 11-byte i8 circular history, signed 16-bit sum, and fractional ~87.26kHz→8kHz area integrator. Inside every high-rate input group, accumulate `sum(raw[-10..0])` **without division**. The signed integrated `area` has absolute bound `11*64*fs <= 112,640,000` for the largest accepted 160kHz group-rate.

Only when an 8kHz PCM sample is emitted, compute exact integer

`floor(abs(area)*256/(11*fs))`

without a 64-bit product. Divide `abs(area)` by `fs` into quotient `q` and remainder `r`, get `floor(r*256/fs)` using the existing *exact reciprocal* fast-divide when fs is 87,260, then divide the combined `q*256+fraction` by the constant 11. All intermediates fit 32-bit unsigned; there is no heap, codebook, ISR-time codec operation, DMA buffer change or output sample-format change.

The original FIR11 is still available unchanged under `K6_SOTTO_GP0_FIR11=1`; delayed-normalization FIR11 is a separate **default-off, mutually exclusive** `K6_SOTTO_GP0_FIR11_LATE=1` experiment. Requires GP0 mic, expanded RAM0 and fast ISR; cannot combine with AA3 or original FIR11.

### Fixed-point and audio comparison

The extracted *actual Rust GP0 decimator*, compiled independently with `rustc`, matched an independent Python reference **sample-for-sample** at 35,000; 84,960; 87,260; 110,000; and 160,000 groups/s, including seeded 64-binomial input, all-zero, all-one and mixed extremes.

With a one-second seeded 1kHz synthetic density-modulation fixture, original vs deferred FIR11 produced 8,000 PCM samples with waveform correlation **0.99999964**, error RMS **6.68 PCM counts**, max absolute error **24 counts**; these differences are explained by the delayed quantization, not RF/codec changes. The 1, 2.8, 7, 9 and 15kHz spectral regressions stayed within **1 dB** of original FIR11. This is not measured microphone SNR.

### Same-source full Cortex-M0 release builds

The exact pinned Sotto submodule and a reconstructed matched firmware feature profile were used for all four modes. These outputs are **not byte-identical to either installed 118,408-byte legacy or previously tested 118,448-byte AA3 images** and **are not to be flashed**. Private full ELF/BIN, build logs and 2-MiB SPI calibration backups remain off GitHub.

| Pre-decimation mode | Full image | Protected RAM0 used | Free RAM0 | Compiled ISR .text size |
|---|---:|---:|---:|---:|
| Legacy bypass | 117,360B | 3,792B | 48B | 808B |
| AA3 three-pole | 117,400B | 3,804B | 36B | 848B |
| Original FIR11 | 117,440B | 3,808B | 32B | 888B |
| **FIR11 deferred** | **117,556B** | **3,808B** | **32B** | **1,004B** |

Deferred FIR11 uses **no extra protected RAM compared to original FIR11**, but its instruction logic increased flash code even though it lowers high-rate execution work. RAM0 margin in the *actual deployed AA3 feature profile* was just **12B**; the extrapolated FIR11 margin could be only **8B** and needs a separate full-profile audit before physical deployment. Main-stack reservation remains 2,812B; KUL2 module arena remains 14,976B.

## Important advance: executed real Rust IRQ handler, not just a size proxy

`tools/k6_gp0_linked_irq_profile.py` loads the ELF-matched **actual** Thumbv6m MCU firmware image into Unicorn Cortex-M0 and directly executes the production `sotto_gp0_dma_irq` entrypoint using the linked `STATE`, raw DMA buffer, PCM ring, and GPF1 counter globals. The harness validates ELF/image byte equality, feeds 96 deterministic alternated DMA half-buffer interrupts (8 PDM groups per half), observes 70 emitted 8kHz PCM samples, and asserts correct uninterrupted ISR count and no PCM loss.

**Mean executed instructions/IRQ — 96 linked-ISR runs, same build flags**

| Mode | Synthetic 1 kHz tone | Near-silence |
|---|---:|---:|
| Bypass | 1,103.54 | 1,103.19 |
| AA3 | 1,231.54 | 1,231.19 |
| FIR11 original | 2,378.51 | 1,512.20 |
| **FIR11 deferred** | **1,382.26** | **1,300.45** |

Deferred FIR11 runs approximately **42% fewer instructions than original FIR11 on the synthetic tone**, and **14% fewer on near-silence**. The original FIR11's expensive high-rate signed division varies substantially with input amplitude, while late normalization is more predictable. However, deferred FIR11 still runs about **12% more instructions than AA3 on that tone**, and AA3 physically overflowed. Therefore this prototype is **NOT READY TO FLASH**.

These are executed instruction counts, **not Cortex-M0 cycles**, TIM17 measurements, average CPU usage, or proof of a real-time-pass. The test does not emulate bus contention, nested IRQs, encoder foreground, true PDM noise shaping or the analog MICIN route. The absolute physical overflow time cannot be predicted from this test.

Machine-readable results: `docs/evidence/sotto-gp0-fir11-late-linked-isr-instruction-profile-20261126.json`. The full ELF, 96-half trace, raw voice WAVs, SPI/calibration backup and original microphone captures remain in private recovery, not in this branch.

## Next filter optimization and acceptance gates

- Explore **8-tap power-of-two moving average** with constant shift normalization, optionally a short first-order smoothing pole. A FIR8 window's stopband is weaker than FIR11, but a cheaper ISR may be a better overall tradeoff; test both 9 and 15kHz alias rejection against AA3 and voice loss at 2.8kHz.
- Keep the exact linked-ISR instruction counter in the regression suite. Reject any candidate that exceeds the known AA3 compute budget unless its real-time behavior can be independently explained and measured.
- Obtain **measured average IRQ cycles/distribution**, not just the maximum, when a future low-overhead hardware diagnostic is safe; preserve the 12B protected RAM0 headroom warning.
- Feed 1kHz, 2kHz and generator-off microphone recordings into the physical audio/PDM-path model, comparing spectrum, clipping, gain and voicing with trace timestamps. **Only test a new on-radio filter after it passes both sustained PCM throughput and sound-quality gates.**

This work leaves the previously working physical radio and SDR monitor untouched.
