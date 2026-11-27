# K6 Sotto GP0: CIC3/R8 anti-alias filter and linked-ARM timing

Date: November 26, 2026 Pacific. Branch `research/sotto-gp0-cic3-decim8-20261126`. **All work offline**: no radio programming, serial connection, SDR use, transmission, SPI changes, or bench reservation. The K6 remains on its known-good 118,408-byte MCU firmware with the unchanged, signed, optimized 9,760-byte slot24 SOTTO GP0 module. This branch is **NOT FLASH QUALIFIED**.

## Context

The three-pole AA3 filter was physically tested, returned `GP0 ENGINE`, and boot-local GPF1/GPT1 established a four-block PCM queue-full abort at about 240.11ms. No DMA-half, codebook callback, or FSK callback errors were recorded. The successful unfiltered run, using exactly the same signed module, had completed 202 frames. The previous simulated AA3 model was optimistic. The new periodic-IRQ simulator, with an explicit 87,260/8 Hz interrupt cadence and measured-peak sensitivity sweeps, is on the separate branch `research/sotto-gp0-hardware-timing-sim-20261126` (`310ba07c`).

The first low-cost 2-stage CIC at R=8 achieved approximately 1,142 Thumbv6m instructions/IRQ (vs AA3 1,232 and unfiltered 1,104), but was too weak against key aliased interference: about -10.75 dB at a 9kHz input in the *strongest 80-3900Hz output bin* vs AA3 about -21.98 dB. A naive single 8kHz alias-bin comparison wrongly reports a much greater improvement: the intermediate 10,907.5Hz output shifts some alias frequencies.

## CIC3 design

Implement the three-integrator/three-comb CIC (`sinc^3`, R=8) entirely within the existing firmware-owned PDM/PCM DMA ISR. Each 87,260-Hz raw popcount is reduced from the original `raw=2*bits-64` to `v=bits-32` before the CIC. Three u16 **wrapping** integrators run for each group. Once per eight groups, three u16 modular comb differences produce a signed i16, bounded at ±16,384 by the input range ±32 and CIC gain 512. The original fractional PCM generator then processes this 10,907.5-Hz output, advancing phase by 64,000 against `fs=87,260` and emitting exactly 8,000 signed 16-bit PCM samples/s. The CIC gain and halved input mean `abs(area)/fs` already has the original Q8 PCM normalization (no extra shift).

This is a new default-off `K6_SOTTO_GP0_CIC3=1` flag. It requires expanded RAM0, mic service and fast ISR, refuses all combinations with AA3, FIR11/late, and CIC2, and refuses any group rate other than the real 87,260Hz. It does not change the signed Sotto module, native 4-block PCM ring, DMA allocation, API, FSK, codec packet format, bootloader, SPI or transmit guards.

The exact Rust decimator, extracted from the source and compiled with `rustc`, matched the Python fixed-point oracle **sample-for-sample** on seeded random-binomial popcounts, all-zero and all-one inputs, and mixed impulses. Separately, the u16 modular triple CIC matched an unbounded signed convolution with three 8-tap rectangular filters, including wraparound and extreme DC inputs. No additional RAM0 buffer or heap allocation was introduced.

## Matched ARM build and executed interrupt path

All measurements below use reconstructed identical feature flags and pinned upstream Sotto submodule, **not** the exact full set of flags from the physically installed 118,408B image. Actual linked Rust ISR was exercised with Unicorn Cortex-M0 against alternating half-DMA buffer interrupts and PDM popcount test vectors. ELF and firmware binary are byte-matched, and all cases yielded the expected PCM writes without ring loss.

| Profile | Mean instructions / IRQ (1k tone) | Protected RAM0 |
|---|---:|---:|
| Unfiltered bypass | 1,103.54 | 3,792B used, 48B free |
| AA3 three-pole | 1,231.54 | 3,804B used, 36B free |
| CIC2/R8 | 1,142.00 | 3,804B used, 36B free |
| **CIC3/R8** | **1,160.26** | **3,808B used, 32B free** |

CIC3's mean executed instructions are about **5.8% lower than AA3**, and just **5.1% above bypass** on this synthetic tone profile, versus 3.5% above bypass for CIC2. The matched full image is **117,388B**, SHA256 `6468e9e20a33493293e695a8fa14c2998032eab4ed3ecd9f1b83b51ac6897bb0`, with module arena 14,976B, main stack reservation 2,812B and Rust ISR symbol size 836B. This profile has 32B protected RAM0 free, but **extrapolating to the deployed AA3 profile with only 12B spare would leave perhaps 8B**, which is too close to a hard boundary to authorize hardware flashing.

**Unicorn instruction count is not equivalent to TIM17 cycles or mean physical ISR time.** Actual K6 memory waits, peripheral bus contention, interrupt priorities, codec foreground and RF operation are not yet measured jointly. The model's earlier AA3 overflow prediction (~400ms vs physical ~240ms) reinforces that it must not be used as a standalone flash gate.

