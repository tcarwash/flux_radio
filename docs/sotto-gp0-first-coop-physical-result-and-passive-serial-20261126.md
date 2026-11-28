# First physical cooperative GP0 diagnostic — PCM queue full confirmed

Date: **2026-11-26, Pacific**. The operator manually ran `CATALOG → SOTTO GP0` on the flashed MCU diagnostic image (`c69a8742…a83ec`) and reconnected the programming cable without power-cycling. The **first** subsequent cable action fetched *both* boot-local read-only reports `0x75` GPF1 and `0x76` GPT1 in one session, saved both exact raw replies before reboot and validated consistent module serial `1795730062` and no decoder errors. Raw hex and decoded reports: [`evidence/sotto-gp0-first-coop-diagnostic-physical-20261126.json`](evidence/sotto-gp0-first-coop-diagnostic-physical-20261126.json).

## What actual silicon reported

| Metric | Cooperative preload + fast ISR/SPI, 2026-11-26 | Previous timed physical baseline |
|---|---:|---:|
| GP0 result | `-12` (`GP0 ENGINE`) | `-12` |
| Exact failure | DMA reason **2: four PCM blocks full** | DMA reason **2: four PCM blocks full** |
| Successful GP0 half-IRQs | **2,182** | 1,746 |
| Approx. active capture duration | **200 ms** | ~160 ms |
| Peak GP0 ISR duration, TIM17 ticks | **3,147** (~32.78 µs) | 3,221 (~33.55 µs) |
| GP0 missed halves | **0** | 0 |
| Resource callback failures | **0** | 0 |
| FSK callback failures | **0** | 0 |
| Codec resource reads | **811** | 506 |
| Accumulated resource-read callback time | **4,849,338 TIM17 ticks = ~50.51 ms** | ~32.71 ms |
| Maximum single resource read | **24,779 ticks = ~258.11 µs** | 25,059 ticks = ~261.03 µs |
| FSK preload calls / packets attempted | **1** | 1 |
| Maximum FSK host callback during preload | **2.2 ms** | **14.8 ms** |
| PCM backlog at *later* stop | **3 blocks** | 3 blocks |

Caveats: 2,182×8/87,260 yields the approximate capture time, not a wall clock attached to the PTT. Timing sums are elapsed callback times and include interrupt preemption; individual TIM17 timer wraps of ~682.7 µs would undercount. The 2.2 ms cooperative value is the **largest individual** callback across the seven preload phases, not total preload duration nor first-RF time. The attempted FSK preload is not proof of a completed RF packet. The earlier operator's report of brief RF/modulation was not independently recorded/decoded here. The source is the *single new physical run*, not simulation.

**Conclusion:** spreading synchronous FSK work materially reduced the longest contiguous block (~85%) and lengthened capture (~25%), but GP0 still cannot sustain the 8kHz producer. We have now directly excluded the proposed FSK-callback rejection in this failed run; codec arithmetic and codebook-access throughput remain leading targets. The module is still the original 9,784-byte slot24 version. A separate four-optimization 9,784-byte module exists **offline unsigned only**; it has **not** been installed. The faster encoder is therefore not evaluated by this hardware result.

## No-firmware-space serial follow-up

The already-installed firmware emits `K6 MOD run serial=...` and `K6 MOD ret=...` as **UART console text**, and the module executes synchronously. `tools/k6_sotto_gp0_follow.py` now implements a read-only, bounded-time **passive serial listener**. It watches for exact `SOTTO GP0` module serial `1795730062` rather than another module's return, logging start/finish with no UART commands during capture. When `--save` and `--ack-reboot` are explicitly selected, and **only after the expected module has returned**, it performs one cable handshake, verifies protocol5 and module arena, queries both GPF1/GPT1, validates the expected run, preserves raw bytes and serial text to a fresh JSON file *before* closing the session/rebooting the radio. Console-only mode performs **zero UART writes** and does not reboot.

Safe procedure for a subsequent attended dummy-load test, with the serial cable left connected and no PC application competing for `/dev/ttyUSB0`:

```bash
python3 tools/k6_sotto_gp0_follow.py --port /dev/ttyUSB0 \
  --module-serial 1795730062 --timeout 120 \
  --save /path/to/a/NEW-gp0-live-test.json --ack-reboot
```

Start this watcher **before** the operator selects/launches SOTTO GP0. It does not key PTT or generate RF. The watcher must not be left running at the same time as other radio serial tools; bench and port locks still apply. If the expected run does not return before the timeout, it exits *without sending a handshake*, leaving a potentially active radio untouched. Use physical controls/standard dekey recovery as appropriate. This is **live lifecycle observation with immediate post-run diagnostic capture**, not streaming PCM or real-time encoder-cycle profiling.

### Continuous live debug is different

The current *lean* K6 UART runs 115200 8N1 with synchronous `send_byte` busy-wait; each 12-byte message could add roughly 1 ms of foreground overhead, enough to perturb the already nearly saturated PCM queue. The current MCU diagnostic image has only **24 bytes free in RAM0** and **4,472 flash bytes available**; the installed module has only **48 bytes scratch margin**. Constant debug messages inside `slim_encode10`, GP0 DMA IRQ, or every 10 ms block would be misleading or unsafe. For more precise foreground attribution, prefer an opt-in tiny *record in RAM, emit after dekey* trace with bounded host field storage and callback timestamps, tested in the linked ARM emulator first. The host-side follower above provides useful repeatability without **any** image, module, ISR, arena or resource changes.

**This investigation did not transmit, reflash, install/replace any module, or use the operator's serial port for a second hardware test.**
