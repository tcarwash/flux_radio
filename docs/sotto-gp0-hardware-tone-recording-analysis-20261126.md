# K6 Sotto GP0: 250 / 500 / 1k / 2k tone-generator RF recording forensics

Date: November 26, 2026 Pacific. Offline investigation of five user-produced WAV files under `sotto_tone_gen`. **No radio flash, serial connection, RF transmission, SPI change, bench lock or SDR control during analysis.** MCU remains known-good 118,408B image and SOTTO GP0 signed slot24 module serial `1795751383`.

## Evidence and scope

The user injected a stated **0.1 Vpp** signal-generator tone at 250, 500, 1,000 or 2,000 Hz into the external microphone input. `Sotto Monitor` recorded the transmitted and subsequently **Sotto-decoded audio** from RF. These WAV files contain **8,000 sample/s, mono signed 16-bit PCM**, not GPIO PB1 PDM bits, *nor PCM before the Sotto encoder*, nor raw RF.

Original WAV bytes were **never copied into Git**. The derived metadata-only results, including reproducible windowed FFT peaks, are in `docs/evidence/sotto-gp0-generator-tone-wav-metrics-20261126.json`. The standalone command `tools/k6_sotto_wav_forensics.py --directory DIR --json NEW.json` rejects output overwrite and does not open radio/SDR.

| Nominal generator | Dominant decoded component | Ratio to nominal | Whole-waveform RMS | Total level | Duration |
|---|---:|---:|---:|---:|---:|
| 250 Hz | 237.79 Hz | 0.9512 | 0.19446 | −14.22 dBFS | 6.88 s |
| 500 Hz | 482.18 Hz | 0.9644 | 0.25126 | −12.00 dBFS | 6.68 s |
| 1,000 Hz | 956.79 Hz | 0.9568 | 0.13104 | −17.65 dBFS | 8.76 s |
| 2,000 Hz | ~1,968 Hz (wanders) | 0.9840 | 0.20579 | −13.73 dBFS | 8.76 s |
| Generator off | No stable dominant pitch | n/a | 0.05941 | −24.52 dBFS | 7.40 s |

The 1k recording is approximately **5.65 dB quieter overall** than the 500Hz recording. All clips are 8kHz PCM with valid headers; no samples were clipped near full scale (largest absolute peak ~0.93 in 2k). The OFF noise is substantial, and in this test the approximation `SNR=10 log10((rms_on²-rms_off²)/rms_off²)` yields roughly 9.9 dB for 250Hz, 12.3 dB for 500Hz, 5.9 dB for 1k and 10.4 dB for 2k. **These are NOT physically measured microphone-chain SNRs**: OFF is *already codec-decoded*, and this calculation assumes independent stationary noise and comparable stream levels.

## Why the 1k tone sounds "lower"

The decoded 1k recording contains a stable harmonic comb, with peaks near:

| Spectral component | 765 Hz | 957 Hz | 1,148 Hz | 1,339 Hz |
|---|---:|---:|---:|---:|
| Harmonic number on ~191.2Hz grid | 4 | 5 | 6 | 7 |

The strongest component remains ~957Hz, but a listener can hear a **missing fundamental near 191Hz** when equally spaced higher harmonics are presented. The perceived low pitch is therefore plausibly a *vocoder harmonic-reconstruction artifact*, and not evidence that the generator emitted 191Hz.

The pinned `sotto/c/fixed/sotto_fx_slim.c` Sotto1200 **speech** encoder searches time-domain pitch at 4kHz over lags 8–80 (roughly **50–500Hz fundamentals**). Input 1kHz/2kHz sinusoids lie beyond its normal pitched excitation range; it often constructs subharmonic pitch choices. This is normal model mismatch for a speech vocoder and should not be used alone to judge fidelity for human speech.