## Correct, alias-aware synthetic spectral comparison

An independent deterministic density-popcount test uses 1.5-second sinusoidal high-rate inputs at 87,260 groups/s, runs each exact filter, excludes the first 200ms, then measures the **largest component in the complete 80–3,900Hz 8kHz output band**, rather than searching only the direct 8kHz alias. Relative strongest-band attenuation vs legacy bypass (dB):

| Input frequency | AA3 | CIC2 | **CIC3** |
|---|---:|---:|---:|
| 1k voice | -0.76 | -0.42 | **-0.54** |
| 2k voice | -2.87 | -1.76 | **-2.24** |
| 2.8k voice | -5.16 | -3.54 | **-4.49** |
| 3.5k voice | -7.35 | -5.59 | **-7.10** |
| 7k interference | -13.77 | -13.70 | **-13.74** |
| 9k interference | -21.98 | -10.75 | **-21.59** |
| 15k interference | -16.47 | -15.81 | **-16.16** |
| 17k interference | -20.14 | -10.50 | **-20.17** |
| 25k interference | -2.96 | -2.29 | **-2.55** |

Compared with AA3, CIC3 preserves an additional 0.67dB at 2.8kHz and 0.63dB at 2kHz, while suppressing the 9k/17k examples similarly. This is **not evidence that CIC3 removes the radio's actual static**: 25kHz interference remains poorly rejected in this *model*, while real GP0 microphone analog/PDM noise statistics and clipping are unknown. The 8-group decimation's intermediate-rate aliasing requires full-band testing over more than these sample tones.

## Random PDM popcount SNR check

A 1.2-second seeded independent 64-bit binomial PDM model estimates 1kHz sinusoidal voice RMS vs residual after the first 200ms. This is **not real GP0 ADC bitstream**. At modulation amplitude ±0.015 probability, bypass gave **−5.267 dB**, AA3 **−2.993 dB**, CIC2 **−3.527 dB**, CIC3 **−3.223 dB**. CIC3 improves this synthetic SNR by ~2.04dB over bypass and trails AA3 by ~0.23dB. At ±0.035, CIC3 reached +4.524dB vs AA3 +4.770dB.

## Disposition and remaining safety gates

- **Offline research success; not ready for physical deployment.** It is a plausible speed/alias-rejection improvement, not demonstrated voice fidelity or real-time stability on K6.
- For future operator-attended tests, first reproduce the exact currently deployed firmware's **full build flags** and protected RAM0 budget. Do not borrow stack or extend PCM rings. Validate ISR+codec joint scheduling against real GPF1/GPT1 and sustained TX traces.
- The user's 1kHz/2kHz/generator-off microphone measurements, when available, should be used to calibrate the real MICIN/PDM transfer, gain, out-of-band noise and encoder voicing before deciding whether CIC3's response is beneficial.
- Further alias studies should sweep 0–43kHz densely, model varying PDM noise correlations (not independent binomial counts alone), and compare the **maximum output component anywhere in 80–3900Hz** after quantization.
- Preserve the known-good MCU image, calibration backup, signed module, and fail-closed PCM overrun/dekey behavior. **Do not flash CIC3 without explicit subsequent approval.**

The private matched ELF/BIN, raw test logs and recovery data stay outside Git. Evidence for the link-time ISR and spectral/SNR checks is in `docs/evidence/sotto-gp0-cic3-decim8-offline-20261126.json`.

## Additional dense out-of-band alias sweep: important negative result

A 31-frequency, 5–43kHz sweep (1.25kHz spacing, 0.7s each) evaluated
**the strongest 80–3900Hz output spur**, not only the direct 8kHz-alias bin.
It exposed a weakness hidden by the chosen 9kHz and 17kHz spot checks:

- At **7.5kHz**, AA3 reduced the strongest in-band component by **11.93dB**
  against the unfiltered baseline, whereas CIC3 reduced it by only **5.67dB**.
  CIC3 was **6.25dB worse than AA3**, with its largest spur near 3.41kHz.
- At 6.25kHz, CIC3 was 3.77dB worse; at 8.75kHz, 3.46dB worse.
- Frequency-dependent sample-count quantization means this is a specific
  deterministic modeled PDM-count test, **not a measured K6 microphone or
  a worst-case guarantee over the entire spectrum**.

Evidence and reproducible CLI:
`docs/evidence/sotto-gp0-cic3-wide-alias-sweep-20261126.json` and
`tools/k6_gp0_alias_sweep.py --start 5000 --stop 43000 --step 1250 --duration .7 --json NEW.json`.

**Updated disposition:** CIC3 is the strongest *low-ISR-cost* filter candidate
so far, but it is **not** generally equivalent to AA3 anti-alias performance.
Do not promote to on-radio testing without real MICIN/PDM measurements and
further rejection analysis over the problematic 6–9kHz input region.
