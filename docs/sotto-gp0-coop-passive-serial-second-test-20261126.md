# Successful passive serial follow: second GP0 hardware attempt still overflows

**Nov 26 2026 Pacific**, experiment branch `experiment/sotto-cooperative-fsk-preload-20261126`. The operator left the K6 PL2303 programming cable plugged into `/dev/ttyUSB0` while launching SOTTO GP0 manually. `tools/k6_sotto_gp0_follow.py` captured plain UART logs *without sending any bytes while the module executed*, then after observing the exact module `ret=-12`, it entered a read-only cable session and collected both GPF1/GPT1 diagnostic records, saving the raw bytes before the session rebooted the radio. The unattended serial observer did not key RF or modify firmware/SPI.

Complete serial lines, exact raw GPF1/GPT1 bytes and decoded reports are in [evidence/sotto-gp0-coop-passive-serial-physical-20261126.json](evidence/sotto-gp0-coop-passive-serial-physical-20261126.json). The tool saved an independent workstation copy under the dated recovery folder, verified SHA256 `55fb9cb16c4656be011d5892a67a646aef52fdfa84c8a5e37b3d9d113b5735fa`.

The UART trace confirms slot24 SOTTO GP0 loads as a 9,784-byte executable (serial `1795730062`), reports `tx on hz=443500000`, then `tx off`, and returns `-12` after **5,067 ms total module lifetime**, with `stack_free=1428 B`. The wall module lifetime is *not* PCM capture time. It encompasses foreground module operation and input wait. **No decoded RF packet or microphone PCM content was captured.** Note the external microphone and programming cable share the jack; cable-present testing is useful for reproducing timing failures but does not by itself establish valid analog voice input.

### Physical diagnostic table

| Field | Serial-cable-present run | Previous immediately-read run |
|---|---:|---:|
| Module return | `-12` | `-12` |
| GP0 reason | **2: PCM ring full** | **2: PCM ring full** |
| Successful DMA half-buffer IRQs | **2,182** | **2,182** |
| Approximate active PCM capture | **200.05 ms** | 200.05 ms |
| Peak ISR TIM17 ticks | 3,147 | 3,147 |
| Missed halves | 0 | 0 |
| Codec resource callback errors | 0 | 0 |
| FSK callback errors | 0 | 0 |
| Resource reads | 766 | 811 |
| Accumulated resource callback duration | **4,748,045 ticks (~49.46ms)** | 4,849,338 ticks (~50.51ms) |
| FSK preload calls | 1 | 1 |
| Peak single cooperative FSK callback | 2.2 ms | 2.2 ms |
| PCM backlog at later stop | 3 blocks | 3 blocks |

The reproducible 2,182 IRQ count and 3,147-tick peak are physical measured values in two separate reports; they do not prove that the codec always fails at that precise time under every input or test condition. The 40ms queue still reaches 4/4 at the DMA IRQ. The 3-block later stop occupancy is not contradictory because the foreground may consume a block between failure and dekey/cleanup.

**Next technical test:** the default slot24 encoder still has the slower full-width Cortex-M0 software divider and generic history copies. A separate four-optimization 9,784-byte encoder was built *offline*, with only 48 bytes scratch margin, but it has not been signed/installed or physically validated. Before changing it, requalify the module's exact source, scratch and signature/rollback with the current host image; then change only the module (not the MCU host) for a controlled A/B physical test. If run duration increases substantially or the PCM ring no longer overflows, this isolates encoder throughput. If it still overflows, more granular encoder-cycle timestamps (sampled and reported after dekey) are needed. Never stream continuous UART messages inside the 8kHz ISR or codec inner loop because this already saturated Cortex-M0 and its lean synchronous UART would distort timing.
