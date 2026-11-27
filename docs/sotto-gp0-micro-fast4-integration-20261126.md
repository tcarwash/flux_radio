# Combine pinned Sotto RAM-micro with fast4 for microphone-to-SDR test

Date: Nov 26 2026 Pacific; branch `experiment/sotto-gp0-micro-fast-combined-20261126`. **The timing comparison is offline. Following its regression gates, the user explicitly authorized installing the module for a physical mic test. Firmware and slot 24 were changed safely; no microphone test has yet been performed.**

## Upstream isolated work reviewed

`research/k6-codec-micro-20261126` commit `2a7a0159`, with RAM-micro adaptation from `b2d63cdb`, was reviewed directly. The RAM change is *temporary generated Sotto C source*, not a modification to the pinned Sotto submodule SHA `598f1bce87c22ebada2b9d0d1fcfd89107b33a09`. It lowers the 4kHz history from 240 to 222 samples and reuses FFT-dead/workspace regions for both `a` and `q`, subject to a compile-time lag-workspace bound. The original branch independently tests equal codec output and a 196-byte smaller encoder state (3,148 → 2,952B), with the module scratch budget improving 48 → 264B (including its 20B code shrink).

**Important negative result:** Under the isolated research branch's pessimistic modeled SPI read costs, this memory change **does not** fix PCM overrun. Both unoptimized and RAM-micro control variants overflow the four-block GP0 queue at 1,280 samples when an additional SPI byte cost rises to 1.95–2.0 microseconds/byte (model fixed cost 34µs/read; CPI 1.5; ADC 2,400 cycles/sample). At 1.85µs/byte both pass. Those are *assumed simulation inputs*, not measured present-radio timings. Do not claim a physical throughput win from RAM-micro.

The previously **physically measured** optional streaming SPI comparison on a separate debug image approximated 7.64µs + 1.454µs/byte before and 6.19µs + 0.940µs/byte after. In our current per-module model, the already built fast4 codec optimizations matter more for throughput. Physical queue-overflow evidence on old GP0 module: 2,182 half-buffer IRQs; cause 2 (queue full); no missed DMA halves, no FSK errors, no resource errors. The SDR capture hardware is read-accessible via `librtlsdr`, which avoids the broken Soapy adapter; independent physical decoding of *live microphone* audio is still pending.

## Combined module now built, independently checked

The new `tools/k6_sotto_module.py --ram-micro` opt-in builds alongside the existing `--fast-div --fast-history-copy --fast-cb-pair --fast-lag-pair` options. It leaves the default 9,784-byte module **byte-identical** to the fielded reference. It changes no RF/PTT logic, module ABI, app MCU firmware, GPIO pin, or codec bitstream format. Builds are for the existing exact pinned upstream Sotto.

| Variant | Relocatable module | Estimated remaining scratch | SHA256 |
| --- | ---: | ---: | --- |
| GP0 original | 9,784 B | 48 B | `551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e` |
| GP0 RAM micro only | 9,764 B | 264 B | `bcf5a376264da62da2c625221237a83efbb53d63e8e95ee838e4c0e86f34d117` |
| GP0 fast4 only | 9,784 B | 48 B | `9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0` |
| **GP0 fast4 + RAM micro** | **9,760 B** | **268 B** | `7c91f220ad6d7577dfac86cd2eaf3d53a6a14b2ad671aa870596031025092121` |

The scratch calculation uses a 14,976B arena, 1,040B runtime callback/stack reserve, 24B module overhead, original scratch requirement 4,080B, and 196B saved within encoder state. The new module has now been installed as a CRC32-protected K6M image on the mic-capable K6 host, with a verified library readback. Scratch estimates still are not worst-case hardware measurements.

### Compiled ARM and independently decoded virtual RF comparison

All eight simulations used the actual compiled relocatable ARMv6-M GP0 codec, a virtual 8kHz microphone PCM producer, the pinned SPI resource/deck and native FSK/RX decoder, with 8,293 test samples. Results: [machine-readable comparison](evidence/sotto-gp0-micro-fast4-comparison-20261126.json).

