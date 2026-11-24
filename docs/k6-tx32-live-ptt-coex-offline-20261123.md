# TX-32 — Live-PTT Coexistence Gate (`PA11` Mic Capture + Native-FSK RF TX): Offline Qualification & Live Bench Protocol

**Date:** 2026-11-23  
**Branch:** `research/tx32-live-ptt-coex-offline-20261123`  
**Scope (this commit):** Strictly **OFFLINE** Cortex-M0 PIC build, behavioral Unicorn verification, and blind RTL-SDR IQ + K6 Goertzel coexistence analyzer across all 6 decision-matrix outcomes. **No radio, serial port, SDR, SPI flash, or RF TX performed in this qualification.**

## Goal

Determine in **one bounded ~2.5-second dummy-load run** whether the Baofeng K6 (`0x4829` / FD6818B) supports **real-time live-PTT digital voice** (simultaneous `MIC_ADC → AF_OUT (select 4) → PA11` capture **while** PA is ON and native 4800-bps guarded L64 FSK TX is active) without hardware modifications.

## Six-Slot Protocol (`c/k6/mod/tx32_live_ptt_coex.c`)

With a steady **1300 Hz** acoustic tone at the K6's unplugged microphone, a **50 Ω dummy load** attached, and an air-coupled **RTL-SDR** recording at `433.750 MHz` (`1.024 MS/s`, target TX `433.500 MHz`):

| Slot | PA | `REG_30` | Native FSK TX (4800 bps) | Mute / Filter Lever | What It Isolates |
| --- | --- | --- | --- | --- | --- |
| **S0** | OFF | `mic+af` | OFF | stock | PA-OFF baseline `1300 Hz` contrast on `PA11` (`>12 dB`). |
| **S1** | ON | `0xC3FE` | OFF | stock | PA-ON RF self-interference on `PA11` + baseline `1300 Hz` FM dev on SDR. |
| **S2** | ON | `0xC3FE` | OFF | `REG_50 \|= 0x8000` | Pure-carrier check of whether `REG_50[15]` mutes mic-to-FM independently of `PA11`. |
| **S3** | ON | `0xC3FE` | **ON** (2× guarded 33w L64) | stock | Primary live-PTT test: does native FSK auto-isolate mic from FM while `PA11` stays clean? |
| **S4** | ON | `0xC3FE` | **ON** (2× guarded 33w L64) | `REG_50 \|= 0x8000` | Live-PTT + `REG_50[15]` mute. |
| **S5** | ON | `0xC3FE` | **ON** (2× guarded 33w L64) | `REG_74=44=45=0` | Fallback: does zeroing the TX audio filter chain mute mic-to-FM independently of `AF_OUT` sel 4? |

## Safety Gates
- **Inert by default:** `TX32_BENCH_ENABLE=0` compiles to a tiny stub returning `-9` with zero writes.
- **Explicit compile-time acknowledgments required** to arm: `TX32_BENCH_ENABLE=1`, `TX32_DUMMY_LOAD_ACK=1`, `TX32_WATCHDOG_ACK=1`.
- **Runtime gates:** `0x4829`-only (`REG_00`), `433.500 MHz` VFO lock, channel-clear RSSI `≤ -90 dBm`, bounded `tx_on(8000 ms)`, keypad + `PA15` power-switch abort checked between slots, and unconditional restoration of all 16 BK4829 registers, `REG_30`, `GPIOA`, `GPIOB` `PB2`, and `tx_off()`.

## Offline Verification Results

Verified via `python3 -m pytest -v tests/test_k6_tx32_coex.py` (**15/15 passed**):
- **Cortex-M0 PIC binary sizes:** inert default (`TX32_BENCH_ENABLE=0`) = **6 bytes** (returns `-9`, zero writes); armed bench build (`TX32_BENCH_ENABLE=1`, `TX32_DUMMY_LOAD_ACK=1`, `TX32_WATCHDOG_ACK=1`) = **3044 bytes** (zero `__aeabi_uidiv`).
- **Unicorn behavioral verification:** all 6 safety refusals (`-6`, `-2`, `-8`, `-7`), full 6-slot execution (confirming `REG_30 = 0xC3FE` bus write + restore to `0xC3FA`, `REG_50[15]=1` mute in S2/S4, `REG_74/44/45=0` in S5, `6 × 33 = 198` guarded FSK FIFO writes, `1300 Hz` PA11 Goertzel selectivity, and bit-exact restoration of all 17 BK4829 registers + `GPIOA` + `PB2` + `tx_off()`), and mid-run `PA15` power-switch abort restoration (`-4`) all passed.
- **Blind RTL-SDR IQ + K6 Goertzel analyzer:** all 6 decision-matrix outcomes (`LIVE_PTT_DIRECT`, `LIVE_PTT_REG50_MUTE`, `LIVE_PTT_TXFILT_MUTE`, `PA_INTERFERENCE_ON_PA11`, `FSK_HIJACKS_AFOUT`, `MIC_UNMUTABLE_ON_FM`) including displaced offset-2 (`0x00F8`) guarded L64 frames decoded and classified accurately.

```sh
python3 -m pytest -v tests/test_k6_tx32_coex.py
python3 tools/k6_tx32_coex_analyze.py sim --scenario direct
```

## Live Attended Bench Procedure (When Operator Authorizes)

1. Confirm 50 Ω dummy load on K6 (`0x4829`), tune K6 to `433.500 MHz`, and stage a steady `1300 Hz` acoustic tone source near the K6 mic.
2. Start the receive-only RTL-SDR capture on the host (never touches serial/radio):
   ```sh
   python3 tools/k6_tx32_coex_analyze.py capture --out /tmp/tx32_live --seconds 12 --center-hz 433750000 --rf-hz 433500000 --gain-tenth-db 0
   ```
3. On the K6, follow the screen prompt (`PULL CABLE + 1300HZ / PRESS ANY KEY`), let the ~2.5 s 6-slot sequence finish, then `PLUG CABLE IN / PRESS ANY KEY` to dump `tx32 s=0..5 ...` lines.
4. Combine the captured IQ + K6 serial lines for the verdict:
   ```sh
   python3 tools/k6_tx32_coex_analyze.py analyze --iq /tmp/tx32_live.npy --k6-log /tmp/tx32_k6.txt
   ```
