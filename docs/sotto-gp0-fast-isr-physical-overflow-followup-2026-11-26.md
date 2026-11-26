# Second attended GP0 failure: optimized ISR helps but encoder still underruns PCM drain

**2026-11-26, measured hardware, temporary diagnostic firmware active.** The original 9,784-byte KUL2 SOTTO GP0 module remains untouched.

## GPF1 side-by-side physical evidence

The user launched SOTTO GP0 on a dummy load, released PTT after the screen again showed GP0 ENGINE, and reconnected the programming cable without cycling the battery. As the **first** subsequent cable operation, the read-only `0x75` GPF1 trace was captured. The cable command then rebooted the radio normally.

| Firmware | Original trace-only GP0 | Optimized PDM ISR + SPI |
|---|---:|---:|
| GPF1 valid | true | true |
| GP0 DMA interrupts | 1,310 | **1,746** |
| Peak TIM17 ISR ticks | 4,866 | **3,188** |
| Peak ISR reduction | — | **34.5%** |
| Capture duration from DMA count | ~120 ms | **~160 ms** |
| PCM lost | true | true |
| GP0 error | 3 | 3 |
| Missed DMA halves | 0 | 0 |
| Resource-read callback errors | 0 | 0 |
| Async FSK callback errors | 0 | 0 |
| Module return | -12 | -12 |

The 8-group half-buffer DMA interrupts are at ~87,260/8 = 10,907.5 per second; 1,746 IRQs correspond to ~1,280 generated 8 kHz PCM samples. This is an approximation: ISR count increments only after successful halves, and the failing last half is not counted. ISR **peak** is not average CPU load; measured performance improvement does not establish how many cycles each codec block consumes. The four-block queue holds 4 × 80 samples (=40 ms), so an uninterrupted foreground stall or a sustained ~25% processing deficit can lead to error 3 even with improved DMA performance.

The immediate failure classification is still **PCM/DMA producer-consumer overflow**. Firmware error 3 also covers a decimator input-validation failure, but the fixed GP0 rate 87,260 and bounded 0–64 PDM group count make that alternative unlikely absent state corruption.

Physical raw GPF1:
`docs/evidence/sotto-gp0-fast-isr-physical-failure-20261126.json`
Prior fielded GPF1:
`docs/evidence/sotto-gp0-gpf1-physical-20261126.json`

## Fresh CPU-cost sensitivity replay

The independently hardware-measured streaming SPI timings were fitted by two points:
- 20-byte resource read: 25.00 µs mean
- 128-byte resource read: 126.56 µs mean

The exact compiled ARM GP0 TX module, pinned Sotto codec and K6 radio packet receiver were replayed for 250 ms at a model RFIC register-write cost of 60 µs/write (the actual bit-banged RFIC driver includes 24 bits × two nominal 1µs delays, plus GPIO overhead). The simulation still has no calibrated physical Cortex-M0 mean CPI, and the model's per-PCM-sample ADC cost is synthetic.

| Modeled average cycles/instruction (CPI) | Modeled GP0 ISR cycles per PCM sample | Result |
|---:|---:|---|
| 1.5 | 4,000 | Complete, 2/4 queue peak |
| 2.0 | 4,000 | Complete, 3/4 queue peak |
| 2.5 | 3,000 | Complete, 3/4 queue peak |
| 2.5 | 4,000 | **Overflow at 1,600 samples**, 4/4 |
| 3.0 | 3,000 | **Overflow at 1,280 samples**, 4/4 |
| 3.0 | 4,000 | Overflow at 1,040 samples, 4/4 |

The matching model shape under more conservative CPI reinforces that **codec foreground throughput** needs direct measurement. It does **not** measure or identify the actual processor CPI. Sensitivity JSON:
`docs/evidence/sotto-gp0-fast-isr-physical-cpi-sensitivity-20261126.json`

## Important competing bottlenecks

The asynchronous FSK service's **start** callback is still synchronous: before returning it snapshots nine RFIC registers, writes ~46 RFIC registers/FIFO words via software-bitbanged 24-bit transfers, and calls the firmware TX guard on each write. Its nominal bit timings are 2 µs per bit plus GPIO/software overhead; the total actual call duration is not measured. The 20ms FIFO settling delay itself is cooperative, so that particular 20ms delay does not directly block the encoder.

The codec also performs numerous flash-resident 20-byte vector reads and 128-byte centroid reads, and expensive Cortex-M0 correlation/FFT/search math. The GPF1 resource-error count being zero shows the reads **succeeded**, not that their *duration* was small. Existing offline hypothetical LRU experiments suggest an approximately 1.3 KB cache could improve a pessimistic timing model, but **there is no safe free SRAM for that cache**, and no corresponding real on-radio cache timing. Do not borrow module scratch, stack, or raw DMA buffers speculatively.

The SDR monitor at http://127.0.0.1:8024 reported `state:idle`, zero good/bad frames and a continuous receiver sample stream after the user test. This does **not** prove RF was absent; no active modem capture for this test was confirmed.

## Next engineering experiment

1. Add **low-overhead, boot-local firmware telemetry** to measure maximum **FSK preload callback duration**, codebook resource-read total/time distribution, the PCM ring's producer–consumer backlog and precise reason for DMA code3. Do not place timing work in the interrupt or change the loadable module. Bound instrumentation so any added load is measured, and retain the existing GPF1 read path as a compatibility fallback.
2. Reproduce the observed encoder stalls in compiled ARM virtual tests with a more conservative CPI model before deciding which section to optimize. Start with cooperative FSK FIFO preload (small bounded bursts across poll invocations) if timing measurements confirm that op0 blocks too long; preserve FSK ordering, contiguous FIFO format, TX lease, and abort/dekey safety.
3. Preserve original module code SHA256 `551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e`, arena 14,976B, main stack reservation 2,812B, original SPI backup and stock bootloader. Reject any build that violates the independent memory and readback gates.

**No subsequent RF transmission or firmware flash was performed after this GPF1 capture.** The optimized firmware remains installed and the bench is free.
