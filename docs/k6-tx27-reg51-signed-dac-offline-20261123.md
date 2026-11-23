# TX-27 — FD6818B `REG_51` single-write 8-bit signed DC frequency DAC: offline qualification

**Date:** 2026-11-23  
**Queue issue:** [#20](https://github.com/tcarwash/flux_radio/issues/20) (subordinate to `#9` P0 / `#10` P1 / `#12` P2; coordinated via `#11`)  
**Branch:** `research/tx27-reg51-signed-dac-offline-20261123`  
**Scope:** Strictly **OFFLINE** Cortex-M0 build, Unicorn emulation, and independent synthetic FM-discriminator verification. **No radio, serial port, SDR, SPI flash, or physical RF transmission was performed.**

## Hypothesis and provenance

Two separate `0x4829` physical findings on `research/tx16-subaudio-modulation` have never been combined:
1. **TX16M/N** proved that loading `REG_08` with all-1s (`0x0FFF`, `0x87FF`) makes DCS output a **constant DC frequency offset** whose sign flips on a single write to `REG_51[13]` (64/64 exact bits up to ~315 sym/s).
2. **TX16B** proved that `REG_51[6:0]` provides a monotonic **7-bit subaudio FM deviation gain** (`0..127`).

Combining both in **one 16-bit register write**:
```text
REG_51 = 0x8000 | bw_bit | (neg ? 0x2000 : 0) | (mag & 0x7F)
```
tests whether `REG_51` acts as an **8-bit signed (`-127..+127`) direct-to-PLL DC-coupled frequency DAC** with continuous carrier phase and zero droop. At 200–300 sym/s, 4-FSK yields **400–600 bps** (and 8-FSK yields **600–900 bps**), without touching `REG_59`/`REG_5F` or MICIN.

Additionally, TX16M/N left `REG_07` at `0x0AD7` (134.4 Hz) and `REG_51[9]=0`. Compile-time variants `(TX27_CLK_WORD=0x1FFF, TX27_BW_DISABLE=1)` test whether the subaudio LPF corner widens when the DCS clock and auto-BW bit change.

## Guarded inert-by-default C module (`c/k6/mod/tx27_reg51_dac.c`)

- **Inert default (`TX27_BENCH_ENABLE=0`):** compiles to <256 bytes, returns `-9`, zero RFIC/TX writes.
- **Armed compile gates:** `TX27_RF_ENABLE=1` requires `TX27_BENCH_ENABLE=1`, `TX27_FIXTURE_ACK=1`, and `TX27_WATCHDOG_ACK=1`; fixed to `433.500 MHz`.
- **Runtime guards:** requires `REG_00 == 0x4829`, `rx_hz == 433.500 MHz`, not already TX, double-checked RSSI `<= -90 dBm`, initial `REG_51 == 0`, key/power-switch abort, `<=1800 ms` TX lease, and full snapshot/restore of `REG_51`, `REG_52`, `REG_70`, `REG_71`, plus `TIM17`/`RCC_APB2ENR`. Never touches `REG_59` or `REG_5F`.
- **TIM17-timed sequence (~1.07 s total, self-compensated for `fd_write` latency):**
  1. `60 ms` carrier-only lead reference (`REG_51 = 0`).
  2. **9-step static signed DAC staircase** (`+60, +40, +20, 0, -20, -40, -60, +60, -60`, `45 ms/step`) — verifies monotonicity, sign symmetry, zero-hold, and repeatability.
  3. `40 ms` carrier-only separator.
  4. **24-symbol 4-FSK pilot** (`+3,-3,+1,-1` × 6) + **64 held-out 4-FSK symbols** (128 bits = `"FLUX TX27 DAC!"` + CRC-16/CCITT-FALSE) at **200 sym/s (400 bps, 5 ms/sym)** + 2 guard symbols.
  5. **20-step `2.5 ms` alternating `+60/-60` burst** — measures dynamic edge retention for `(TX27_CLK_WORD, TX27_BW_DISABLE)` comparisons.
  6. `60 ms` carrier-only shutdown guard.

## Offline verification

```sh
python3 -m pytest -q tests/test_k6_tx27_reg51_dac.py
```

Covers inert refusal, compile-time ACK enforcement, runtime preflight/key-abort restoration, full Cortex-M0 Unicorn execution across `(0x0AD7, bw=0)` and `(0x1FFF, bw=1)` variants (`write_us=117`, `late=0`), end-to-end 0/64 held-out 4-FSK symbol errors with CRC-16 pass (`"FLUX TX27 DAC!"`), and strict rejection on the `ignore_gain_in_dcs=True` binary-only negative control.

## Future physical bench gate (not authorized by this offline package)

1. Exclusive `#11` lease ACK + canonical nonblocking `serial_lock('/dev/ttyUSB0')`.
2. Rated 50-ohm dummy load, RTL-SDR at `433.500 MHz`, independent TX watchdog.
3. Run default variant `(0x0AD7, bw=0)` first; backup/restore/verify original signed slot30 and watched VFO A/B.
4. Only if `(0x0AD7, bw=0)` passes staircase linearity & 4-FSK CRC, separately compare `(0x1FFF, bw=1)` edge retention in a second leased run.
