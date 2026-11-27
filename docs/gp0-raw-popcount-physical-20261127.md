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
