# K6 GP0 offline simulator: measured variable group clock and pitch-direction checks

Date: November 26, 2026 Pacific. Branch `research/sotto-gp0-variable-clock-20261126` based on `310ba07c`. **Offline-only code**: no radio hardware, serial, RF, SDR control or firmware deployment.

## Motivating physical observations

The user supplied five actual Sotto Monitor decoded 8kHz WAVs from 0.1Vpp generator signals. The dominant spectral components were 237.79Hz for nominal 250, 482.18Hz for nominal 500, and 956.79Hz for nominal 1k; 2k resulted in unstable harmonics around 1.9–2.05k. The pinned Sotto1200 slim encoder can recreate a ~191.2Hz harmonic comb from a clean *956Hz* digital input, explaining why the 1k recording may sound lower; however this does not independently explain why input frequency reaching the codec is lower than nominal.

Earlier **different** physical 1k/OFF/2k GP0 DMA diagnostics observed actual popcount rates **84,960 and 87,770 groups/sec**. The firmware's `sotto-api30-gp0-capture.rs` and module hardcode a **nominal 87,260 groups/sec**. These previous measurements **are not contemporaneous** with the current recordings and do not justify changing any fixed divisor based on this evidence alone.

## Important signal-clock distinction

In a system with a physically running PDM group source `R_actual`, an MCU converter advancing fractional phase by 8,000 against **87,260** (its hardcoded divider), and a nominal 8,000Hz decoded WAV header:

- True PCM production clock = `8,000 * R_actual / 87,260` PCM samples per physical second
- **Ideal tone playback frequency = true physical input tone * 87,260 / R_actual**

That second expression is the **inverse** of the first. Slower PDM clock makes recorded playback run **higher** in pitch, not lower; faster PDM makes it lower.

| GP0 groups/s | Scenario provenance | Actual PCM samples/s | Ideal decoded 500Hz input |
|---|---|---:|---:|
| 84,960 | Prior physical 1k diagnostic | ~7,789 | ~513.5Hz |
| 87,260 | Firmware fixed nominal | 8,000 | 500.0Hz |
| 87,770 | Prior physical OFF/2k diagnostic | ~8,047 | ~497.1Hz |
| ~90,485 | **Purely inferred**, *if* 500→482.18 were all clock mismatch | ~8,296 | 482.18Hz |

The last value is **not a measured K6 frequency**. It is larger than both prior physical observations. Sotto harmonic pitch quantization, generator frequency error, and receiver chain must be checked before assigning it to hardware.

## Code changes and their limitations

The periodic-IRQ class `GP0InterruptBudget` now accepts a **separate** `actual_group_rate_hz` (default unchanged, 87,260) and schedules 8-group DMA interrupts using that *physical* rate. Its `pcm_samples` still uses exactly the firmware's **nominal hardcoded divisor**, creating correct producer-count and wall-clock drift in the 4×80 PCM-ring simulator. An invalid physical rate is rejected.

The compiled K6 Sotto ARM virtual-link `trial(..., gp0_irq_cycles=N, gp0_actual_group_rate_hz=R)` passes that clock through the existing IRQ scheduler. The report exposes both `gp0_actual_group_rate_hz` and `gp0_effective_pcm_rate_hz`. The offline `k6_sotto_gp0_hardware_envelope.py` accepts `--actual-group-rate R`, reports correct IRQ-period / PCM-rate / inverse-playback-pitch sensitivity, and otherwise preserves all original 87,260 defaults.

**Crucial limitation:** the virtual-link still feeds *ideal PCM fixture bytes directly to the compiled Sotto codec*, and synthesizes GP0 PDM as a timed producer count. It **does not turn the real 0.1 Vpp microphone waveform into the wrong-clock PCM samples**, nor does it execute the actual Rust IRQ in parallel with the C foreground. Therefore a low/high timing simulation cannot by itself validate the recorded frequency offset or perceived pitch.

A separate reproducible `tools/k6_gp0_clock_sensitivity.py --json NEW.json` command produces metadata-only scenarios with the inverse playback formula, explicitly labeling physical prior measurements and hypothetical inferred clocks. Evidence: `docs/evidence/sotto-gp0-variable-clock-sensitivity-20261126.json`.

## Regression and next measurements

The new tests check measured rates 84,960/87,770, nominal 87,260, a faster candidate 90,500, the fractional IRQ/PCM quantization (DMA half-IRQ batches of 8 PDM groups), inverse pitch sign, fail-closed bounds and the full compiled K6 virtual link. The existing periodic-IRQ and native virtual-link suites must continue passing unchanged at default rate.

**Before hardware modification:** directly measure the generator's actual pitch at the mic input; then measure GP0 clock under the same live capture route and compare with PCM before codec. An FSK packet timestamp by itself is not a direct ADC clock—packets are buffered and sent asynchronously. Once physically measured, an accurate clock can calibrate the PCM ratio and update the simulator's PDM timing / alias frequencies. Keep the signed slot24 Sotto module, full SPI backup and known-good **118,408B MCU image** untouched until the separate real-time/audio gates pass.
