# First GPT1 attended GP0 test — definitive queue-full cause, host capture fix

Date: 2026-11-26 (Pacific). Branch: `diagnostic/sotto-gp0-timing-20261126`.

## What actually happened

The user ran the original **CATALOG → SOTTO GP0** module with the newly installed GPF1+GPT1 timing firmware, then reconnected the programming cable without switching the radio off. The first cable operation was the intended dual read in `tools/k6_sotto_gp0_timing.py`.

Both wire queries succeeded, and `decode_timing` progressed through magic, length, valid flags, same module serial, resource/FSK field integrity, and the relationship between GPT1 failure cause and GPF1 DMA status. It then rejected the report with:

```
ValueError: GPT1 queue-full reason without four pending blocks
```

This exception can occur only when `GPT1.gp0_code3_reason == 2` (**PCM queue full at the failing DMA IRQ**), the associated GPF1 report has `gp0_dma_error == 3`, both traces are valid for the same module, and the PCM backlog sampled later at mic_stream(op1) stop is below four blocks. This establishes the previously uncertain distinction between PCM queue exhaustion and a rejected decimator input on the actual K6 hardware.

**Important:** the decoder was too strict, not the firmware. The firmware sets reason 2 in the DMA IRQ when `produced.wrapping_sub(consumed) >= 4`. The firmware's queue backlog field is sampled **later**, after op1 stops DMA. The foreground engine can consume a block between IRQ failure and stop, so a later backlog of 0–3 is entirely possible. The two values represent distinct instants and must not be required to agree.

The original host helper ended the cable session in a `finally` block, which restarted the radio, before saving any JSON because the decoder raised. Consequently **raw timing values were not saved and are unrecoverable from the boot-local record**. Do not invent resource timings, preload durations, final backlog, or IRQ peaks from this attempt. The on-radio error cause itself is grounded in the observed validation failure and firmware branch logic. No conclusion about whether codec flash reads or FSK preload dominates the foreground stall is justified yet.

## Host-only repair

- Remove the incorrect `cause==2 && backlog<4` rejection. Mark backlog explicitly as sampled *at stop*, not at the instant of failure.
- Keep strict validation of unknown failure reason, GPF1/GPT1 serial mismatch, impossible backlog above four, bad offsets, reserved bits, and conflicting DMA error.
- Save raw GPF1 and GPT1 cable bytes as hex **before ending the cable session** whenever `--save` is supplied, including if either decoder raises. Include any successfully decoded fields and an error description. This preserves evidence on future host-side regressions without a firmware reflashing step.
- Add regression tests: reason2 with final stop backlog 0,1,2,3,4; fake cable test asserts the raw data and parsed result are on disk *before* `end()`; malformed report test asserts both raw replies survive before reset.
- No changes to the already-installed K6 firmware, SPI module slot24, ISR code, or RF operation.

## What remains

We now have **physical confirmation that the PCM ring reaches four pending 80-sample blocks while Sotto TX runs**. The unresolved question is the causal foreground stall: slow codec math/table reads, synchronous FSK preload, or both. A future operator-attended dummy-load PTT run with the same installed firmware and fixed dual-read host will provide the numeric resource and preload timings; no additional flash is needed. Use:

```bash
python3 tools/k6_sotto_gp0_timing.py --port /dev/ttyUSB0 --ack-reboot \
  --save /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-foreground-live-repeat-20261126.json
```

Reconnect programming cable after the test without a power cycle and do not issue a separate status command first.

Known-good firmware and the complete verified 2 MiB SPI backup remain preserved. The bench lock is not held.
