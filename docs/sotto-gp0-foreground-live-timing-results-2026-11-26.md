# First complete on-radio GPF1 + GPT1 foreground-timing capture

**November 26, 2026 (Pacific).** This is a real attended manual PTT test of the original SOTTO GP0 module on a dummy load, using the temporary opt-in firmware timing image. No additional RF transmissions, SPI writes or firmware flashes were performed to extract or analyze the result.

## Saved wire evidence

The programming cable was reattached without a battery/power cycle. Both read-only cable commands (`0x75` GPF1 and `0x76` GPT1) completed in **one** cable session. The revised host reader saved **both raw replies as hex before ending the session/rebooting**, then decoded them successfully. Exact full record including `fault_raw_hex` and `timing_raw_hex`: [`docs/evidence/sotto-gp0-foreground-live-timing-20261126.json`](evidence/sotto-gp0-foreground-live-timing-20261126.json).

The raw replies are exactly **44 and 40 bytes**, `decode_error=null`, same module serial **1795730062**, `valid=true` for both.

| Signal | Observed physical value |
|---|---:|
| Module return | **-12** (`GP0 ENGINE`) |
| GP0 DMA error | **3** |
| Exact cause | **2: PCM queue full in DMA IRQ** |
| PCM loss | true |
| PCM queue at DMA error | **4 / 4 blocks** (80 samples per block) |
| Queue at later mic shutdown | **3 blocks**, sample offset 0 |
| Successful DMA half-buffer interrupts | **1,746** |
| Peak IRQ duration | **3,221 TIM17 ticks** (~33.55 µs at nominal 96 MHz) |
| Missed DMA halves | **0** |
| Resource-read callback failures | **0** |
| FSK callback failures | **0** |
| Resource read count during GP0 capture | **506** |
| Max observed resource callback duration | **25,059 TIM17 ticks** (~261.03 µs) |
| Sum of measured resource callback durations | **3,140,064 ticks** (~32.709 ms) |
| Mean elapsed resource callback duration | **~64.64 µs** |
| FSK preload (op0) count | **1** |
| FSK preload max elapsed duration | **148 × 100 µs = 14.8 ms** |

The **reason=2 value is recorded in the DMA interrupt itself**, while the 3-block backlog comes from subsequent op1 stop/cleanup. One block can be consumed between the failure and cleanup; these values are not contradictory.

The IRQ counter corresponds approximately to **160.1 ms of capture** given 8 of the 87,260 PDM groups processed per half interrupt and 8 kHz PCM production, with slight uncertainty because the final failed interrupt is not counted. Therefore ~32.7 ms of resource callback elapsed time and 14.8 ms of synchronous FSK startup occurred within about 160 ms of the active live-capture period.

**Timing qualifications:** Both callback measurements are elapsed times, not exclusive CPU cycles, and may include interruption by GP0 IRQs. They must not simply be added together as independently measured CPU utilization. The resource timing uses a 16-bit 96-MHz timer that wraps every ~682.7 µs: a single read spanning one or more wraps would be undercounted. The FSK timing uses 100 µs ticks. The 3,221-tick IRQ value is a *peak*, not a mean CPU duration. No codec-internal math timing or real RF voice-packet decode was captured in this operation.

## What this proves and what it does not

The previous inference is now **directly verified**: under live Sotto transmission, the four-block PCM producer/consumer queue fills. This is *not* an invalid decimator input, and the module's `-12` does not indicate a codec resource-read or FSK callback failure. Data shows significant synchronous foreground work during the short active capture: hundreds of flash lookups and a single 14.8 ms FSK preload. Neither callback is failing, but both take foreground time that could delay codec processing and PCM draining.

It does **not** by itself prove which foreground operation is the dominant reason throughput falls behind 8 kHz PCM production; the encoder's Cortex-M0 calculations and other foreground work are unmeasured. A 14.8 ms contiguous preload is material with only 40 ms total PCM queue depth, while the 32.7 ms resource read elapsed time is spread over 506 reads and includes potential DMA preemption.

The first attempted dual trace captured the *same code-3 queue-full cause*, but the earlier host parser mistakenly insisted that the **later** shutdown backlog had to equal four. That earlier cable session rebooted the radio before writing evidence; it was repaired in commit `f273adf6`. This report is the first fully preserved wire-level GPF1+GPT1 capture.

## Recommended next engineering work

Prioritize reducing **contiguous foreground stalls** while preserving live PCM production, Sotto codec compatibility, original module size, RFIC register safety and PTT/dekey guards. The obvious candidate is the asynchronous FSK callback's synchronous **op0 preload**: it snapshots nine RFIC registers, writes configuration registers, then 33 FIFO words via bitbanged transfers before returning. An offline prototype could move those writes into bounded cooperative steps invoked by op1 polling, but must prove payload pointer lifetime, FIFO ordering, PA state, startup watchdog, cancellation, and safe abort/register restoration before any radio flash. No such implementation is installed yet.

Also study the codec table-read pattern and CPU-heavy codec code paths, since 506 reads consume ~33 ms elapsed during capture and the decoder still exceeds sustainable throughput with fast SPI. A safe optimization must respect the K6's **32 bytes free protected RAM0** on this trace build and only **48 bytes module scratch** on the existing loadable module. Do not add a large cache by borrowing DMA/stack space.

A worthwhile benchmark is a model ablation at measured SPI times and real op0 preload cost versus the zero/pre-staged op0 scenario, plus a trace of codec compute time if the first fix is inconclusive. Preserve the independent virtual RF decoder and error/dekey regressions.

**Hardware state:** the radio remains on the previously installed GPF1+GPT1 temporary diagnostic firmware, and the unchanged signed SOTTO GP0 module is in catalog slot 24. The full verified 2 MiB SPI backup and known-good field firmware remain available. The bench has no owner, and the cable read session has ended/rebooted the radio normally.

## Offline FSK preload sensitivity — not sufficient by itself

Following the full hardware capture, an additional **offline compiled Cortex-M0 virtual RF replay** used the hardware-calibrated streaming SPI read costs and a 250ms synthesized 8kHz PCM stream. Its FSK model assigns a per-RFIC-register-write delay, not the exact device's asynchronous 20ms settle sequence; these are sensitivity experiments, **not a physical RFIC throughput measurement**. The model's assumed CPI/ISR costs are deliberately conservative and are not measured hardware averages.

| CPI assumption | GP0 ISR cycles per PCM sample (model) | FSK write cost (model) | PCM samples before overflow | Complete? |
|---:|---:|---:|---:|---|
| 2.5 | 4,000 | 2 µs (nearly zero) | 1,920 | No |
| 2.5 | 4,000 | 60 µs | 1,600 | No |
| 2.5 | 4,000 | 300 µs | 1,120 | No |
| 3.0 | 3,000 | 2 µs (nearly zero) | 1,600 | No |
| 3.0 | 3,000 | 60 µs | 1,280 | No |
| 3.0 | 3,000 | 300 µs | 1,120 | No |

Source evidence: [`docs/evidence/sotto-gp0-fsk-preload-ablation-20261126.json`](evidence/sotto-gp0-fsk-preload-ablation-20261126.json). All six model runs reached a four-block PCM queue and returned -12. **Shortening the FSK preload helps** (~320 extra captured samples in both conservative test conditions from 60→2 µs per RFIC write), **but does not eliminate the sustained throughput problem** under these assumptions. Therefore work on cooperative FSK preload should be paired with optimization or profiling of actual codec computation/resource fetches rather than proposed as a complete fix in isolation.
