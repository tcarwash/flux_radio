# GP0 RAW AIR actual RF captures — November 27, 2026

**State:** Device flashed with experimental GP0 RAW AIR ARM firmware, boot/legacy cable protocol 5 verified. The signed GP0 RAW AIR API30 module is installed in **KUL2 logical slot 25** and was authenticated/read back. Original slot 25 GP0 PCM AIR 1,952-byte signed image and fresh full 2,097,152-byte SPI backup preserved privately; slot 24 SOTTO GP0 independently verified byte-for-byte unchanged; remaining 30 KUL2 slots unchanged. Radio TX only through attended, manually-held physical PTT into user-confirmed dummy load. No further MCU flashes or unapproved module replacements required for the captures described here.

## First take — signal generator output OFF

RTL-SDR: 35.072s post-tuned 128kS/s IQ, RF 443.500 MHz, −31,300Hz fine offset, `recovery/gp0_raw/gen-off-20261127-01.c64`. Receiver detected all **86 physical frames** with strong frame sync, but only **82/86 accepted CRCs**; sequences **9, 10, 18, 23** were corrupt. A second offline decoding search over alternative symbol phases and rates did not recover them. Preserve incomplete IQ, manifest and all **3,904** verified raw counts, **do not invent or interpolate the 192 missing counts**. A contiguous last segment of **2,944** counts (sequences 24–85) is available for independently replayable noise analysis. The transmitter's capture timer: 445 × 100µs = **44.5ms**, implied group rate **92,045 groups/sec**. For the verified raw counts: mean **32.201**, RMS deviation **1.219** counts; range **28–36**.

## Second take — user requested 500 Hz, 0.1 Vpp, output ON

RTL-SDR: 35.072s saved IQ `recovery/gp0_raw/gen-500-20261127-01.c64` and exactly **4,096 contiguous source uint8 count bytes** in the private `...gen-500-20261127-01.bin`. **86/86 packets accepted with CRC**; SHA256 from the receiver manifest `4ae51ec0173e25c77f69d115fddfa554dc2e02d38dff416c1bb226fd9cb01fc7`, verified independently. Capture ticks **439** ⇒ **43.9ms** and **93,303 groups/sec**. Mean **32.143**, SD **1.233** counts; range **28–36**.

**Unexpected signal finding:** There is essentially **no 500 Hz signal in the captured raw counts**. A fixed-500Hz least-squares sine fit measures only ~0.062 count peak against ~1.233 count RMS residual. In offline replay through *the actual Rust GP0 decimator* (with prospective weight7500 and observed source clock), the reconstructed 500Hz PCM peak is only ~0.0068 full scale, while the previous Nov26 physical 500Hz PCM test had ~0.41 full-scale peak. Those runs are not identical in physical setup/time and this does **not** prove a firmware regression. The generator output, external microphone jack contacts, RFIC mic routing, and recording-mode equivalence need a fresh, independent analog FM audio check or operator verification. **Do not use this 500Hz raw capture as a signal-throughput or filter passband measurement.**

## Genuine pre-decimation noise filter comparison, no radio modifications

The same real group-count data were passed to a host build of the *actual firmware Rust `Decimator` / `dec_push`*, with empirical fractional group weight7500 to emulate a nominal ~8kHz replay. All results are for a 43.9ms window (500Hz) and a contiguous ~31.9ms window (OFF), not long-duration noise characterization.

| Rust pre-decimator | 500-run PCM RMS (FS) | OFF-run contiguous PCM RMS (FS) |
|---|---:|---:|
| Original group boxcar | 0.03878 | 0.04236 |
| AA3 three-pole high-rate IIR | 0.02981 | 0.03015 |
| FIR11_LATE | 0.03455 | 0.03417 |
| FIR11 | 0.03442 | 0.03405 |

AA3 reduces the reconstructed noise amplitude by ~2–3dB in these real short recordings, FIR11 by ~1dB; the values match the earlier synthetic broad-spectrum model reasonably well. However, this does **not** establish AA3 as the right voice filter: previous synthetic passband modeling showed it attenuating 2.8kHz by ~4.6dB. An actual strong 500/1000Hz raw-group capture and speech recording are needed to qualify passband and intelligibility. Filters remain fully offline, the hardware firmware's AA3/FIR11 options remain disabled, and the clock patch is not flashed.

