# Sotto compiled Cortex-M0 RX decode -> PCM/PWM (offline, 2026-11-24)

**OFFLINE EXPERIMENTAL PROTOTYPE — COMPILES BUT DOES NOT FIT OR RUN.** Extends the two-ARM virtual RF path
(`docs/k6-sotto-live-arm-e2e-20261124.md`) so the second booted K6 ARM
instance runs the actual compiled Cortex-M0 Sotto-1200 fixed-point decoder
(`c/fixed/sotto_fx_dec.c`, `sotto_fx_lr.c`, `fx_math.c`, `fx_tables.c`)
rather than decoding only on the Linux host.

## Scope

- **Module:** `c/k6/mod/sotto_rx31_play_emu.c` (opt-in API31 RAM module, `-Oz`,
  `-fropi`, `-DFX_CB_SPI`, `-DFX_SYNTH_STATE`, `-DFX_MP_ROWGEN`,
  `-DFX_CS512_CONST`).
- **Packet decode:** Reuses `c/k6/sotto_dv_wire.c` (`flux_dv_wire_session_rx66`
  and `flux_dv_wire_rx66`) over API31 `fsk4800_rx`.
- **Codec & codebook:** Reuses `c/fixed/sotto_fx_dec.c` and `sotto_fx_lr.c`
  with `-Dcalloc=sp_calloc -Dfree=sp_free` bump-pool allocation in module
  `scratch`, and `fx_cb_fetch()` reading the `K6CB` codebook via
  `api->resource_read(0, ...)` after CRC-32 header/table verification.
- **PCM & PWM validation:** Each 20-ms 160-sample `int16_t` PCM block is
  hashed (FNV-1a), converted to 160 `uint16_t` PWM compare values (`top=2000`,
  `gain_q8=256`, `emphasis=1`) using the exact `c/k6/sotto_pager.c`
  `sp_pcm_to_pwm()` filter/scaling, and exposed to the Unicorn harness for
  sample-for-sample comparison against:
  1. Independent Linux native `sotto_lr_decode` (`tools/k6_sotto_e2e_mock.py`).
  2. Host-compiled `c/k6/sotto_pager.c` `sp_pcm_to_pwm()`.
- **Corruption & cleanup:** DESIGNED ONLY, NOT EXECUTED for corrupted voice packet CRC (`-11`),
  missing voice packet before `END` (`-10`), and corrupted `K6CB` codebook
  (`-13` before arming `fsk4800_rx`), with `pool_blocks_end == 0`, zeroed
  `pcm`/`pwm` buffers, and `fsk4800_rx(2, 0, 0)` disarm.

## Independent validation — footprint gate failed

The coordinator independently initialized pinned Sotto source and compiled this actual Cortex-M0 module. Initial upstream static symbol collision and Clang r9 global-register issues were corrected, and a minimal local 64-bit ARM runtime added. Clang/LLD then compiled successfully, but the size gate rejected the image: **16,105 bytes including 24-byte envelope** versus **14,976 bytes total module arena**, exceeding it by **1,129 bytes before any scratch**.

With the proposed 764-byte capture header, 3,200-byte decoder pool, and 1,024-byte dedicated stack, about **6.1 KiB must be eliminated from code or loaded outside the arena**. No decoder PCM output, PWM comparison, corruption behavior or CPU deadlines have been verified. The opt-in receiver gate cannot pass until this architectural blocker is addressed. Do not enable this branch in production or mark playback complete.

A later solution needs system SPI executable pages or a budgeted resident/shared decoder and must repeat relocation, stack, timing, buffer-safety and speaker DMA validation. The original 181-passed software milestone remains unaffected.

## Why speaker DMA is not yet validated

This milestone validates **real Cortex-M0 codec decoding and PCM/PWM sample
generation**, not physical or DMA-driven speaker peripheral playback:

1. `c/k6/sotto_pager.c` uses writable static state (`.bss`/`.data`) and is
   designed for resident firmware flash, whereas K6 RAM modules (`module.ld`)
   forbid writable statics (`SIZEOF(.data) == 0`).
2. `k6_api_t` (ABI v1, level 31) does not expose a TIM1 CH4 / DMA audio ring
   callback to RAM modules, and the full-profile API28-31 firmware image has
   only ~700 bytes of flash remaining.
3. Binding `TIM1` PWM + DMA cadence, 20-ms real-time deadline margins on
   96 MHz KD32F328 silicon, and PA/speaker amp gating remain separate
   resident-firmware tasks.

## Independent validation commands

From the repo root with a byte-matched API28-31 `K6_RAM0=1` ELF/image pair and
initialized `sotto` submodule:

```sh
ELF=firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura
IMAGE=firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin

~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_arm_rxplay_gate.py \
  --elf "$ELF" --image "$IMAGE" --sotto-tree . --blocks 8

K6_SIM_SOTTO_LIVE_ARM=1 K6_SIM_SOTTO_LIVE_ELF="$ELF" \
K6_SIM_SOTTO_LIVE_IMAGE="$IMAGE" \
~/.local/share/flux-rf-venv/bin/python -m pytest -q \
  tests/test_k6_sotto_arm_rxplay_gate.py
```

The JSON output reports `module_bytes`, `envelope_bytes`, `scratch_room`,
`scratch_used`, `pool_peak_bytes`, `stack_reserve_bytes`, and
`stack_used_bytes` measured on the emulated Cortex-M0.
