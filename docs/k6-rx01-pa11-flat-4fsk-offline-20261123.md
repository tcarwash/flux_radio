# RX-01 — Full-Sensitivity 4-FSK / DV Receive via PA11 Delta-Sigma & Flat Discriminator: Offline Qualification

**Date:** 2026-11-23  
**Queue issue:** [#24](https://github.com/tcarwash/flux_radio/issues/24) (subordinate to `#9` P0 / `#10` P1 / `#12` P2; coordinated via `#11`)  
**Branch:** `research/rx01-pa11-flat-4fsk-offline-20261123`  
**Scope:** Strictly **OFFLINE** Cortex-M0 build, Unicorn PA11 delta-sigma behavioral verification, and exact K6 RC-ladder + 4800-baud 4-FSK receiver physics simulation. **No radio, serial port, SDR, SPI flash, or RF TX performed.**

## Motivation

Flux's GP0 1-bit IF receive path hits a hard-limited noise floor near **`-79 dBm`**, whereas the FD6818B (`0x4829`) internal FM discriminator delivers full receiver sensitivity (`~ -120 dBm` class) to `AF_OUT` (pin 9). Because `AF_OUT` reaches no MCU ADC pin, the K6's voice-prompt pin **`PA11`** can be run as a 1-pin 2nd-order CIC delta-sigma converter (`pa11adc.h`, measured `~17.8 kS/s` on `0x4819` with `0.00017 FS` converter noise floor vs `0.051 FS` receiver hiss).

However, `AF_OUT` couples to `PA11` through a two-stage RC ladder (`2.2k + 1 uF + 22k` into `22 nF`, then `4.7k` into `3.9 nF` and `4.7k` to `PA11`). Exact nodal-admittance analysis reveals an important circuit nuance: because `PA11`'s delta-sigma feedback actively holds the `PA11` pin near `V_th` (virtual AC ground through `4.7k + 4.7k = 9.4 kΩ`), the `22 nF` node sees the Thevenin parallel resistance `24.2 kΩ || 9.4 kΩ = 6.77 kΩ`, placing the **exact coupled dominant pole at `1029 Hz`** (3× wider than the naive unloaded `22k / 22 nF = 329 Hz` estimate, with `-8.23 dB` attenuation at `2.4 kHz`). Even so, that `1029 Hz` pole still causes a `20.2%` SER ISI floor on unequalized 4800-baud 4-FSK — and bypassing the FM audio filters (`REG_2B |= 0x0700`) plus applying a **single-tap Cortex-M0 fixed-point inverse-RC equalizer** (`y[n] = x[n] - (178 / 256) * x[n-1]`, ~5 CPU cycles/sample at `17.8 kS/s`) cancels it cleanly!

## Components

1. **RX-only PA11 flat-discriminator probe (`c/k6/mod/rx01_pa11_flat_probe.c`):**
   - Inert by default (`RX01_BENCH_ENABLE=0` → `-9`, `<256 B`).
   - Strictly **API level 2 (RX-only)**: refuses non-`0x4829` (`-6`), TX/PA active (`-2`), or FSK active (`-8`).
   - Snapshots and unconditionally restores `REG_2B`, `REG_47`, `GPIOA` (`MODER`/`OSPEEDR`/`PUPDR`/`ODR`), and `GPIOB` `PB2` (speaker amp muted during pulsing) on normal exit and key / `PA15` power-switch abort (`-4`).
   - Runs three 128-sample CIC bursts (`stock REG_2B`, `flat REG_2B | 0x0700`, and `flat REG_2B | 0x0700 + REG_47 AF select 4`) and reports peak-to-peak, AC MAD, and first-difference high-band energy (`hf`) with zero `__aeabi_uidiv`.

2. **Exact PA11 RC-ladder & 4800-baud 4-FSK DSP simulator (`tools/k6_rx01_pa11_4fsk_sim.py`):**
   - Solves exact nodal admittance for `2.2k + 1 uF + 22k / 22 nF + 4.7k / 3.9 nF + 4.7k` plus 2nd-order CIC `sinc^2` droop at `17.8 kS/s` and measured PA11 converter floor (`0.00017 FS`).
   - Compares 4800-baud 4-FSK SER across RF input levels for `raw_pa11`, `stock_reg2b_eq1`, `eq1_fixed_pa11`, and `ideal_disc` against GP0's `-79 dBm` floor.

## Offline verification results

Verified via `python3 -m pytest -v tests/test_k6_rx01_pa11_4fsk.py` (**10/10 passed**) and `python3 tools/k6_rx01_pa11_4fsk_sim.py`:

- **Cortex-M0 PIC binary sizes:** inert default (`RX01_BENCH_ENABLE=0`) = **6 bytes** (returns `-9`, zero writes); bench-enabled (`RX01_BENCH_ENABLE=1`) = **1066 bytes** (zero `__aeabi_uidiv`).
- **Unicorn behavioral verification:** all 6 RX-only safety refusals (`-6`, `-2`, `-8`), full 3-burst PA11 delta-sigma capture (verifying `REG_2B | 0x0700` and `REG_47` select 4 writes, strictly higher `hf` first-difference energy on flat bursts, `PB2` speaker-amp mute/restore, and bit-exact `REG_2B`/`REG_47`/`GPIOA` restoration), plus mid-run key-press and `PA15` power-switch abort restoration (`-4`) passed.
- **Exact PA11 RC-ladder response (`2.2k + 1 uF + 22k / 22 nF + 4.7k / 3.9 nF + 4.7k`):**
  - Coupled dominant pole: **`1029.0 Hz`** → recommended Cortex-M0 fixed-point EQ coefficient **`alpha_q8 = 178`** (`178/256 ≈ 0.695`).
  - Relative attenuation: `-0.38 dB` at `300 Hz`, `-3.80 dB` at `1200 Hz`, `-8.23 dB` at `2400 Hz`, `-11.45 dB` at `3600 Hz` (`7.85 dB` drop from `300 Hz` to `2400 Hz`).
- **4800-baud 4-FSK (`±1944 Hz` outer, RRC `alpha = 0.5`, 2000 symbols, `12.5 kHz` IF BW, `6 dB` NF offline model) at `17.8 kS/s` PA11 sampling:**

| Receiver path | High-SNR SER (`-90 dBm`) | `1% SER` sensitivity | Margin vs GP0 (`-79 dBm` floor) |
| --- | ---: | ---: | ---: |
| `stock_reg2b_eq1` (stock `REG_2B` + PA11 + 1-zero EQ) | `51.22%` (undecodable) | none | — |
| `raw_pa11` (flat `REG_2B`, unequalized PA11 RC ladder) | `20.20%` (ISI floor) | none | — |
| `eq1_fixed_pa11` (flat `REG_2B` + PA11 + 1-zero `alpha_q8=178`) | **`0.000%`** | **`-116.0 dBm`** | **`+37.0 dB`** |
| `ideal_disc` (ideal flat FM discriminator reference) | `0.000%` | `-118.0 dBm` | `+39.0 dB` |

**Key design takeaway:** **Both** `REG_2B |= 0x0700` (bypassing FM audio HPF/de-emphasis/LPF) **and** the 1-zero Cortex-M0 inverse-RC equalizer (`alpha_q8 = 178`) are strictly necessary — omitting either leaves an undecodable `20%–51%` SER floor even at `-90 dBm`, whereas combining both lands within **`2.0 dB`** of an ideal flat discriminator (`-116 dBm` vs `-118 dBm`, `+37 dB` better than GP0's `-79 dBm` floor in the offline RF model).

```sh
python3 -m pytest -v tests/test_k6_rx01_pa11_4fsk.py
python3 tools/k6_rx01_pa11_4fsk_sim.py
```

## Future physical RX-only gate (no TX required)

Run `rx01_pa11_flat_probe` in temporary RAM on a verified `0x4829` idle RX radio under canonical `serial_lock('/dev/ttyUSB0')` (e.g. tuned to open squelch hiss or a weak local 4-FSK reference carrier) to confirm on physical silicon whether `0x4829` exposes wideband discriminator audio via `REG_2B | 0x0700` (burst 1) and/or `REG_47` select 4 (burst 2), and confirm exact `REG_2B`, `REG_47`, `PA11`, and `PB2` restoration.
