# GP0 PCM clock calibration from real RF captures — opt-in offline candidate

**Nov 26, 2026 PT; branch** `experiment/gp0-pcm-clock-7500-offline-20261126` in isolated worktree `/storage5/dev/flux-gp0-pcm-clock-7500-20261126`. Baseline is physical GP0 PCM AIR findings recorded on the [preceding experiment branch](sotto-gp0-pre-vocoder-physical-results-20261126.md), not just simulated. **Radio unchanged; no flash, module install, or RF TX was initiated in this work.** The stable existing firmware and `GP0 PCM AIR` diagnostic slot25 remain as installed before this branch.

## Hardware finding and low-cost fix hypothesis

Real K6 GPIOB GP0/PB1 DMA → firmware fractional decimator → API30 PCM → FSK raw PCM dump, fully bypassing Sotto codec:

| Operator input | Received PCM samples | 100µs capture ticks | Measured physical PCM throughput | Original nominal-8k WAV tone | Physically corrected tone |
|---|---:|---:|---:|---:|---:|
| Generator 500Hz take2 | 480 CRC-validated contiguous prefix (20/67 packets) | 1876 (declared full 1600 sample capture) | 8,528.78/s | ~468.69 Hz | ~499.67 Hz |
| Generator 1000Hz take1 | 1600, complete 67/67 packets | 1876 | 8,528.78/s | 937.50 Hz | 999.47 Hz |
| Generator 500Hz take1 | 1600, complete, *tone absent* | 1875 | 8,533.33/s | no robust tone | no robust tone |

`8000/8530 ≈ 0.9379` is the needed per-time output-rate correction. Existing `sotto-api30-gp0-capture.rs` uses a fixed `fs=87260` GP0 group-rate **denominator**, with `group_weight=8000` added for each PDM 64-bit count group. DMA is free-running (TIM17 has not been demonstrated to pace the raw DMA), so the real group throughput differs from the nominal denominator. The measured output-rate ratio implies about **93.0k PDM groups/s** versus the baked-in 87.26k; the true raw group clock has **not** been directly timed in this experiment.

**Candidate:** select `group_weight=7500` behind a new **default-OFF** `K6_SOTTO_GP0_CLOCK_7500=1` build flag. The predicted new output throughput is **~7,996–8,000 samples/second** for the observed ~8,529–8,533 sample/s baseline. This adjustment changes the fractional sample emission **numerator**, not the denominator, preserving the exact fast Cortex-M0 reciprocal divider, its scaling, and the same input ISR group sequence. It emits *fewer*, not more, PCM blocks per second, which might reduce foreground Sotto encoder pressure; that benefit is not yet hardware-qualified. The post-ADC 8kHz contract is maintained; no Sotto bitstream changes are proposed.

The build gate allows only `K6_SOTTO_MIC=1`, expanded `K6_RAM0=1`, `K6_SOTTO_GP0_FAST_ISR=1`, and **all GP0 prefilters off**, to isolate this clock experiment. The unflagged compilation path keeps `group_weight=8000` exactly as before. CIC/R8 variants continue to use their existing weight and cannot be combined with the new gate through the official builder. This is an empirical constant for *this measured radio*, **not** a universal sampling clock fix. Hardware CPU/interrupt rate could depend on firmware version or MCU/peripheral contention; later calibration should be based on measurement, not blindly applied to other radios.

## Actual Rust decimator regression

`tests/test_k6_gp0_clock_7500_offline.py` compiles the **exact decimator and 87,260 divider extracted from Rust firmware** both default and with the candidate `--cfg`. On deterministic 500Hz and 1kHz synthetic 64-bit PDM popcount sequences flowing at a modeled **93,050 groups/s**, the default emits ~8,531 samples/s and pitches tones low; the opt-in path emits ~7,998 samples/s and restores the expected 500Hz / 1kHz tone to within ~3Hz. The default path also matches the unmodified Python fixed-point oracle **sample for sample**, including randomized density, silence and extremal counts. These tests validate algorithmic rate and bit-exact reference behavior, **not** real DMA sampling rate or Cortex-M0 ISR cycle timing.

Cortex-M0 Thumbv6M cross-compile of a standalone extracted `dec_push` wrapper with `rustc --target thumbv6m-none-eabi -C opt-level=z` also succeeded for both variants. Wrapper `.text` bytes: **284 baseline vs 296 calibrated**, a 12-byte *isolated object* increase. No full linked MCU firmware size, startup, RAM0, or RF run is claimed by that comparison. Real on-radio performance gates remain mandatory.

