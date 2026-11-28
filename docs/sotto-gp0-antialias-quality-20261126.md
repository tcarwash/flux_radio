# Sotto GP0 microphone quality — pre-decimation anti-alias prototype

2026-11-26 Pacific | branch `experiment/sotto-gp0-audio-quality-20261126` at a precise checkpoint `checkpoint/sotto-gp0-intelligible-voice-20261126`. The verified original 118,408B GP0/trace firmware, signed Sotto GP0 module, all 2MiB SPI calibration and the operator's private 2.24s WAV remain unchanged. This is a **candidate**, NOT a demonstrated acoustic-quality improvement on K6 hardware.

## Why this is the first DSP change

The radio's actual 443.5MHz RF transmission has seven CRC-valid voice packets, 56 native Sotto 1200 frames, and speech audibly identifiable by the operator. The 8kHz decoded WAV contains high-frequency energy roughly equal to the speech band, no sample clipping, and has most frames coded as unvoiced; the fixed-point encoder, RF FIFO and SDR decoding work. The RFIC MIC gain field is already at maximum code31, so boosting it is not an available solution. The old monitor's per-packet decoder resets were separately fixed and the live receiver now runs persistent Sotto synthesis.

The GP0 microphone capture reads 64 oversampled PB1 one-bit levels per ~87.26kHz count. The previous firmware **fractionally boxcar-averaged directly to 8kHz**, which has weak rejection of broadband noise above Nyquist. Once that energy is aliased into the voice band, later digital filtering cannot reliably distinguish it from speech. Upstream radio and microphone hardware paths are unchanged.

Opt-in `K6_SOTTO_GP0_AA3=1` (which also requires mic capture, expanded RAM0 and already-qualified fast IRQ) adds a three-stage low-pass IIR before fractional resampling:

- Three 32-bit **Q7** state variables per GP0 session; each signed high-rate stage has shift-right-by-2 smoothing, alpha 1/4.
- No heap allocation, division, RFIC register writes, DMA layout changes, or added PCM queue storage. Exact signed Q8 scaling into the *existing* DC rejection and 16-bit saturation remains intact. Q7 (rather than Q8) bounds all area products within i32 across the entire 35–160kHz `dec_push` rate acceptance interval.
- All source/ABI/ISP behavior remains exactly as it was when `K6_SOTTO_GP0_AA3` is omitted or `0`. The working on-radio Sotto GP0 slot24 fast4+micro module is unchanged.

## Offline measurements, not radio measurements

The [model and raw results](evidence/sotto-gp0-antialias-spectral-model-20261126.json) use deterministic 64-bit-per-count PDM group measurements at 87,260Hz, deliberately moderate modulation depth (four signed counts each way, avoiding saturation), and optional seeded binomial 1-bit noise. The *actual Rust decimator* was extracted directly from source, compiled in both modes, and compared **sample-by-sample** against the independently implemented integer reference across baseline, extreme groups, four input rates (including 160kHz), and 8kHz output rates. At the nominal GP0 clock, the predicted spectral changes versus the baseline boxcar are:

| Input tone | Recovered 8kHz alias | Three-pole change |
| --- | --- | ---: |
| 320Hz | 320Hz | -0.1dB |
| 1,000Hz | 1,000Hz | -0.8dB |
| 2,000Hz | 2,000Hz | -2.9dB |
| 2,800Hz | 2,800Hz | -5.2dB |
| 7,000Hz | 1,000Hz | -18.0dB |
| 9,000Hz | 1,000Hz | -23.3dB |
| 15,000Hz | 1,000Hz | -33.3dB |

The filter may improve perceptual SNR, but if the microphone's interference is **already within 0–4kHz** or is introduced by the Sotto codec or analog path, it cannot fix that. Also, 2–3.5kHz speech consonant energy is moderately attenuated; listening comparison is essential before adopting the feature as default.

## Real ARM image and gates

The pinned Aura `c3aeb17aec0fd09dccf931b52f6c7d5dbf72e7ac` and pinned Sotto `598f1bce87c22ebada2b9d0d1fcfd89107b33a09` build the full API30 mic / cooperative FSK / 2MiB KUL2 / GPF1+GPT1 firmware. The opt-in candidate image is **118,448B**, SHA256 `cff8ee3308fd0cf65a07dac28513bd33f7c86556073a59b6ec2281568de83e56`, only **40B over the prior 118,408B physically verified image**. Remaining 122,880B app flash headroom **4,432B**, protected RAM0 used 3,828/3,840 (**12B spare**), main-stack reserved **2,812B** (minimum 2,780), expanded module arena 14,976B unchanged. These are **tight but passing** memory limits; verify ISR timing on real hardware and watch for missed DMA/PCM blocks. Candidate and linked ELF remain in the workstation's *private* `recovery/gp0-antialias-20261126/` directory. They are not published as flash-qualified production binaries.

The focused stand-alone Rust source tests and combined linked ARM GP0 service, IRQ start/stop, encoder/module, API30, SPI and timing regression set has **47 passing tests**. Firmware flash and size gates pass. This is not a real microphone-to-SDR audio-quality score: the mandatory next gate is a controlled 1kHz or a repeatable spoken sample through the operator's actual radio, with antenna dummy load, live SDR receiver at corrected tuning, and immediately preserved boot-local GPF1/GPT1 after reconnection without reboot. Compare full codec frame hashes, speech/unvoiced codes, reconstructed PCM noise spectrum and audio intelligibility. If more ISR DMA halves or PCM drops appear, roll back immediately.

## Recovery and safe deployment

The known-good physical image is **`coop-gpf1-gpt1.bin` 118,408B**, SHA256 `c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec`; the original full 2,097,152B SPI backup and exact signed KUL2 slot24 image remain in workstation recovery. The proposed image changes only MCU firmware; do not modify the SPI module catalog. There is also a GitHub tag `checkpoint/sotto-gp0-intelligible-voice-20261126` for byte-identical source restoration. Without physical improvement compared with baseline, keep AA3 experimental and default-off.
