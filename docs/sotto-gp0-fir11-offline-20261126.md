# Sotto GP0 — FIR11 voice-preserving anti-alias alternative (offline only)

Date: 2026-11-26 Pacific. This branch derives from committed AA3 commit `4525b550` and **must not be flashed** without a fresh hardware timing/audio and memory review. The installed K6 is running the separate AA3 candidate; this branch never accesses the radio, SPI flash, RTL-SDR, or the bench lock.

## Objective

AA3's three Q7 alpha=1/4 one-pole stages provide strong 7–25 kHz rejection but attenuate 2.8 kHz voice components about 5.2 dB. To preserve consonant clarity, this experiment replaces AA3 (mutually exclusive build flags) with a sliding 11-count average at the ~87,260-Hz GP0 group rate ahead of the existing fractional 8-kHz resampler. A length-11 FIR has its first zero at ~7,933 Hz; the original fractional boxcar remains unchanged.

The alternative uses a signed 16-bit running sum of eleven i8 raw counts (each within -64..64), an 11-byte circular buffer, a byte index, and a compile-time-constant `/11` Q7 normalization. It does not increase PCM queue, heap, DMA buffer, or SPI allocation. Maximum accumulated Q7 area remains inside i32 at all accepted GP0 rates. The firmware flag `K6_SOTTO_GP0_FIR11=1` requires mic, RAM0, and fast ISR and forbids concurrent `K6_SOTTO_GP0_AA3=1`; both are default-off. No changes to the Sotto codec or signed slot24 module.

## Ideal frequency-response contrast (relative to prefilter bypass)

| Analog input | Eleven-tap FIR | AA3 three-pole |
|---|---:|---:|
| 320 Hz | -0.02 dB | -0.08 dB |
| 1 kHz | -0.23 dB | -0.79 dB |
| 2 kHz | -0.92 dB | -2.89 dB |
| 2.8 kHz | -1.84 dB | -5.16 dB |
| 3.5 kHz | -2.96 dB | -7.35 dB |
| 7 kHz | -17.61 dB | -18.01 dB |
| 9 kHz | -18.63 dB | -23.05 dB |
| 15 kHz | -24.52 dB | -34.09 dB |

This illustrates the expected **tradeoff**, not measured radio audio. The FIR is a less aggressive high-frequency suppressor and may retain speech articulation AA3 removes.

## Simulated 64-bit-binomial-PDM voice/noise comparison

The independent 1.2 s model sends a coherent 1 kHz density modulation through independent 64-bit binomial popcounts and compares post-transient fitted sine RMS to broadband residual. Simulated SNR, dB:

| Density modulation amplitude | Bypass | AA3 | FIR11 |
|---|---:|---:|---:|
| 0.005 | -14.28 | -11.95 | -12.77 |
| 0.015 | -4.93 | -2.73 | -3.57 |
| 0.035 | +2.39 | +4.70 | +3.87 |

AA3 beats FIR11 by about 0.8 dB in this *uncorrelated random-noise* model; FIR11 retains more high-frequency voice energy. These input statistics **are not recordings of the K6 microphone path** and cannot resolve whether actual static is in-band analog noise, PDM shaping, codec artifacts, or aliased interference. Do not overinterpret them as speech quality scores.

## Builds and protected-memory comparison

Both full thumbv6m Cortex-M0 release images were built on the same isolated worktree using the same pinned Sotto submodule `598f1bce`, pinned Aura source, reconstructed K6 feature profile, tracing, fast SPI, and fast IRQ. Only FIR11 versus AA3 flags differ. These reconstructed flags do *not* reproduce the AA3 image currently installed on the radio, which has additional diagnostics/profile details.

| Same profile | AA3 | FIR11 |
|---|---:|---:|
| Firmware image | 117,400 B | 117,440 B |
| Protected RAM0 used | 3,804 B | 3,808 B |
| Protected RAM0 free | 36 B | 32 B |
| Main-stack reservation | 2,812 B | 2,812 B |
| Module arena | 14,976 B | 14,976 B |

Thus FIR11 costs **+40 B flash and +4 B RAM0** against AA3 on the same build profile. If that four-byte delta held for the *deployed* AA3 profile (3,828 B RAM0 used), FIR11 might leave only **8 B protected RAM0 spare**. That extrapolation has not been verified with a byte-matched deployed build. The FIR11 image is **not a flash-ready replacement**.

Build artifacts, raw logs, and SNR model output stay private under `recovery/{fir11-offline-build,aa3-matched-build}/`, excluded from any source commit; neither original voice WAV nor full SPI backup was copied into Git.

## Gates and next step

The source-compiled Rust FIR11 decimator matches the independent Python fixed-point reference sample-for-sample at 84,960, 87,260, 110,000, and 160,000 Hz, including all-zero/all-one 64-bit popcount stress cases; spectral tests verify 1 kHz passband, 2.8 kHz improvement and 9/15 kHz stopband. A standalone focused test run passed 12/12 including existing AA3/bypass tests. The broader linked GP0 fast-divider, fast-popcount, ISR-service ARM gate, fast-micro combination, and timing suite passed **39 tests with 1 skip** under the workstation's Flux RF Python environment, which includes Unicorn. The complete output is preserved at `recovery/fir11-offline-build/targeted-tests.log`.

**Do not flash FIR11 while AA3 is awaiting a controlled on-radio normal-voice/silence/1 kHz comparison.** Preserve full SPI library, the baseline 118,408-B `coop-gpf1-gpt1.bin`, and the currently installed AA3 MCU binary. Capture GPF1/GPT1 immediately after PTT before power-cycle; assess IRQ duration, missed halves, lost PCM, and decoder frame quality. If AA3 reduces static but sounds muffled, FIR11 is the next A/B candidate after verifying complete deployed-profile size, protected RAM, and ISR budget. If AA3 fails to improve real SNR, prioritize analog mic/PDM capture and in-band noise diagnosis rather than indiscriminately adding low-pass filtering.