**Controlled proof using the real pinned native codec, no RF or GP0 hardware:** built `sotto/c/fixed/sotto_slim_enc` and `sotto_dec_fx -1200` and passed exact 8kHz PCM sine inputs. 250Hz decoded near 251Hz, 500Hz decoded at 500Hz, but 1kHz/2kHz digital tones were much less spectrally stable. Crucially, feeding the native codec a clean **956Hz** PCM sine recreated the same ~191.2Hz harmonic comb: 765, 956, 1,148 and 1,339Hz. 482Hz digital input recreated the physical 500 setting's dominant ~482Hz. Private exact codec output and controls are preserved under `recovery/sotto-tone-control/`, never published. These results strengthen the **codec harmonic** explanation and indicate the user-supplied nominal 1k tone's effective frequency reaching the codec could already be near 956Hz, but they do not independently locate the precodec frequency shift.

The decoded 2k tone has broad, changing peaks roughly 1.9–2.05kHz (and generated high frame content); this resembles the unstable high-frequency native codec response and is not a reliable oscillator frequency counter.

## The codec alone reproduces much of the 500-vs-1k level difference

The controlled *native* slim encoder/decoder also reveals that Sotto loses energy from a pure 1kHz digital input even with the microphone, RF and decoder web monitor **entirely absent**. At identical exact PCM sine peak amplitude, the decoder's output RMS levels were:

| Clean digital PCM sine peak level | Native decoded 500Hz | Native decoded 1kHz | 1kHz lower by |
|---|---:|---:|---:|
| 0.08 full-scale | −27.78 dBFS | −31.67 dBFS | 3.89 dB |
| 0.20 full-scale | −18.74 dBFS | −24.89 dBFS | **6.15 dB** |
| 0.35 full-scale | −14.22 dBFS | −18.12 dBFS | 3.90 dB |

The physical 500→1k difference is **5.65 dB**, within the range reproduced by the native speech codec alone. This strongly supports a **codec level/voicing response** as a substantial contributor to the apparent quiet 1k tone; it does **not establish** the real microphone's absolute frequency response or whether additional analog gain variation exists. Native PCM levels are fractions of signed full scale, not the generator's 0.1 Vpp electrical amplitude; do not equate them directly.

## Why the observed ~4% downward frequency offset is not yet explained

The Rust GP0 implementation currently fixes `fs=87,260` groups per second for its fractional `group_phase += 8,000` converter and labels emitted PCM as **8,000Hz**. Earlier physical GP0 calibration observed **84,960 groups/s** during a 1k test and **87,770 groups/s** in another OFF/2k test; however, those earlier records are **not contemporaneous with the current five transmitted WAVs**, and the rate varies by about 3.3%.

**Critical sign check:** with actual group clock `R_actual`, the emitted PCM sample cadence is

`R_pcm_actual = 8000 * R_actual / 87260`.

If a real input tone is at `F_input` and this PCM is played at a header rate of 8,000Hz, then ideally

`F_playback = F_input * 87260 / R_actual`.

Thus **slower actual capture => higher playback pitch**, and faster capture => lower playback pitch. For a 500Hz input, 84,960 groups/s would predict **513.5Hz**, not 482Hz, whereas 87,770 would predict **497.1Hz**. Explaining exactly 500→482.18 purely through this divider requires **approximately 90,485 groups/s**—higher than either earlier physical calibration. Ratios from the 250 and 1k recordings are also not exactly identical and are distorted by vocoder quantization. **Do not blindly change the firmware's 87260 constant or conclude the source is undersampled**.

Other open possibilities: signal generator actual frequency differs from display, level/impedance/routing variations, differences in RFIC clock/configuration between sessions, Sotto pitch-quantization offsets, discontinuities, or clock handling in the receive chain. WAV metadata alone cannot discriminate these. A ~4% RTL-SDR sampling clock error would be unusually large and is not established; Sotto Monitor's WAV is correctly marked 8000.

## Generator-OFF and fidelity implications