| Modeled regime | Fast4 | Fast4 + micro | Outcome |
| --- | --- | --- | --- |
| CPI 2.5, ADC 4000 cycles, SPI 6.19µs + 0.94µs/B | Complete | Complete | 26 Sotto frames, six CRC-valid virtual packets, PCM peak 2/4 |
| CPI 3.0, ADC 4000 cycles, same SPI | Overflow | Overflow | Both fail closed at 5,120/8,293 samples, return -12 and dekey |
| CPI 1.5, ADC 2400 cycles, SPI 34µs + 1.85µs/B | Complete | Complete | Six valid packets, PCM peak 3/4 |
| CPI 1.5, ADC 2400 cycles, SPI 34µs + 1.95µs/B | Complete | Complete | Six valid packets, PCM peak 3/4 |

All successful sessions matched the independent native Sotto encoder and RF decoder. **No evidence of additional speed advantage or new codec correctness issues due to the micro change** in this set. This is useful primarily because it creates buffer/stack/diagnostic room while preserving a fast encoder.

## Physical staging complete; microphone → SDR verification pending

After the user explicitly transferred bench ownership from `mic-to-sdr-verify-20261126`, I claimed it under `sotto-mic-gp0-optimized-20261126` and confirmed the cable port idle. The previous *mic-disabled* API29+API31 host is kept as rollback. Its VFO was 443.500MHz, standby, TX off, battery 7.67V.

1. Flashed the previously physically booted, GP0-mic-enabled cooperative FSK + GPF1/GPT1 MCU image `coop-gpf1-gpt1.bin` (118,408B, SHA256 `c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec`) through the stock bootloader. **116/116 KiB blocks acknowledged.** The normal VFO/cable interface returned.
2. Used `k6_install.py backup --legacy` to read **all 2,097,152 SPI bytes** at negotiated 192kbps, with an independent repeated calibration sector check before saving. SHA256 `a069789376abf06374d96dccdab1fe1880afdf5e37c3f6f6e49496ed7c5a0869`. Both this and the archived older full SPI backup remain stored under the workstation recovery directory; never upload factory calibration publicly.
3. Parsed the exact packed KUL2 snapshot. All 32 catalog entries were occupied. Extracted and preserved the original slot24 signed module envelope, hash `0dccf301c1c6bd93bdc25cb06805e79ef0ba2d35715d689b1ed2e16a8259e143`, original unsigned module 9784B SHA256 `551bea3e...99d48fa6e`. Explicitly preserved slot22 synthetic TX, slot25 MIC IRQ, slot28 SOTTO RX, slot30 SOTTO MIC.
4. Built the 9760B optimized fast4+RAM-micro module from its pinned source, with 268B estimated scratch margin; SHA256 `7c91f220ad6d7577dfac86cd2eaf3d53a6a14b2ad671aa870596031025092121`. `k6_install.py load --legacy --only 24 --verify` installed its **9784-byte K6M envelope**, and physically read it back byte-exact. A separate read-only catalog query reported slot24 `SOTTO GP0`, code 9760B, replay serial `1795751383`, host API30.
5. The already running Sotto Monitor is receiving at nominal 443.500MHz, using the user's existing **−30,500Hz** click-to-tune offset. A native `librtlsdr` receiver capture was completed and hashed independently; a quiet capture correctly yielded 0 voice frames, and the decoder's synthetic voice selftest returned one CRC-accepted packet, 8 codec frames, 5,120 PCM bytes. The new offline helpers are `tools/k6_sotto_sdr_capture.py` and `tools/k6_sotto_cf32_to_wav.py`, with optional `--tune-offset-hz -30500`.

**The microphone→RF→SDR decode acceptance result is still unknown. No MIC PTT run was initiated by the assistant after installing the new slot24 image.** The operator must keep the dummy load attached, remove the programming cable (same headset jack as the mic), connect microphone, run `CATALOG → SOTTO GP0`, press PTT and speak 3–5 seconds, then release at an earlier fault. After the test, reconnect the serial cable **without power cycling or any other cable query**, so the first command can fetch GPF1+GPT1 boot-local diagnostics with `tools/k6_sotto_gp0_timing.py --port /dev/ttyUSB0 --ack-reboot --save NEWFILE.json`. Then retrieve Sotto Monitor events/audio or an independently recorded IQ file and decode to WAV, never claim RF interoperability based solely on a module return code. The SDR receiver must be retuned or its exact click offset applied consistently to IQ offline, not assumed zero.
