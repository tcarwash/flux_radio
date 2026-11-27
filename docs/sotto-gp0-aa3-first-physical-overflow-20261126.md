# K6 Sotto GP0 AA3 — first attended microphone test: PCM queue-full regression

Date: 2026-11-26 Pacific (first diagnostic read 2026-11-27T04:30:16Z). Physical experiment with the 118,448 B AA3 opt-in MCU image, SHA256 `cff8ee3308fd0cf65a07dac28513bd33f7c86556073a59b6ec2281568de83e56`. Exact signed 9,760 B SOTTO GP0 module slot24 serial `1795751383` remained unchanged through the MCU flash. The operator launched SOTTO GP0 and observed `GP0 ENGINE`, then reattached the cable without power-cycling.

The first subsequent cable session read BOTH boot-local GPF1 and GPT1, persisted the **raw bytes and decoded fields before the cable-session reboot**, and the host decoder accepted both as valid. Private recovery original: `recovery/gp0-antialias-20261126/aa3-first-gp0-engine-postrun-20261127T043016Z.json`. A non-speech copy of both telemetry records is in `docs/evidence/sotto-gp0-aa3-first-physical-queue-overflow-20261126.json`; no microphone speech, KUL2 binary, or SPI calibration backup is in Git.

## Actual hardware outcome

| Field | AA3 run |
|---|---:|
| Module return | **-12 (GP0 ENGINE)** |
| Exact GPT1 cause | **2: PCM queue full at DMA interrupt** |
| PCM ring at failure | all four 80-sample blocks unavailable |
| PCM ring **later at stop** | 3 blocks, offset 0 |
| GPF1 PCM lost | yes |
| Completed GP0 DMA half-IRQs | **2,619** |
| Approx. live capture duration | **240.11 ms** |
| Peak ISR TIM17 ticks | **3,488** (~36.33 µs at nominal 96 MHz) |
| Missed DMA halves | **0** |
| Resource callback errors | **0** |
| FSK callback errors | **0** |
| Codec resource reads | **1,034** |
| Accumulated codebook callback ticks | **6,913,531** (~72.02 ms; modulo individual wraps) |
| Peak resource callback | 29,206 ticks (~304.23 µs) |
| FSK preload attempts | **1** |
| Peak FSK stage | **2.4 ms** |

The discrepancy between the ring being full *at failure* and showing three pending blocks *after shutdown* is expected: the foreground may consume a block before the post-stop telemetry snapshot.

The four-block queue holds only 40 ms of 8-kHz audio. This is not a DMA overrun or invalid PDM popcount; the interrupt's reason=2 explicitly states the foreground did not free enough completed PCM blocks. The FSK/resource callbacks did not signal failure. It is not possible to identify the exact CPU hog from this readout alone. Peak ISR is not average ISR time, and TIM17's 16-bit wraps at ~682.7 µs may undercount slow individual resource calls.

## Regression context

A previously successful physically tested **unfiltered** fast4+micro build using the *same slot24 module serial* reported positive module return **202 encoded frames**, `gp0_dma_error=0`, no PCM loss, no missed DMA halves, no codec/FSK errors, and a peak GP0 ISR value of **3,147 ticks**. The new AA3 run's peak is **341 ticks (~3.55 µs, 10.8%) higher**, a cross-run observation rather than an isolated causal estimate. The high-rate IRQ occurs at ~10,908 times/s; even an additional ~3.55 µs on **every** IRQ would consume ~3.9% more CPU, but peak deltas cannot establish the real mean load.

The **AA3 code's three high-rate IIR stages** are a plausible contributor to the exhausted real-time margin. However, the measured failure cannot distinguish added IRQ cost from codec performance variance, table SPI latency, FSK scheduling, or host/preload timing. One run cannot prove AA3 makes the radio consistently fail, though AA3 is **not hardware-qualified** and must remain default-off. The first intelligible microphone WAV and full 2 MiB SPI calibration backup remain private and intact.

## Next safe actions

1. **No additional AA3 microphone PTT** until the regression is addressed; do not try to hide it by increasing the PCM queue using protected RAM0 (AA3 leaves only 12 bytes free) or borrowing module scratch. Do not alter ISR queue overflow/dekey safeguards.
2. Safest immediate operational recovery is an attended **MCU-only rollback to the exact previous working 118,408 B** `coop-gpf1-gpt1.bin`, SHA256 `c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec`. Preserve the signed slot24 KUL2 module, SPI catalog, and bootloader. This requires the operator to put the K6 in UPDATE mode and authorize flashing; **no rollback has been executed**.
3. Independently inspect real thumbv6m ISR machine code and cycle/instruction cost in matched-AA3-on/off profiles, plus additional headroom/foreground codec throughput tests. The linked AA3 IRQ occupies 0x368 bytes versus the known-good 0x340 bytes; this static **+40-byte** difference does not measure extra cycles. Test smaller/filter-alternative approaches offline but do not flash FIR11 just because it preserves more consonants.
4. Once real-time budget is restored, retest **one controlled normal speech and 1 kHz tone** with synchronized RTL-SDR evidence and immediate boot-local GPF1/GPT1 retrieval. Compare noise energy, voicing codes, codec packets and no-overflow performance before recommending a default filter.

The Sotto Monitor was receiving at 443.500 MHz, tune offset -31,300 Hz at the time of diagnostic capture; it reported zero valid and invalid frames. This does **not** prove no RF appeared; it only proves the monitor did not decode any candidate packet.
