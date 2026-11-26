# Physical Sotto GP0 ENGINE abort: PCM DMA producer overrun evidence

Date: 2026-11-26. Status: **fault captured successfully; no RF follow-up tests performed**.

## Actual GPF1 fault report

After operator manually launched SOTTO GP0 on dummy load and held PTT, LCD displayed `GP0 ENGINE`. As the first action after reconnecting the programming cable (without a prior status query), the CLI fetched the boot-local `GPF1` report and ended the cable session, restarting the radio. It passed the native host decoder and was saved unmodified to:

`/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-live-failure-20261126-namefix-gpf1.json`

- `valid:true`, module serial `1795730062`, module return `-12`.
- GP0 capture **started**; DMA ISR count **1,310**.
- `gp0_dma_error:3`; `pcm_lost:true`; missed halves **0**.
- Highest observed TIM17 ISR elapsed **4,866 ticks**, nominal **~50.7 µs at 96 MHz**, compared to a nominal ~8,800 tick (~91.7 µs) half-period and 9,000-tick firmware interrupt-budget guard. **Peak**, not average runtime.
- Codec resource-read callback errors **0**, FSK callback errors **0**, last FSK error **0**.

## Hardware error-code interpretation

In `firmware/k6-aura/sotto-api30-gp0-capture.rs`, error 3 is assigned when `dec_push` returns -1, or when `pcm_sample` cannot append a sample. The decimator rejects only `fs<=16000`, `fs>160000`, or `bits>64`; the hardware run uses `fs=87260` and a packed 64-bit PDM popcount bounded to 0..64. Under normal undamaged state these checks cannot fail. The PCM writer reports lost on `produced-consumed>=4` (four 80-sample PCM blocks, ~40 ms maximum queued audio), or `offset>=80` (also unreachable from initialized normal producer). Therefore **PCM ring full** is the strongly supported likely cause; a hidden memory-corruption/invalid decimator state has not been independently excluded.

The 1,310 IRQs each process 8 of the 87,260/s PDM groups; the decimator emits 8,000 PCM samples/sec, giving approximately `1310*(8*8000/87260)=960.8` PCM samples, or about 120 ms after capture start. This matches the observed one-second TX LED including the module's explicit 800 ms RFIC GP0 bias settling stage. The abort is from failure to drain codec PCM blocks quickly enough, **not** evidence of missing RF or a broken microphone route. The GPF1 trace also confirms real IRQ activity.

## Physical-timing-calibrated virtual RF replay

The previously captured on-radio read timings were baseline 20/128-byte means 36.72/193.75 µs and streaming SPI means 25.00/126.56 µs. They were fitted as a fixed+linear read-cost approximation into the compiled Cortex-M0 GP0→Sotto→FSK→virtual decoder with a 200 ms 8 kHz PCM waveform, `cpi=1.5` and six different model CPU interrupt cost assumptions.

Evidence:
`/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-live-failure-measured-irq-replay-20261126.json`

| Modeled cost per PCM sample | Original SPI | Streaming SPI |
|---|---|---|
| 4,000 cycles | Full session, no loss (high-water 3/4) | Full session, no loss (2/4) |
| 6,000 cycles | PCM full, abort after 1,280 samples | Full session, no loss (3/4) |
| 6,600 cycles | PCM full, abort after **960 samples** | PCM full, abort after 1,280 samples |

For intuition, a peak real DMA IRQ duration 4,866 ticks corresponds to roughly 6,640 cycles **per emitted PCM sample** after dividing by ~0.733 sample/IRQ; however the peak IRQ duration is not the mean, and the virtual model charges interrupt work per PCM sample rather than reproducing the real 8-group DMA cadence. This numerical match is encouraging **sensitivity evidence, not proof of exact per-sample CPU cycles**.

## Engineering priority

1. Optimize **interrupt-time PDM unpack/popcount and decimation**; profile worst-case and typical ISR cycles on hardware, preserve the fixed 87,260→8,000 decimation and signal quality. Aim for meaningful headroom below the 9,000 tick IRQ bound and allow more main-loop codec time.
2. Combine the measured ~32–35% faster streaming SPI driver with the **same fielded feature profile and GPF1 trace** in a later attended build, with compiler/arena/stack/RAM0 gate and original SPI catalog preserved. This may eliminate the queue overrun at moderate ISR costs, but it is not guaranteed.
3. For a stronger proof, add a **saturating PCM producer-consumer high-water and lost-at-overflow index** to a future firmware trace without growing the original 9,784-byte KUL2 module or reducing its 48-byte scratch margin. Distinguish ring-full from unexpected decimator invalid state in a separate reason counter.
4. Run compiled virtual RF regression and a short attended dummy-load PTT/SDR test; require no overflow, correct voice-frame output and recovery to VFO before integrating.

The radio currently runs the **corrected temporary GPF1 diagnostic firmware** (not the earlier known-good image). The normal source/module/library catalog is unchanged. The full verified 2 MB SPI backup and original fielded 116,796-byte firmware are preserved. The cable read terminated by restarting the radio, and the bench lock was free when last checked. **Do not interpret this successful fault classification as a Sotto TX fix.**