## Source-verified, pitch-corrected listening WAVs

An offline read-only tool `tools/k6_gp0_pcm_clock_replay.py` verifies original RF PCM headers, CRC-complete WAV metadata / SHA256 or re-decodes C64 IQ, and optionally permits a **CRC-verified contiguous sequence-zero prefix** (never treats partial as complete, never inserts missing samples). It calculates the source rate from the full 1600-sample capture header's 100µs tick count, then applies a band-limited polyphase resampler with exact rational factor `capture_ticks/2000` to create a **new true 8000Hz** signed16 listening WAV. No existing WAV/IQ is overwritten. An optional third-order 2.2kHz **zero-phase output-side lowpass** provides a reversible playback A/B; it is **not** a deployable real-time high-rate anti-alias fix and cannot recover aliased PDM noise already folded down.

Private, reproducible A/B WAV outputs on the workstation:

`/home/tyler/flux-bench/gp0-clock-lab-20261126/`

Machine-readable derived-only summary (no private IQ/audio/keys): [`docs/evidence/sotto-gp0-pcm-clock-7500-real-playback-20261126.json`](evidence/sotto-gp0-pcm-clock-7500-real-playback-20261126.json).

| Real capture | Corrected output spectral peak | Sine-fit residual RMS full-scale: bypass → 2.2kHz LP | LP residual gain | LP tone amplitude loss |
|---|---:|---:|---:|---:|
| 500Hz take2 (verified 480-sample partial) | 499.70Hz | 0.04572 → 0.03381 | −2.62dB | negligible |
| 1000Hz take1 (complete) | 999.45Hz | 0.04304 → 0.03052 | −2.98dB | negligible |
| Generator OFF (verified 864-sample partial) | — | 0.04192 → 0.03035 | −2.81dB | N/A |

The original generator-500 take1 had no appreciable 500Hz tone; its pitch-corrected WAV also has no useful tone. **No clipping** was observed in the two valid signal captures. These controlled results support a large part of the apparent pitch defect being **before** the Sotto vocoder. They also show a ~0.045 full-scale broadband/non-sinusoidal residual even with a strong clean generator input. Filtering after PCM recovers only a modest ~3dB of residual energy. More ambitious anti-alias removal requires properly sampled higher-rate PDM data or controlled on-radio hardware experiments; the low-rate PCM cannot identify the spectrum that folded into it.

## Next acceptance gates (no MCU flash yet)

1. **Completed offline:** complete default and calibrated ARM images built under the recovered feature profile, plus linked Cortex-M0 IRQ simulation and pinned Sotto virtual link checks. The two paired builds have identical flash/RAM0/stack layout; see the qualification addendum below. Verify the actual fielded image provenance and physical CPU/IRQ timing **before any MCU flash**; the 12-byte isolated Thumb wrapper result alone was not a full size proof.
2. Preserve the existing known-good MCU firmware and 2MiB SPI backup. For any subsequent test flash require an **attended recovery plan and operator at the radio**. Do not overwrite current diagnostic module merely to test the clock.
3. On the dummy-load RF bench, capture *new* generator 500Hz and 1kHz raw PCM with corrected firmware, with PTT held until `67 packets sent`. Success criteria: independent CRC-verified full 1600 PCM samples in ~200ms physical, both original WAV header spectra within a few Hz of generators, no GP0 overflows, no TX hang, and normal RX restored.
4. Only after that feed corrected 8k PCM to the pinned Sotto encoder, compare SNR/intelligibility to prior voice WAVs, and evaluate any **separate** cheap high-rate anti-alias filter under measured IRQ/stack budgets. Avoid combining several unqualified DSP changes into one flash.

### Replay commands (workstation only)

```bash
cd /storage5/dev/flux-gp0-pcm-clock-7500-20261126
V=/home/tyler/.local/share/flux-rf-venv/bin/python
IN=/storage5/dev/flux-sotto-gp0-pcm-telemetry-20261126/recovery/pcm_air
OUT=/home/tyler/flux-bench/gp0-clock-lab-20261126

# Complete, independently verified 1kHz run
$V tools/k6_gp0_pcm_clock_replay.py \
  --wav "$IN/gen-1000-01.wav" --metadata-json "$IN/gen-1000-01.json" \
  --expected-tone-hz 1000 --lowpass-hz 2200 \
  --output-prefix "$OUT/1000-full-another-name"

# Second 500Hz run: validated contiguous RF prefix ONLY, no invented tail
$V tools/k6_gp0_pcm_clock_replay.py \
  --iq-c64 "$IN/gen-500-02.c64" --allow-partial \
  --expected-tone-hz 500 --lowpass-hz 2200 \
  --output-prefix "$OUT/500-another-name"
```

