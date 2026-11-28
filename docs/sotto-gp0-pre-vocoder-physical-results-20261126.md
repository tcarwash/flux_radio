# GP0 PCM AIR: first physical pre-vocoder captures (Nov 26, 2026 PT)

## Setup and raw evidence (kept private on workstation)

With the API30 `GP0 PCM AIR` diagnostic installed and independently read back in K6 **slot 25**, the operator made three separate physical PTT / dummy-load captures. The known-good `SOTTO GP0` module remains in slot 24; no MCU flash was performed. Offline RTL-SDR receiver used 443.500 MHz and fine offset -31,300 Hz, with 28.032 seconds of recorded post-tune 128 kS/s complex64 IQ per run. IQ and PCM WAVs remain **untracked** in `recovery/pcm_air/`, *not GitHub*. Each C64 capture is 28,704,768 bytes.

| Run | CRC-valid RF chunks | Complete? | PCM samples | Radio capture ticks (100us) | Emitted PCM samples/s |
|---|---:|:---:|---:|---:|---:|
| `gen-off-01` | 36 consecutive seq 0–35; 36/67 | No | 864 known original samples recoverable as partial data only | 1876 | 8528.78* |
| `gen-500-01` | 67/67 | Yes | 1600 | 1875 | 8533.33 |
| `gen-1000-01` | 67/67 | Yes | 1600 | 1876 | 8528.78 |

`*` The generator-OFF radio reports the full capture duration in every received packet, but 31 **trailing** RF packets (seq 36–66) were not recovered; no complete OFF WAV was generated, correctly. This is *consistent with* early release/cancellation of the PTT or RF transmission, but not a proven cause. The first 864 samples were recovered **only in memory for offline analysis**, with no missing samples synthesized.

All three recovered packet sets have internally consistent session tokens and capture durations. Both complete WAV payload SHA256s match the independent RF metadata:

- 500 Hz SHA256 `45be3082862a3b6a14f39f9c45dacb7113a1a9e64895c19c6b9cc28048e9d298`
- 1 kHz SHA256 `1294e4fc943d605812855cf351dd16fbdfe30a8f0ba3a44e8592bafef4f628d4`

## Main observation: hardware PCM clock runs ~6.6% fast

Source code declares a nominal **8,000 Hz** PCM consumer rate and uses hardcoded **87,260** GP0 64-bit PDM groups/s for fractional resampling. The radio's *100us-tick capture elapsed time*, without Sotto encoding, independently implies **8,529–8,533 PCM samples/s**, **6.61–6.67% faster** than the declared 8kHz. The two different tone runs agree within ~0.05%.

More importantly, the **strong 1,000 Hz generator** at pre-vocoder sample-header 8kHz yields a 937.500 Hz spectral peak. Reinterpreting the exact same PCM with the *actual radio-clock-measured sample rate* gives `937.500*8528.784648/8000 = 999.467 Hz`. Equivalently the known physical 1kHz input predicts **8,533.33 samples/s**, essentially the hardware timing rate. This frequency/timebase agreement localizes a major pitch error **upstream of Sotto**; codec distortion may still be present in addition. The generator was assumed accurate within a few Hz. The capture timer's oscillator accuracy and first/last PCM-buffer boundary biases are not independently calibrated; avoid claiming perfect ppm-level timing.

The existing GP0 driver in `firmware/k6-aura/sotto-api30-gp0-capture.rs` uses 87,260 as the fractional decoder denominator and fast-divider constant. If the GPIO/DMA free-run clock is higher than that assumption, **the producer emits more than 8,000 PCM samples per wall second**. The observed ratio corresponds to a *hypothetical* actual high-rate input of ~93.0 k PDM groups/s (~5.96M samples/s at 64 reads per group); this is an inference, **not a measured DMA transfer counter**. Earlier GP0 free-run results differ by session; do not simply replace the denominator or flash the MCU without controlling CPU/ISR timing and performance regression risk. Favor a measured or calibrated timebase solution and offline test against this WAV.

## Raw PCM amplitude and noise before the vocoder

All ratios are measured on signed 16-bit pre-Sotto PCM, relative to full scale. The raw off data below uses only **the first 864 existing samples** (36 valid packets). No synthetic padding or samples from another session were inserted.

| Case | PCM RMS / full scale | Fitted primary tone RMS / full scale | Near full scale clips |
|---|---:|---:|---:|
| OFF (first 864 samples) | 0.04377 (about -27.18dBFS) | none expected | no observed severe clipping |
| 500Hz, 0.1Vpp (1600) | 0.04463 | 0.00254 (at ~488.83 Hz near nominal window) | 0 |
| 1kHz, 0.1Vpp (1600) | 0.29407 | 0.29060 (937.50Hz, nominal WAV rate) | 0 |

The 500Hz record has a broadband-looking strongest-peak distribution rather than a strong line at its clock-expected ~469Hz. Its **tone fit is ~41.15dB lower** than the strong 1kHz tone, and its ~0.045 PCM RMS is virtually all residual/noise. The first-864-sample 500Hz RMS (0.04577) is close to OFF (0.04377). This is **not credible evidence of a 41dB analog high-pass response**. First verify the generator was actually outputting/connected/settled at 500Hz during the 800ms pre-capture bias period; then repeat that single run (different output prefix) with adjacent analog receiver verification if useful.

