# TX-26 — FD6818B 4800-baud 4-FSK (`REG_5F` sign + symbol-rate `REG_70` magnitude): offline qualification

**Date:** 2026-11-23  
**Queue issue:** [#19](https://github.com/tcarwash/flux_radio/issues/19) (subordinate to `#9` P0 / `#10` P1 / `#12` P2; coordinated via `#11`)  
**Branch:** `research/tx26-c4fsk-bchip-offline-20261123`  
**Scope:** Strictly **OFFLINE** Cortex-M0 build, Unicorn emulation, and independent synthetic FM-discriminator verification. **No radio, serial port, SDR, SPI flash, or physical RF transmission was performed.**

## Hypothesis and provenance

`upstream/main` (`6bb97ac`, `docs/k6-c4fm.md`, `c4probe.c`, `c4lock.c`, `fusotx.c`) demonstrated on original `0x4819` hardware that the native 4800-baud binary FSK modem can emit true 4-FSK (YSF / C4FM / DMR / FuSotto, decoded by a physical FTM-300DR and 604/610 FuSotto superframes) by putting each symbol's **sign (`±`)** into `REG_5F` and rewriting **`REG_70[6:0]`** (FSK/tone-2 gain) on symbol boundaries (`117 µs` SPI write inside a `208.3 µs` symbol).

On FD6818B (`0x4829`), Flux has already verified both halves separately (4800-baud native `REG_5F` FSK in FSK-10B/FSK-13 and rapid live `REG_70` gain updates on tone 1 in TX-15X), **but has not tested `REG_5F` + live mid-packet `REG_70[6:0]` together**.

## Guarded inert-by-default C module (`c/k6/mod/tx26_c4fsk_probe.c`)

- **Inert default (`TX26_BENCH_ENABLE=0`):** compiles to <256 bytes, returns `-9`, performs zero RFIC or TX calls.
- **Armed compile gates:** `TX26_RF_ENABLE=1` requires `TX26_BENCH_ENABLE=1`, `TX26_FIXTURE_ACK=1`, and `TX26_WATCHDOG_ACK=1`; fixed to `433.500 MHz`.
- **Runtime guards:** checks `REG_00 == 0x4829`, `rx_hz == 433.500 MHz`, not already TX, double-checked RSSI `<= -90 dBm`, no active FSK work mode (`REG_3F & 0xA000 == 0`), key/power-switch abort, `<=1800 ms` TX lease, `fd_write` latency `<= 166.7 µs`, and full snapshot/restore of `REG_3F, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x70, 0x72` plus `TIM17` / `RCC_APB2ENR`.
- **FIFO ordering note (`#15`):** writes `REG_5D = 0x3B00` *after* `REG_59 = 0x80F8 -> 0x00F8` plus a 2 ms settle before `REG_5F` preload, and 15 ms settle before `TX_EN` (matching `fd6818b.rs` `mdc1200_tone_tx` and `upstream/main:c4lock.c`).
- **Four 30-word (480-symbol) packets inside one <=1800 ms TX lease (~900 ms total):**
  1. **P0:** 480-symbol PN9 at fixed outer gain `72` (outer `±Δf` & symbol clock baseline).
  2. **P1:** 480-symbol PN9 with mid-packet `REG_70[6:0]` staircase `72, 54, 36, 24, 12` (96 symbols / 20 ms each) — discriminates live vs latched `REG_70`, linearity, and exact write-to-RF step lag.
  3. **P2:** 240 zeros then 240 ones at gain `72` — checks TX high-pass droop.
  4. **P3:** 64-symbol `±3/±1` pilot (`+3,-3,+1,-1` × 16) followed by **416 held-out 4-FSK symbols (104 bytes, CRC-16/CCITT-FALSE)** with symbol-timed `REG_70[6:0]` writes (`72` vs `24`).

## Independent analyzer (`tools/k6_tx26_c4fsk_analyze.py`)

- Acquires all 4 packets strictly from public `16×0x55 + 076b3e36` correlation (no payload truth).
- Measures P0 outer deviation & sign BER, P1 monotonicity / latched rejection / step landing offset, P2 droop ratios, and P3 pilot-only threshold calibration followed by held-out 416-symbol decoding and CRC-16 check.

## Offline verification

```sh
python3 -m pytest -q tests/test_k6_tx26_c4fsk.py
```

Covers inert refusal, compile-time ACK enforcement, runtime chip/freq/RSSI/mode/key-abort guards, full Cortex-M0 Unicorn execution (`write_us=117`), exact register + TIM17 restoration, end-to-end 0/416 held-out 4-FSK symbol errors with CRC-16 pass on the live model, and strict rejection on the `latched=True` negative control.

## Future physical bench gate (not authorized by this offline package)

1. Exclusive `#11` lease ACK + canonical nonblocking `serial_lock('/dev/ttyUSB0')`.
2. Rated 50-ohm dummy load, air-coupled/attenuated RTL-SDR at `433.500 MHz`, independent TX watchdog.
3. One single-shot run (no auto-retry); backup/restore/verify original signed slot30 module and watched VFO A/B.
4. Pass requires monotonic P1 steps (not latched), P2 droop >0.85, and P3 pilot-calibrated CRC-valid decode (or if lag differs from +1.75 sym, record exact P1-measured lag before any single follow-up).