The OFF clip has RMS **−24.52dBFS** with no stable strong tone, dominated by diffuse 3–4kHz noise in the decoded spectrum. A mathematically *zero* PCM input run through pinned offline slim Sotto1200 decoded as exact digital silence. Therefore the physical OFF recording contains excitation **somewhere before or within the received/decoded signal path**; codec output is *not* forced to this noise level by an all-zero PCM input alone. The source cannot be isolated from decoded WAV without pre-vocoder PCM/PDM and packet/SDR evidence. Possible causes include analog microphone bias/ground loops, GP0 GPIO/PDM quantization and aliasing, RF demodulation errors, active vocoder excitation on small input noise, or a combination.

The RFIC microphone code already selects its highest documented 5-bit MICIN gain value (31); blindly amplifying digital PCM is unlikely to improve SNR and can worsen clipping or encoder saturation. The unfiltered GP0 converter has weak high-frequency alias suppression. CIC3 is an experimental lower-CPU filter but loses up to 6.25dB of alias attenuation relative to AA3 at 7.5kHz in a separate *synthetic* full-band test, and AA3 previously physically caused a PCM queue overflow. Neither is ready for unattended deployment.

## Independent Antigravity Delegate review

A broad high-effort source review (`agy-jobs` `871c5bb30d41`, Hydrogen High) timed out without a report. A **narrow, read-only evidence-only independent second opinion** (`4f8fb0598a98`, Hydrogen Medium) completed; it agreed that vocoder subharmonics can explain perceived low pitch, observed OFF noise deserves precodec/RF-path localization, and source-clock/codec effects need separate testing.

We independently **rejected the review's clock-scaling sign claim** (it incorrectly described a slower group clock as lowering decoded playback pitch). We likewise **do not assume RF packet arrival intervals directly measure microphone clock**: Sotto1200 packets are buffered, bundled and asynchronously transmitted; measuring packet cadence without correlating to actual PCM production and FSK scheduling is ambiguous. The second reviewer did not read recordings or run tests; our actual WAV analysis and native codec controls above were performed separately.

## Practical next measurements, in order

1. **Verify actual generator signal frequency at the MIC input**, using a frequency counter/scope or a suitable direct calibrated audio capture, while its front panel is set to 250/500/1k. Do not infer input frequency from the decoded Sotto WAV. Confirm source termination and *actual* Vpp at the load.
2. **Calibrate GP0 raw group rate concurrently with the same mic routing and known tone**, using an existing safe attended diagnostic if available; correlate with a raw PDM/PCM test or low-RAM mailbox trace, not the Sotto received WAV alone. Respect the existing narrow protected RAM0 margin and avoid live cable access during PTT.
3. **Capture a pre-vocoder PCM noise window or bounded RMS/spectral counters**, comparing generator-off, short *only with safe coupling/voltage checks*, 250/500/1k/2k. This separates analog/PDM from the vocoder and RF; do not add unsafe buffering or callbacks inside IRQ.
4. **Verify RF packet reception and continuity separately** (good/bad CRC, sequence gaps, FSK deviation, SDR ppm), and run an offline Sotto sine/voice A/B with real captured precodec PCM, maintaining identical gain. Speech intelligibility and signal-normalized noise comparison matter more than a pure 1k sine's synthesized pitch.
5. **Only then select a filter and CPU optimization**. The known-good 118,408B firmware remains installed; defer AA3/CIC3 flashing until joint ISR+encoder real-time timing, full-band alias and protected RAM0 gates pass.

### Private equal-loudness listening fixtures

Two artifacts were produced offline by RMS-normalizing the original 500Hz and 1kHz recordings to ~0.1195 RMS each, preserving their frequency and harmonics; 3s each, 0.5s silence, 20ms fades. These files are **not published or source WAVs overwritten**:

`recovery/listening/equal_rms_500_then_1000.wav` and `recovery/listening/equal_rms_1000_then_500.wav`.

If 1k still sounds lower at equal RMS, that further supports *perceived pitch* rather than only loudness as the reported observation.
