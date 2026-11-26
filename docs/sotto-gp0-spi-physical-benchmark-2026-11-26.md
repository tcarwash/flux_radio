# Flux K6 Sotto GP0: first physical SPI timing benchmark

**Date:** 2026-11-26. **Status:** benchmark completed successfully, original firmware restored, bench released. No RF transmission or PTT was used.

## Reproducible firmware and recovery

The original fielded firmware profile was recovered using the actual Cargo compiler fingerprint dated 2026-11-26 21:17:11 UTC. A clean baseline matched the 116,796-byte fielded image everywhere except the eight-byte GNU fast linker build ID. No other executable, code, or read-only data differed.

Fielded firmware SHA256: 8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d.

The instrumented image was clean-built from pinned sources with streaming SPI reads and the cable-only SPI benchmark enabled. Image size: **117,708 bytes**, flash headroom **5,172 bytes**; arena **14,976 bytes**, RAM0 used **3,768 bytes**, free **72 bytes**, stack reservation **2,812 bytes**. SHA256: 5bad0c3d2fe049828ec582a32df83af63a507959d7f4938d9e0784b44636e99d. A separate non-inlined ~400-byte stack frame contains the bench comparison buffers. The build gate passed and rejects mismatches beyond the linker-generated eight-byte ID.

Before flashing, a complete 2,097,152-byte SPI flash backup was acquired over the cable. The first automatically negotiated 500-kbaud attempt encountered a fast-read transfer error and timed out; a second, forced **115,200-baud** read completed with no errors. The installer independently reread and validated the entire factory calibration sector before preserving the data.

- Backup: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/k6-spi-before-sotto-spi-latency-test-20261126.bin
- SHA256: 18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd
- KUL2 slot24 envelope matched the rollback report SHA256 0dccf301c1c6bd93bdc25cb06805e79ef0ba2d35715d689b1ed2e16a8259e143. Compared with the pre-install signed envelope, only the two-byte install serial and eight-byte authentication tag differed; the 9,784-byte module code hash matched 551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e.
- Slots25 and30 matched the previously recorded preserved hashes. No executable pages in KUL2 required a new build ID. The older KSD1 directory already belonged to a different historical build ID and was not modified.

The diagnostic firmware temporarily replaced only MCU flash through the stock bootloader, reporting 115/115 accepted 1KiB packages. It rebooted into Flux cable protocol5. The single 0x73 SPB1 read-only timing request ran with no RF, no mic capture and no flash writes.

## Measured on-device SPI results

TIM14 100-microsecond ticks, **128 reads per path per size**. A running digest and eight independent full 128-byte comparisons confirmed data equality. Error flags: zero; both digests: 5e501d4b.

| Read size | Original batch | Streaming batch | Original mean | Streaming mean | Speed improvement |
|---|---:|---:|---:|---:|---:|
| 20 bytes | 47 ticks | 32 ticks | **36.72 µs** | **25.00 µs** | **31.9% faster** |
| 128 bytes | 248 ticks | 162 ticks | **193.75 µs** | **126.56 µs** | **34.7% faster** |

The figures represent complete firmware read calls, including software loop overhead—not isolated SPI clock cycles. Raw JSON is saved at /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/spi-real-latency-20261126.json.

A simple two-point linear fit gives approximately **7.64 µs + 1.454 µs/byte** for the original path and **6.19 µs + 0.940 µs/byte** for the streaming path; these coefficients are approximations, not independent hardware component measurements.

**The optimization delivers a real ~32–35% flash read speed improvement with byte-equal results.**

## How it changes the physical GP0 failure diagnosis

Earlier virtual experiments intentionally stressed flash at 34 µs/read plus 2 µs/byte and reproduced a PCM queue overflow. That profile was pessimistic, not physical evidence. The actual measured transfer costs were fitted into the compiled ARM GP0 microphone→Sotto encoder→FSK→independent virtual receiver, using 0.41s of 8kHz audio and CPI=1.5.

| Driver timing | ADC/IRQ model | PCM high-water | PCM lost? | Virtual session |
|---|---:|---:|---|---|
| Original measured | 2,400 cycles/sample | 2 | No | Complete |
| Original measured | 4,000 cycles/sample | 3 | No | Complete |
| Streaming measured | 2,400 cycles/sample | 2 | No | Complete |
| Streaming measured | 4,000 cycles/sample | 2 | No | Complete |

All four runs encoded 11 codec frames and recovered four valid packets in a complete BEGIN/VOICE/END session. Results: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-measured-spi-model-20261126.json.

**SPI speed is a confirmed bottleneck but the physical GP0 ENGINE abort is not proven to be caused by flash latency alone.** Potential remaining causes include actual GP0 DMA interrupt timing under contention, PCM ring loss, codec resource error returns, or FSK callback failure.

## Original firmware restored and verified

Immediately after the benchmark, the original pinned 116,796-byte firmware was reflashed over the cable, with 115/115 accepted blocks. The post-restore Flux protocol5 cable session succeeded and confirmed:

- Slot24 SOTTO GP0 — 9,784-byte code, serial1795730062
- Slot25 MIC IRQ — 3,771-byte code
- Slot30 SOTTO MIC — 9,780-byte code

Post-restore evidence: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/spi-postrestore-status-20261126.txt. Bench owner sotto-spi-bench explicitly released. The complete SPI backup remains intact and unchanged.

## Next engineering steps

Preserve the optimized SPI path as an opt-in until integration into the verified firmware. Prioritize physical error classification of negative GP0 engine returns and telemetry for DMA timing, PCM producer/consumer high-water, codec SPI error and FSK callbacks, without another tight-scratch LCD diagnostic. Then perform a separate attended dummy-load+SDR live Sotto PTT test; nothing in this benchmark proves on-air voice works.
