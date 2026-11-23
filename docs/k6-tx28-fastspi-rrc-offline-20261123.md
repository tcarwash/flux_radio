# TX-28 — Guarded Fast-SPI Bus Probe & Oversampled RRC 4-FSK Shaping: Offline Qualification

**Date:** 2026-11-23  
**Queue issue:** [#21](https://github.com/tcarwash/flux_radio/issues/21) (subordinate to `#9` P0 / `#10` P1 / `#12` P2; coordinated via `#11`)  
**Branch:** `research/tx28-fastspi-rrc-offline-20261123`  
**Scope:** Strictly **OFFLINE** Cortex-M0 build, bit-level Unicorn 3-wire SPI emulation, and RRC 4-FSK spectral/EVM simulation. **No radio, serial port, SDR, SPI flash, or RF TX performed.**

## Motivation

Aura's `fd6818b.rs` bit-banged SPI uses `BIT_DELAY_US = 1` (48 SysTick delay calls per 24-bit write ≈ **117 µs/write**, ~8.5 kWrites/s), whereas the BK4819/BK4829 3-wire SPI supports up to **8 MHz SCK**. Reducing register writes from ~117 µs to **~4–10 µs** unlocks:
- **4× to 8× oversampled RRC/Gaussian pulse shaping** on 4800-baud `REG_5F + REG_70` 4-FSK (19.2–38.4 kWrites/s, 52–26 µs write budget) to solve unshaped-pulse errors (`c4dmr`) and dramatically lower adjacent-channel power;
- Audio-rate (`8–16 kHz`) modulation on any internal register whose analog bandwidth supports it.

## Components

1. **RX-only fast-SPI integrity probe (`c/k6/mod/tx28_fastspi_probe.c`):**
   - Inert by default (`TX28_BENCH_ENABLE=0` → `-9`, <256 B).
   - Strictly **API level 2 (RX-only)**: refuses if TX bit (`REG_30`), PA (`REG_36`), or FSK (`REG_58`/`REG_3F`) is active.
   - Sweeps 6 half-bit delay ladders (`nops = 32, 16, 8, 4, 2, 1`) on benign idle register `REG_5A` (64 patterns/ladder), cross-checking every write via both fast readback and stock `api->fd_read()`, measuring exact 96 MHz `TIM17` write cycles, and restoring `REG_5A`, `GPIOB_MODER`, and `TIM17`.
   - **Firmware safety note:** any production fast-SPI path belongs inside the firmware's guarded `mod_fd_write()` (preserving `MOD_NO_WRITE` and TX watchdog checks), never bypassing firmware guards.

2. **Hardware-constrained RRC 4-FSK shaping simulator (`tools/k6_tx28_rrc_shaping.py`):**
   - Models the exact physical constraint of `REG_5F` (binary sign per symbol) + `REG_70[6:0]` (quantized 7-bit nonnegative gain updated `1×`, `4×`, or `8×` per symbol, clamping opposite-sign RRC lobes to 0).
   - Reports ACPR at `±12.5 kHz` (9 kHz BW), 99% OBW, and RRC matched-filter symbol EVM.

## Offline verification results

Verified via `python3 -m pytest -v tests/test_k6_tx28_fastspi.py` (**8/8 passed**) and `python3 tools/k6_tx28_rrc_shaping.py`:

- **Cortex-M0 PIC binary sizes:** inert default (`TX28_BENCH_ENABLE=0`) = **105 bytes** (returns `-9`, zero register writes); bench-enabled (`TX28_BENCH_ENABLE=1`) = **1412 bytes**.
- **Unicorn bit-level 3-wire SPI emulation:** all 6 delay ladders (`nop = 32, 16, 8, 4, 2, 1`, 384 fast writes + 384 fast readbacks cross-checked against `api->fd_read()`) completed with **0/384 errors**, exact `REG_5A` / `GPIOB_MODER` / `TIM17` restore, and all RX-only safety refusals (`-2`, `-6`, `-8`) verified.
- **Oversampled RRC 4-FSK (`alpha = 0.5`, 4800 baud, `±1944 Hz` outer deviation, 512 symbols):**

| Mode | Writes/s | Write budget | ACPR (±12.5 kHz, 9 kHz BW) | 99% OBW | RRC matched-filter EVM |
| --- | ---: | ---: | ---: | ---: | ---: |
| `rect_1x` (unshaped `REG_5F + REG_70`) | 4,800 | 208.3 µs | `-32.46 dB` | `9281.2 Hz` | `4.23%` |
| `rrc_4x_reg70` (`REG_5F` sym-locked + 4× `REG_70`) | 19,200 | 52.1 µs | **`-45.13 dB`** | `8437.5 Hz` | **`2.53%`** |
| `rrc_8x_reg70` (`REG_5F` sym-locked + 8× `REG_70`) | 38,400 | 26.0 µs | `-45.60 dB` | `8662.5 Hz` | `2.15%` |
| `rrc_4x_signed` (4× signed 8-bit DAC / TX-27) | 19,200 | 52.1 µs | `-46.01 dB` | `8400.0 Hz` | `1.83%` |
| `rrc_8x_signed` (8× signed 8-bit DAC / TX-27) | 38,400 | 26.0 µs | **`-49.08 dB`** | `8512.5 Hz` | **`0.55%`** |
| `ideal_rrc` (continuous reference) | — | — | `-48.77 dB` | `8550.0 Hz` | `0.27%` |

**Key design takeaway:**
- On symbol-locked `REG_5F + REG_70` (TX-26 path), **4× oversampling (19.2 kWrites/s, 52.1 µs budget)** already captures `+12.67 dB` of adjacent-channel rejection (`-32.46 dB` → `-45.13 dB`) and cuts EVM nearly in half (`4.23%` → `2.53%`). Pushing to 8× on `REG_70`-only gains only `0.47 dB` because symbol-locked `REG_5F` clamps minor opposite-sign RRC lobes at symbol boundaries.
- On a **signed 8-bit modulator** (TX-27 `REG_51[13,6:0]` if fast-BW confirmed, or sub-symbol sign flips), **8× oversampling (38.4 kWrites/s, 26.0 µs budget)** eliminates the clamping floor and reaches **`-49.08 dB` ACPR / `0.55%` EVM**, matching ideal continuous RRC.

```sh
python3 -m pytest -v tests/test_k6_tx28_fastspi.py
python3 tools/k6_tx28_rrc_shaping.py
```

## Future physical RX-only gate (no TX required)

Run the compiled RX-only `tx28_fastspi_probe` in temporary RAM on a verified `0x4829` idle RX radio under canonical `serial_lock('/dev/ttyUSB0')` to find the lowest `nop` count with **0/64 errors** and confirm exact `REG_5A` and VFO restoration.
