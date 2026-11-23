# Flux FSK-09S-H — real Cortex-M0 Unicorn emulator qualification

**Date:** 2026-11-23. **Status:** EMULATOR PASS, PHYSICAL CHIP UNTESTED. Source frozen and committed previously at `86164de`. This follow-on changes **only emulator/test documentation**, not the actual armed transmitter source or compiled code SHA. The entire study remains **offline; no serial, SDR, firmware flashing or RF transmission**. Hardware lease request in shared queue #11 is still separate and unacknowledged.

## Meaning of OFFLINE for this research

An offline *test* must **execute the actual compiled Cortex-M0 Thumb module in Unicorn**, with a modeled FD6818B register interface, FIFO draining, IRQ latch, and hardware TX/completion clocks. Python-only source checks, protocol simulators and synthetic IQ receivers are *supporting checks*, **not** completion of the offline research gate. The actual radio remains the separate physical qualification; emulation cannot establish the real B silicon's register semantics, electrical timing or actual RF signal quality.

We located **Unicorn 2.1.4 already cached** on the development host and ran it without network access or installing globally. For repeatable strict execution, `tools/run_fsk09s_h_emulator.sh` uses only locally cached `uv run --offline --no-project --with unicorn==2.1.4 --with pytest==9.2.0 --with numpy==2.5.5 --with scipy`; its startup fails if Unicorn is unavailable instead of counting skips as success.

## Executed binary and modeled peripherals

`tests/test_k6_fsk_bchip_ae_emulator.py` calls `run_m0()` from `tests/test_k6_fsklab.py`, which compiles the actual API3 source using `modem.k6mod.build()` and executes its **ARMv6-M Thumb machine instructions** under `unicorn.UC_ARCH_ARM | UC_MODE_THUMB | UC_MODE_MCLASS`, explicit CPU `UC_CPU_ARM_CORTEX_M0`. API stub calls are intercepted and mapped onto a **hypothetical B-chip device** model. The simulator models:

- `REG_5F`: actual word preload; tracks 49 words per frame, asserts **no active-TX writes** and max occupancy 49, under the 64-word previously physically proven bound.
- Native 4800bps 2-FSK FIFO consumption: 16-bit words drained at **300 words/s** from each frame's start; completion only after draining 49 words **plus 8ms modeled hardware completion latency**.
- `REG_5E`: correct prospective FIFO AE threshold **bits `[9:3]`**, preserving untouched register bits, and a deliberately *wrong* legacy `[11:6]` behavior for falsification.
- `REG_3F`: the FIFO-AE IRQ enable **bit14**, which must be present before the synthetic model emits an AE flag.
- `REG_02`: **write zero then read** latch; both synthetic alternate encodings (bit14=0x4000 vs low-nibble event code `0x000E`), with early/absent/stuck IRQ variants. The model monitors write/read ordering.
- Host API guards: frequency/chip ID refusal, single TX-on and off, final PA-off, exact source-writable register snapshot/restore, and a per-frame timeout if completion never occurs. No flash, calibration, destructive FIFO read or refill.

### Raw emulator trace (one example per IRQ encoding)

| Measurement | Emulated bit14 IRQ | Emulated event14 IRQ |
| --- | ---: | ---: |
| Module return | **98** (two 49-word frames) | **98** |
| Frame 0 threshold | **24 words** | **24 words** |
| First AE at 24 | **84ms** | **84ms** |
| Frame 1 threshold | **8 words** | **8 words** |
| First AE at 8 | **136ms** | **136ms** |
| AE onset shift | **52ms** | **52ms** |
| Completion times | 172ms, 171ms | 172ms, 171ms |
| Latched IRQ writes | 295 | 295 |
| First word, preloaded | proper expected public vector | proper expected public vector |
| Mid-TX FIFO writes | **0** | **0** |
| Source register restoration | **PASS** | **PASS** |
| End state | **tx_off** | **tx_off** |

These times are *not measurements of the physical FD6818B*. They show the compiled code will distinguish the two model IRQ hypotheses while preserving the controlled FIFO and restoring the radio state when modeled correctly. The model computes the expected 24-vs-8 threshold shift; this is **self-consistency verification**, not an independent chip discovery.

**Negative emulator cases:** no AE, early/stuck AE, wrong `[11:6]` threshold hardware map, IRQ source silent despite set enable bit, missing TX completion/timeout, wrong chip ID, wrong RX frequency, and unarmed module. Missing completion leads to the expected `-14` and TX-off, and all viable negative runs restore the saved RF registers without attempting refill. The IRQ scorer rejects those spurious or absent timing patterns. A successful model IRQ is still flagged **not yet safe for refill**.

### Strict run

```bash
# No real radio interfaces or network: cached Unicorn execution is MANDATORY
./tools/run_fsk09s_h_emulator.sh
```

**Observed:** **59 passed, 0 skipped, 0 failed**, including 11 real-emulator scenarios, existing Cortex-M0 legacy FSK tests, strict external harness guards and the independent IQ scoring unit tests. Previous “30 passed / 18 skipped” run without Unicorn is **superseded as an offline completion claim**.

## Physical discriminator remains outstanding

This emulator gate demonstrates that the **proposed code and test scoring are safe and internally consistent under explicitly stated chip hypotheses**. It does *not* show which IRQ mechanism the actual FD6818B supports. A measured, independent physical run is required to distinguish the B chip's real `REG_5E` and `REG_02` behavior. Preserve exact frozen source+binary SHA before physical testing, as well as separate authenticated serial lock/queue ACK, dummy load, bounded TX watchdog, original module and watched dual VFOs on exit, and an independent SDR payload check. The physical pass criterion must not be relaxed based on this emulator trace.