The 1kHz sine-fit residual RMS is **~0.04499** (~-26.94dBFS); relative to its 0.29060 tone RMS, the pre-vocoder SNR proxy is **~16.2dB**, while the OFF raw RMS yields ~16.4dB. Thus some substantial broadband residual is **already present before Sotto**, although only 200ms was captured, and harmonic/jitter residual is not distinguishable from random noise by one sine fit. Sotto can independently worsen noise and harmonics; this experiment does not exonerate the vocoder.

## Practical next steps / go-no-go

1. **Rerun 500Hz only**, using a fresh `gen-500-02` output prefix. Confirm generator output ON, 0.1Vpp, stable continuously across the 800ms bias settle and 200ms PCM capture; verify the actual RFIC analog FM microphone path with a nearby monitor if desired. Keep PTT pressed until the `67 packets sent` completion message.
2. Optionally rerun generator OFF (`gen-off-02`) with PTT held for the full TX to obtain a CRC-complete noise baseline. The current 36-packet partial is still useful, but the old comparison CLI requires 1600 samples and correctly refuses a partial file.
3. Prefer studying a **clock-corrected 8kHz producer** (ideally time-calibrated) and anti-alias strategy offline before an MCU flash. Keep the existing known-good Sotto module and recovery backup. A rate-fix candidate must preserve the GP0 IRQ CPU budget and avoid queue full; earlier AA3 regressions make a high-cost ISR change unsafe to assume.
4. Do not interpret output WAV labeled 8kHz as physically 8kHz until source clock correction; this explains the ~937.5Hz precodec fundamental for an actual 1kHz generator.

All physical run metadata was inspected from `recovery/pcm_air/gen-{off,500,1000}-01.json`. The 500/1000 WAVs were re-read and checked against their signed-PCM SHA256s. The generator-OFF IQ was decoded again with the independent blind RF framing and D9 CRC decoder, yielding 36/36 valid sequential packets. No radio serial, flashing, PTT or transmitter commands were issued in this analysis.

## Second 500Hz run: physical confirmation (same evening)

The operator made a fourth recording, `recovery/pcm_air/gen-500-02.c64` (28.032s of post-tune IQ, original 28,704,768-byte file preserved). The receiver's metadata says `complete_pcm: false`, because it received only packets **0–19 (20/67)**, with no missing packets in that *prefix*. Its real D9 CRC/session decoder recovered **480 genuine consecutive signed-16 PCM samples** (no filler, prediction, or synthetic silence); exactly 47 trailing RF packets are missing. This is consistent with a shortened transmission or physical PTT release, but *the reason for truncation is not established*. The module's LCD completion state was not recorded for this run.

A separate offline re-decode and sine fit of those 480 samples yielded:

| Measure | Second 500Hz (`-02`) | Earlier 500Hz (`-01`) | 1000Hz reference (`-01`) |
|---|---:|---:|---:|
| Present consecutive samples | 480/1600 | 1600/1600 | 1600/1600 |
| Physical capture time from D9 header | 187.6ms (full capture completed even if RF incomplete) | 187.5ms | 187.6ms |
| PCM production rate from MCU ticks | 8528.78 samples/s | 8533.33 | 8528.78 |
| WAV-8k-equivalent measured pitch | **468.69Hz** | no robust tone | **937.50Hz** |
| Pitch corrected by physical sample production rate | **499.67Hz** | not reliable | **999.47Hz** |
| Dominant fitted sinusoid peak/full-scale | **0.40745** | ~0.00041 at clock-predicted tone | **0.41098** |
| RMS of residual after fitted sine/full-scale | **0.04633** | ~0.0446 (noise-dominated) | **0.04499** |
| Total PCM RMS/full-scale | **0.29147** | ~0.04463 | **0.29407** |
| Max absolute PCM sample/full-scale | 0.48242 | 0.16113 | 0.51465 |
| Near-full-scale clipped samples | 0 | 0 | 0 |

**Conclusion:** The second 500Hz run is a strong authentic pre-vocoder signal. It agrees with the 1kHz reference in amplitude (tone ratio ~-0.08dB), similar residual broadband noise (~0.045 relative full scale), and corrected generator pitch within <1Hz. Thus the previous weak 500Hz take is **not reproducible** and cannot justify attributing a >40dB difference to GP0 hardware frequency response. The 6.6% fast PCM producer is independently corroborated at **two input frequencies**. The 480-sample window is only ~56ms wall time, so noise-spectrum detail and long-term drift remain uncertain.

**Next hardware step, if a full replacement is desired:** make another attended 500Hz capture with a *new* name, generator output stable before launch and physical PTT **held until `67 packets sent`**. A complete second generator-OFF run remains useful for the original strict OFF/ON comparator. Do not delete, patch, or fabricate samples to satisfy its 1600-sample full-record gate. For clock remediation, run a firmware-free offline model first; all previous queue-overflow / ISR-cycle safety constraints remain applicable.