## Private evidence and repeatable measurements

Keep `recovery/gp0_raw/*.c64`, `*.bin`, `*.json` private; only derived **nonreversible metrics** are in [`docs/evidence/k6-gp0-popcount-rf-captures-20261127.json`](evidence/k6-gp0-popcount-rf-captures-20261127.json). The two source recordings are separately retained as originally decoded; no missing values were reconstructed. New offline analysis did not transmit RF or touch the device. This project worktree is `/storage5/dev/flux-gp0-popcount-air-20261127`.

## Next attended physical check

Before another long raw-data transmission, confirm the external generator at **500Hz / 0.1Vpp / output ON** is reaching the radio's microphone input. A short analog FM transmit into the dummy load, with an independent receiver/SDR monitoring the output, can check the cable and microphone route; do not transmit unattended. Then request an additional **500Hz raw-group capture**, followed by **1kHz**. Receiver must be started first; physically hold PTT until `GP0 RAW TX DONE / 86 packets sent`. If either raw capture again lacks a tone while analog FM confirms the generator, investigate the new API9 pre-decimator raw capture path and sampling mode before inferring frequency or anti-alias improvements.

## Third take — 500 Hz after operator reseated generator probes (successful)

A second retry while the signal generator had timed out produced **no RF frames** (35 seconds of noise-only IQ); its raw IQ was retained privately. The operator identified the signal generator timeout, re-enabled the generator, and supervised another capture at 500 Hz and 0.1 Vpp.

The third 500 Hz take recovered **all 86/86 guarded CRC-valid RF packets**, yielding **4,096 complete raw group counts** (`recovery/gp0_raw/gen-500-20261127-03.bin`, source SHA256 `26a871d9af716673b09adfc2542ad91f010edc21f392a175d3842fc68e8ff3c2`). Independent 500 Hz sine fitting measured **500.0795 Hz**, **3.22255 raw counts peak**, **1.29336 counts residual RMS**, and a 500 Hz raw-domain SNR of approximately **4.92 dB**. This is **~52 times** the earlier weak-tone group's amplitude. The 43.9 ms acquisition timer again implied ~93,303 GP0 groups per second.

Replaying *these actual recorded counts* through the exact compiled Rust decimator with the **offline-only** 7500 fractional clock correction produced:

| Filter | 500 Hz PCM peak (FS) | Residual noise RMS (FS) | 500 Hz SNR | Gain vs original |
|---|---:|---:|---:|---:|
| Original boxcar | 0.39844 | 0.04203 | 16.52 dB | — |
| AA3 | 0.38994 | 0.03104 | 18.97 dB | **+2.45 dB** |
| FIR11_LATE | 0.39572 | 0.03604 | 17.80 dB | +1.28 dB |
| FIR11 | 0.39544 | 0.03602 | 17.80 dB | +1.27 dB |

This is the first **actual source-data** demonstration of a clear, strong GP0 tone and pre-decimator filtering benefits. The earlier nominal 500 Hz original PCM AIR capture was ~0.41 full-scale peak; near agreement with the 0.39844 reconstructed peak now supports the raw group acquisition path. 44 ms does not prove robustness over speech-length signals; the noise changes are not yet sufficient evidence to enable AA3 on the physical MCU. As previous offline frequency response showed, AA3 can attenuate ~2.8 kHz by ~4.6 dB, so an attended 1 kHz raw capture and voice-band tests are still recommended before selecting it. No firmware changes or automated transmissions occurred during this analysis.

## 1 kHz physical raw capture — CRC-partial but scientifically useful

The operator set the signal generator to **1,000 Hz, 0.1 Vpp** and physically held PTT for the GP0 RAW AIR dump. The RTL-SDR detected all **86** frames but only **84** had valid CRCs. Packets **8 and 72** were corrupted, despite alternate offline symbol rate/phase searches. The original **35.072s** SDR IQ and the **84 byte-exact accepted frames** remain private. **No complete raw sample file was created.**

There is a contiguous authenticated fragment of **3,024 group counts** (sequence 9–71, each 48 raw counts). The actual acquisition timer was **439 × 100 µs = 43.9 ms**, implying **93,303 groups/sec**; the 3,024-count segment spans ~32.41ms. Raw data independently fit a **1,000.184 Hz** sine at **3.453 counts peak**, residual STD **1.283 counts**, ~5.59dB SNR in the group domain. This confirms the strong generator input in the *new raw path*.

