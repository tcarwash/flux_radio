# Sotto live-serial 1 kHz tone probe

**Development status:** compiled Cortex-M0 module and virtual RF decoder
qualified offline, **not transmitted, installed or flash-tested on a
physical K6**. This is a separate module from the attended `SOTTO TX`.
Do not install into its occupied slot. Source worktree:
`/storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125`.

## Purpose and exact signal

`SOTTO TONE` generates a fixed **1 kHz, 8 kHz-sampled, PCM16 sine**
from a local 8-sample Q15 table. It produces **32 blocks × 80 samples =
320 ms**, at approximately ±50% full scale. The samples enter the
existing `sl_engine_tick` → production slim Sotto encoder → FDV1
BEGIN/VOICE/END scheduler → **API29** 33-word guarded native FSK TX.

**This is NOT a continuous analog 1 kHz FM audio tone on the RF.** The RF
remains Sotto digital FSK at 4800 bit/s, so the SDR should see FSK
bursts. The receiver must decode the Sotto codec to recover the
approximately 1 kHz audio tone.

The generated module deliberately bypasses API30 `mic_stream` and
physical PTT. It can be launched as a short foreground live-serial
module while the 2-pin programming cable remains plugged in, provided
the actual installed firmware contains active API28 typed codec
resources, API29 native FSK, and the existing live-serial/cooperative
runner. **The 500 kbaud PC RX-only serial improvement is not used.**
Normal 115,200-baud host control carries only short launch/result frames.

## Offline evidence

`tests/test_k6_sotto_tone.py` executes the **compiled Cortex-M0**
`SOTTO TONE` module in the existing virtual FSK link, using real native
Sotto encoding, an independent RF IQ recovery model and separately
compiled wire decoder. On 2026-11-25:

- Module size **9,956 bytes**, entry **16**, API level 29.
- Returned **8 codec frames**, matching byte-for-byte the independently
  encoded 320 ms tone PCM reference.
- Transmitted and independently received **three 66-byte packets**
  (FDV1 BEGIN, Sotto VOICE, FDV1 END); full session, zero invalid
  packets, zero FIFO underflows/overflows, 99 RX FIFO words.
- The default build without `SOTTO_TONE_TX_ARMED=1` is **inert**.
  The explicitly generated build is armed but never TX on **install**:
  only a deliberate foreground module RUN requests a short TX lease.
- The parent API29 Rust register/FIFO fault harness still covers
  premature completion, missing guard, and aborted cleanup.
- Independent physical on-air CRC success **has not been demonstrated**
  for this tone module.

The production attended `sotto_live_tx.c`, modem wire format,
encoder tables, and firmware API29 callback remain unchanged.

## Build without opening a serial port

From the worktree:

```sh
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_tone_live.py build \
    --out /storage5/dev/flux-sotto-tone-build-20261125
```

This creates `module.bin` and `manifest.json`. To build a complete
optional catalog package with the Sotto deck (still no radio IO):

```sh
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_module.py \
    --host-resources --tone-tx \
    --out /storage5/dev/flux-sotto-tone-catalog-20261125
```

The generator must have the **pinned** Sotto git submodule
`598f1bce87c22ebada2b9d0d1fcfd89107b33a09` and the derived
`c/fixed/sotto_fx_slim.c` from that matching source. The tone module
requires an expanded arena with at least its 9,956-byte code plus
`sizeof(sl_engine_t)+slim_enc_bytes()` scratch; the ordinary small
arena may refuse with `SOTTO TONE no RAM`.

## Physical procedure — NOT PERFORMED

Use the normal shared bench policy: K6 on its dummy load, SDR sampling
the air-coupled signal, no other agents holding the radio. The helper
will reserve/release `k6_bench.py` bench owner `sotto-tone-live`,
lock the serial port, require a live monitor, check active VFO
**433.500 MHz** and idle RX, and collect diagnostics after returning.
It neither retunes nor configures the SDR and never retries RF after an
uncertain completion.

Before installing anything, choose an **empty** eligible packed-library
slot; the following `24` is **an example**, not confirmed free. Read
the radio's actual API29 compatibility and codebook/deck provisioning.
On a cable-only firmware, an explicitly authorized installation is:

```sh
python3 tools/k6_sotto_tone_live.py install \
    --port /dev/ttyUSB0 --slot 24 \
    --cable-only --confirm-install
```

On a keyed firmware, use `--key-file PATH` instead of
`--cable-only`. The controller refuses occupied slots by default;
replacement needs an explicit `--replace`. **Never overwrite the
existing SOTTO TX slot automatically.**

Start Sotto Monitor separately so `/api/status?after=0` reports
`state=receiving`, `freq=433.5`, and the best calibrated offset
(previously approximately −30.8 kHz). Then, only with the dummy load
verified, explicitly launch a single bounded tone:

```sh
python3 tools/k6_sotto_tone_live.py run \
    --port /dev/ttyUSB0 --slot 24 --dummy-load-confirmed \
    --capture-dir /storage5/dev/sotto-monitor/evidence/tone-one
```

The capture directory must not already exist. It receives
`monitor-before.json`, `diagnostics.json`, `rejected-iq.npz`,
`monitor-after.json`, `radio-result.json` and `summary.json` where
available, or explicit error files on capture failure. The summary
reports changes in accepted/rejected CRC counts without claiming an RF
pass merely because the module returned normally.

The controller uses live-serial `run_module_cooperative()` with a
20-second host result timeout and a higher-API acknowledgment. Module
TX lease is limited to 9 s, local work to 8.5 s, and ordinary packet
TX retains the separate API29 700 ms guard. Local keypad press and a
token-bound cooperative cancel both abort/dekey. **A 20-second host
timeout is NOT proof the radio is safe to retransmit**; inspect
radio state before any further action.

Success requires *both* normal module result (eight frames) and
independently CRC-valid full FDV1 session at the SDR, not just a TX LED
or RF burst. Compare valid/invalid packet counts and the 66-byte tail
with prior captured samples to isolate actual RFIC TX-done timing.

## Regression commands

```sh
~/.local/share/flux-rf-venv/bin/python -m pytest -q \
  tests/test_k6_sotto_tone.py \
  tests/test_k6_sotto_tone_live.py \
  tests/test_k6_sotto_api29_native_host.py \
  tests/test_k6_sotto_tail_timing.py \
  tests/test_k6_sotto_live_packet_parity.py \
  tests/test_k6_sotto_api29_overlay.py
```