Always use **new output names**; the replay refuses to overwrite existing evidence. This tool does not touch the radio, serial port, firmware, or transmitter.

## Full matched ARM builds and linked-IRQ validation (same evening)

The normal pinned Aura build script `firmware/k6-aura/build-fd6818b.sh` was run **twice from the pinned upstream c3aeb17 source**, with the Sotto codec at pinned `598f1bc`, `K6_AURA_RESET_GENERATED=1`, and the recovered physical-profile feature set: API30 Sotto MIC resources, async cooperative FSK, fast SPI, GP0 fast ISR with fault/timing instrumentation, expanded RAM0, scan/Data, user store, font subset/raster boot, serial transport, etc. All other GP0 prefilters were OFF. **The only meaningful controlled build-profile difference was `K6_SOTTO_GP0_CLOCK_7500=0` vs `=1`.** MCU images were generated **only in ignored, private build output directories**; no flashing or K6 serial connection occurred.

| Full linked MCU resource | Default weight=8000 | Calibrated weight=7500 |
|---|---:|---:|
| Application binary bytes | **118,408** | **118,408** |
| Application flash headroom (122,880B limit) | **4,472B** | **4,472B** |
| Protected RAM0 used/free | **3,816B / 24B** | **3,816B / 24B** |
| Protected module arena | 14,976B | 14,976B |
| Main-stack reservation | 2,812B | 2,812B |
| Complete image SHA256 | `42c9ca7847448c6cbd5518395ab096e8898f2d8abe68f859e61dd1fefe5f2c83` | `e7edf82c5aa9081662f649a383375c68026ecff1a8a16e3ca51edf990561631d` |

Across the two complete binaries, 604 bytes differed in 38 contiguous spans (including relocated code). **Equal-size images do not imply binary identity.** Crucially, although the default image has the same 118,408B size as the previously booted `coop-gpf1-gpt1.bin`, its SHA256 **does not match that fielded image**. Thus neither output should be regarded as a drop-in replacement yet. The 24B free protected RAM0 is especially tight: any future diagnostics/filter state must not consume more unless the entire memory contract is requalified.

The exact **linked** Thumbv6M `sotto_gp0_dma_irq` was then executed in the Unicorn Cortex-M0 emulator with **96 virtual DMA half-buffer interrupts and 8 GP0 groups per half**, using matching ELF/image pairs checked byte-for-byte by the profiler. It exercised the actual compiled ISR/decimator, not a Python reimplementation:

| Linked ISR profile | Default | Calibrated |
|---|---:|---:|
| Emitted PCM samples / ring offset | **70** | **66** |
| Ring lost / queued full blocks | **0 / 0** | **0 / 0** |
| Mean emulated instructions per IRQ | **1,093.46** | **1,087.18** |
| Maximum emulated instructions per IRQ | **1,117** | **1,114** |

The corrected image also passed the existing **linked GP0 service start/stop** test, including interrupt vector/route restoration and TX inactive at completion. A new opt-in repeatable paired test `tests/test_k6_gp0_clock_7500_linked_arm.py` asserts 70→66 and no losses when `K6_GP0_CLOCK_PAIR_ROOT` points to the local `firmware/k6-aura` directory; it intentionally skips when private ELF/BIN fixtures are absent. All **100 tests passed with 3 optional linked-image tests skipped** in the standard GP0/codec/virtual-link regression suite before the paired fixture was enabled; the new optional linked test then passed, as did the separate linked service gate. Neither Unicorn instruction counts nor successful mock DMA execution establish **real physical CPU cycles, sample-clock stability under codec load, a successful radio boot, or RF interoperability**.

Public, derived-only exact results: [`docs/evidence/sotto-gp0-clock-7500-full-arm-build-20261126.json`](evidence/sotto-gp0-clock-7500-full-arm-build-20261126.json). Actual ELF/BIN and logs stay local under `firmware/k6-aura/out-clock-7500-{baseline,calibrated}` and `recovery/clock-7500-build-check/`; source radio backup, RF IQ, WAVs, and signing keys stay private.

**Status:** `OFFLINE_ONLY_NOT_APPROVED_FOR_FLASH`. Before an attended radio trial, ensure a boot/recovery image is explicitly validated against the current fielded firmware, then arrange dummy-load transmit, supervised physical PTT, and independent RTL-SDR CRC. Any calibration value should eventually derive from a measured GP0 raw-group clock, not be assumed universal.
