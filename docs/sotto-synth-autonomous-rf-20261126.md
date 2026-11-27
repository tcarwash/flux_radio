# Sotto synthetic PCM on the actual Cortex-M0 codec / guarded hardware FSK path

2026-11-26 (Pacific), experimental branch. **No physical RF or radio modification yet.** The earlier GP0 physical trials independently proved that the real microphone's DMA PCM queue reaches 4/4 and returns `GP0 ENGINE` around 200ms with resource/FSK errors both zero.

## Architecture

`c/k6/mod/sotto_synth_tx.c` is a new API30 standalone module, named `SOTTO SYNTH`, that substitutes a deterministic 8kHz 80-sample/10ms PCM producer for the physical microphone stage. All downstream parts are the **real compiled on-radio ARM module**: `slim_encode10`, the pinned K6CB/K6SC read-only SPI resource callbacks, FDV1 packet framing, API29 guarded FSK FIFO, and the TX watchdog. It does not call `mic_stream`, inject PTT through the UART, spoof FIFO completion, or depend on the headset microphone.

Four compile-time audio profiles: zero silence; 320+870Hz two-tone triangle mix; swept-frequency chirp; and short alternating tone/silence bursts. Each run produces **104 blocks / 8320 samples / 1.04s**, releases the virtual mic, flushes the encoder and FDV1 END record, then dekeys. All four profiles use zero dynamic allocation and require 4080 bytes scratch. The module is inert unless explicitly compiled with `SOTTO_SYNTH_TX_ARMED=1`. Armed variants lock themselves to either approved *bench* frequency 433.500 or 443.500MHz, refuse other VFOs, use the radio's **low-power** guarded TX path, and cap the TX lease at 9 seconds (TX watchdog remains independent). A local key, other module failure, full PCM queue, codec/SPI error or FSK error triggers a dekey. The built-in UART telemetry on success is `SYN:M:XXXXXXXX` = maximum individual encoder call in 100us ticks, `SYN:Q:XXXXXXXX` = maximum PCM queue backlog, both hexadecimal. A result of 26 indicates 26 encoded codec frames. The serial cable may remain attached because there is no analog microphone read.

### Virtual RF hardware tests completed

`tools/k6_sotto_synth_virtual.py` runs the **actual compiled ARM module** through a model of the K6 radio's UART-independent APIs, RFIC SPI, FIFO and 8kHz timebase, over a virtual RF channel with a compiled RX and independent native speech-codec oracle. The source PCM provided to the oracle is calculated separately on the host, not loaded into the module. Run from repo root:

```sh
python3 tools/k6_sotto_synth_virtual.py --out /tmp/sotto-synth-e2e
```

All **16/16** combinations (4 profiles × original/optimized encoder × CPI 1.7/2.5) completed with exactly 26 codec frames, all 6 decoded virtual RF packets valid, no overflow, no stuck transmitter, bit-exact codec output. The optimized module's peak callback was shorter in every pair: 9.1ms vs 10.3ms on multitone at CPI 1.7; for burst audio at CPI 2.5, 13.5ms vs 15.3ms. See [raw per-run report](evidence/sotto-synth-actual-arm-virtual-rf-matrix-20261126.json). This test does **not** model the physical microphone DMA IRQ load and does not establish that the original GP0 hardware microphone is usable yet. The separate 16-run real-GP0 virtual mic matrix records contention overflows in the original variant.

### Live serial host profile, memory qualification

Current GP0 microphone firmware has `K6_LIVE_SERIAL=0` to fit in protected RAM. The dedicated *temporary* synthetic-only host instead keeps API28 resources, API29 FSK (including cooperative preload), the 14,976B expanded arena, UART live RUN with opt-in API30 module launch, and the TXDOG, **but turns the real GP0 microphone service and GPF1/GPT1 diagnostics off**. This is an experiment configuration, not a production replacement. `K6_SOTTO_SYNTH_HOST=1` gates moving module context and live cancellation state into the protected RAM0 region. It also permits cooperative FSK without requiring the absent microphone service; normal GP0 builds retain their previous guard.

An isolated build (not yet flashed) passed both the flash limit and mandatory memory audit:

| Resource | Build result | Limit |
| --- | ---: | ---: |
| Binary image | **117,720 bytes** | 122,880 bytes |
| Protected RAM0 | **3,668 bytes** | 3,840 bytes |
| RAM0 free | **172 bytes** | positive |
| Main-stack reservation | **2,796 bytes** | 2,780 minimum |
| Module arena | **14,976 bytes** | 14,976 expected |
| Optimized synthetic module, multitone | **9,759 bytes** | 9,784 maximum for 48B scratch margin |

The new synthetic-only `apply-sotto-synth-host-ram0.py` refuses to change protected-state placement unless LIVE_SERIAL=1, RAM0=1 and MIC=0. The regular GP0 MIC+fault tracing build and rollback firmware are unchanged. The 16-byte excess over minimum stack reservation is narrow; **require independent ARM boot regression, full radio catalog identity preflight and verified rollback before hardware deployment**.

## Physical test sequence after final qualification

Preserve the verified original full 2MiB K6 SPI backup and the known bootable `c69a8742…a83ec` GP0 diagnostic MCU image. Hold the bench lock and single PL2303 port lock. Flash the separately qualified live synthetic MCU image using the stock K6 bootloader; verify VFO and library contents. Query live status and features (including `run` and `coop_cancel`); fail closed if live control is absent. Select a **verified vacant** module slot, sign/install the exact candidate with replay-safe serial and readback, never overwrite the original SOTTO GP0 slot24 or MIC diagnostics slots25/30. With the operator's previously verified dummy load still connected, and VFO frequency equal to the compiled 70cm guard, run a single ≤9sec TX at low power using `run_module_cooperative`, then inspect completion, SYN telemetry, and RX restoration. No blind retry on a missing ACK or unknown run. Capture SDR IQ independently if available to verify actual radiated modulation. After testing, verify/deinstall exactly the added slot and restore the original GP0 MCU image, leaving original SPI library untouched.

Permission to transmit is not evidence of modem success; **an acknowledged FSK FIFO completion is not an SDR-decoded RF packet**. Physical bench results must be recorded separately, with frequency, payload, output-level, abort, dekey, stack/RAM and a note about station identification under the operator's call.
