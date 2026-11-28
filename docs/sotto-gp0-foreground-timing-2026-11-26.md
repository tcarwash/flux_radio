# GPF1 + GPT1: on-radio Sotto GP0 foreground timing diagnosis

**Date:** November 26, 2026 (Pacific). **Branch:** `diagnostic/sotto-gp0-timing-20261126`. **Scope:** low-overhead, opt-in firmware telemetry, not a modem implementation change. The original SOTTO GP0 KUL2 code **remains exactly 9,784 bytes**, SHA256 `551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e`, with unchanged 14,976-byte arena and 2,812-byte stack reservation.

## Why

The attended optimized-GP0 firmware test returned `GP0 ENGINE`, GPF1 `gp0_dma_error=3`, PCM lost, 1,746 successful half-buffer IRQs, peak ISR 3,188 TIM17 ticks, zero resource/FSK callback errors. The earlier original profile failed after 1,310 IRQs at 4,866 peak ticks. The ISR is materially faster, but the foreground codec still cannot drain four 80-sample PCM blocks quickly enough.

The current GPF1 cannot distinguish a **full queue** from a **bad decimator input/invalid offset**, nor measure successful resource-read or FSK-preload duration. The updated instrumented test adds only the following measurements, preserving the existing 44-byte GPF1 cable response.

## New opt-in GPT1 telemetry

**Enable:** `K6_SOTTO_GP0_TRACE=1 K6_SOTTO_GP0_TIMING=1`, requires GP0 mic and expanded RAM0. New flag defaults to **0**. The original GPF1 trace remains independently usable.

- **Exact DMA error-3 reason**, recorded **only on the failing ISR path** in a previously unused byte inside the existing HAL State structure (no extra ISR RAM, no normal-ISR-loop work): `1` decimator rejected input, `2` four queued PCM blocks, `3` PCM offset invalid, `4` already lost/other. The previous GP0 error code and original GPF1 semantics are retained.
- **Queue occupancy at stop**, sampled *after* mic_stream(op1) stops DMA: block backlog and partial-block sample offset. No concurrent ISR can modify this readout.
- **Resource-read count, maximum and accumulated durations** during GP0 capture, timed using the already-running **16-bit TIM17 96MHz counter**. This costs two timer reads and a few saturating counter updates per successful resource read, and can affect the actual producer-consumer rate. Individual reads taking a full 65,536-tick TIM17 wrap (~682.7 µs) are **ambiguous**; totals are sums of modulo-16-bit durations, not independently calibrated wall times.
- **FSK preload count and longest synchronous op0 duration**, using the existing firmware `mod_tick()` timebase at **100 µs granularity**; the 20ms FSK settling interval is not counted because it is cooperative after op0 returns.

State added: 16 bytes protected RAM0 (`Gp0Timing`, compiled-size asserted); GPF1 already occupies 24 bytes. All fields saturate; no heap, SPI writes, module ABI changes, TX keying, LCD updates, or new radio commands beyond a **read-only cable query**.

**GPT1**: cable-only command `0x76`, `addr=0`, zero-length request, 40-byte reply: 4-byte `GPT1` magic followed by nine little-endian 32-bit fields `[module_serial, valid, resource_reads, resource_peak_tim17_ticks, resource_total_tim17_ticks_modulo, fsk_preload_peak_100us_ticks, fsk_preloads, gp0_code3_reason, packed_queue_backlog_and_offset]`. The high 16 bits of the final word and all but validity bit 0 are reserved and rejected.

The prior **GPF1 0x75** response is untouched. `tools/k6_sotto_gp0_timing.py` reads **both 0x75 and 0x76 within the same cable session**, validates serial and validity consistency, rejects malformed or contradictory records, writes new-only JSON, and only then ends the cable session, which reboots the radio. The old standalone GPF1 reader still works, but cannot collect GPT1 once it ends the cable session.

## Offline qualification

Built the previously installed optimized feature profile, now with `K6_SOTTO_GP0_TIMING=1`. Image:
`/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-timing-candidate/gp0-timing-offline-only.bin`

- Size: **118,220 bytes**, SHA256 **a4f486a579a7b6632ef63d0fb812676e6fa5e5722cc89c3d3493e76e4238a139**, flash free **4,660 bytes**.
- Compiled RAM0 used **3,808/3,840**, free **32 bytes**; reference GPF1 optimized image used 3,792, free 48 bytes.
- Arena base **0x20001930**, arena size **14,976 bytes**, stack reservation **2,812 bytes**. Exactly +16 bytes RAM0 beyond GPF1; no module arena loss.
- Binary markers: one `GPF1`, one `GPT1`, `KUL2` present; SPI benchmark `SPB1` absent.
- Independent gate `tools/k6_sotto_gp0_fault_gate.py --timing` reported **READY_FOR_ATTENDED_REVIEW**, **zero safety failures**, and validated unchanged pinned field firmware/GP0 module hashes, baseline code identity except linker ID, arena/stack contract and exactly 40 bytes total diagnostic RAM0.
- **92 tests passed, 2 skipped**, including GP0 end-to-end virtual RF decode, timed-report parser, old GPF1 parser, exact failure classification, overlay reversibility, fast ISR bit-for-bit tests, and malformed-report fail-closed tests.
- Fresh **TIMING=0** clean build is **117,628 bytes** and matches the already-installed optimized image byte-for-byte except the eight-byte GNU linker-generated build ID; RAM0 free returns to 48 bytes and the GPT1 signature disappears. This confirms no default-path firmware regression.

The structural gate is not itself permission to flash or evidence of physical timing improvement. The next non-transmitting hardware step is a temporary flash, VFO boot verification, read-only empty dual GPF1/GPT1 responses, and a preserved module catalog. **No live RF/voice transmission was run by the instrumentation build or tests.**

## Attended test procedure after hardware smoke passes

1. Keep K6 on dummy load. Unplug the programming cable. Run **CATALOG → SOTTO GP0**, key PTT for 3–5 seconds while speaking (or release on earlier error). Do **not** power cycle after the test.
2. Reconnect the cable and immediately read **both** boot-local records as the *first* cable operation (a preceding status query ends its cable session and destroys RAM-only evidence):

```bash
python3 tools/k6_sotto_gp0_timing.py --port /dev/ttyUSB0 --ack-reboot \
  --save /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-foreground-timing-live-20261126.json
```

3. Compare exact failure reason and backlog at stop with GPF1 code3, source successful-read peak/aggregate (accounting for timer wrap risk), FSK preload count/time, IRQ count and peak, and SDR packet/voice decode. If GPF1/GPT1 are empty, **do not conclude anything about the radio fault**; investigate trace arming or power-cycle loss.

Known-good rollback firmware (116,796 B, SHA256 `8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d`) and the independently verified 2 MiB SPI backup (SHA256 `18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd`) remain preserved. The diagnostic must never replace slot24 or write SPI flash.