Replay through the same compiled Rust decimator used for the earlier 500 Hz test, with prospective clock weight=7500 (not flashed):

| Filter | Reconstructed 1 kHz peak (FS) | Noise residual RMS (FS) | SNR | Gain over baseline | Tone attenuation over baseline |
|---|---:|---:|---:|---:|---:|
| Original | 0.41907 | 0.04201 | 16.97 dB | — | — |
| AA3 | 0.38602 | 0.03227 | 18.55 dB | **+1.58 dB** | **−0.71 dB** |
| FIR11_LATE | 0.40854 | 0.03648 | 17.97 dB | +1.00 dB | −0.22 dB |
| FIR11 | 0.40828 | 0.03647 | 17.97 dB | +1.00 dB | −0.23 dB |

AA3 reduces reconstructed noise further but introduces more signal attenuation even at 1kHz. On the strong 500 Hz recording, AA3 gained ~2.45 dB versus FIR11 ~1.27 dB; at 1kHz, those gains were ~1.58 versus ~1.00 dB. **Do not deploy based only on these 32–44 ms tone recordings.** To resolve upper speech-band fidelity versus anti-alias rejection, a supervised **2,800 Hz 0.1 Vpp** raw capture is the useful next measurement, followed by human speech recordings. 2.8kHz is close to where earlier modeling predicted AA3 attenuation ~4.6 dB versus FIR11_LATE ~1.6 dB. The optional filters remain OFF on the radio. Exact packet/continuity analysis is saved in `docs/evidence/k6-gp0-1000hz-raw-physical-20261127.json` (derived metrics only).

## 2.8 kHz physical raw capture — high-frequency passband discrimination

After 500Hz and 1kHz measurements, the operator set the generator to **2,800 Hz at 0.1Vpp, output ON**, with physical PTT held manually. The radio sent **86/86 CRC-valid packets**, yielding exactly **4,096 popcounts** and the complete original **35.072s** RTL-SDR IQ. Those source files remain private in `recovery/gp0_raw/`, with **no missing samples** or interpolation. Capture timer 439 ticks = 43.9ms, inferred raw-group rate 93,303/s. Source SHA256 `f5c21e5b8b586f2c0441023ef4b23f770126db703f92a92b96045251b1026764`. Fitted raw tone **2,799.994Hz**, **3.199 group counts peak**, **1.264 counts residual RMS**.

Actual Rust group-decimator variants, all replayed offline against *identical* source group counts with the prospective weight 7500:

| Filter | PCM 2.8kHz peak FS | Residual noise RMS FS | SNR | Signal attenuation vs original | SNR change vs original |
|---|---:|---:|---:|---:|---:|
| Original (no prefilter) | 0.321649 | 0.039845 | 15.130 dB | — | — |
| AA3 | 0.188929 | 0.029348 | 13.164 dB | **−4.622 dB** | **−1.966 dB** |
| FIR11_LATE | 0.267206 | 0.034652 | 14.732 dB | **−1.611 dB** | **−0.398 dB** |
| FIR11 | 0.266975 | 0.034643 | 14.727 dB | **−1.618 dB** | **−0.403 dB** |

This real physical capture confirms the synthetic upper-voice-band prediction: AA3 trades too much high-frequency signal power for low-frequency noise suppression. AA3 is **not** a general-purpose voice-quality fix. FIR11_LATE preserves considerably more 2.8kHz amplitude while still reducing reconstructed noise; it is a more promising candidate for subjective listening trials, but its SNR at this single 2.8kHz tone is still slightly lower than the original boxcar's. The original configuration may remain preferable without further input-specific speech testing. **Do not enable filters on hardware from these tests alone**; previously measured MCU IRQ timing and only 24 bytes of protected RAM0 headroom remain constraints. More useful offline next steps include analysis of possible lighter FIR coefficients, high-frequency equalization after decimation, and evaluating those with saved real GP0 counts and speech recordings.

Derived-only machine-readable source: `docs/evidence/k6-gp0-2800hz-raw-physical-20261127.json`.
