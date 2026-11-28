# GPF1 + GPT1 on-radio non-transmitting qualification

November 26, 2026, Pacific time. **Source branch** `diagnostic/sotto-gp0-timing-20261126`.

The new optional foreground-timing diagnostic was compiled and passed its fail-closed offline gate **READY_FOR_ATTENDED_REVIEW**, zero safety failures, with **92 passing tests and 2 skipped**. A timing-OFF full build of the installed optimized GPF1 firmware was byte-identical to the installed optimized reference except for its 8-byte linker-generated GNU build ID.

**Candidate image flashed:** `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-timing-candidate/gp0-timing-offline-only.bin`, size **118,220 bytes**, SHA256 `a4f486a579a7b6632ef63d0fb812676e6fa5e5722cc89c3d3493e76e4238a139`. The normal bootloader accepted all **116 of 116** 1 KiB packages and restarted into Flux cable protocol 5. No PTT, voice, module execution or RF transmission occurred during this smoke test.

The diagnostic uses 40 total RAM0 bytes, including the original 24-byte GPF1 state and a new 16-byte GPT1 timing record. Audited RAM0 usage **3,808 / 3,840**, free **32 bytes**, with unchanged arena address `0x20001930`, arena size **14,976 bytes**, stack reserve **2,812 bytes**. Module slot24 SOTTO GP0 code remains **9,784 bytes**; slot25 MIC IRQ **3,771 bytes**; slot30 SOTTO MIC **9,780 bytes**. No SPI module writes/erases.

As the first cable action after flashing, the host opened one cable session, read the original command `0x75` GPF1 and the new `0x76` GPT1, then ended the session. Both correctly decoded with `valid:false`, module serial zero, no GP0 capture, no resource reads, no FSK preload and no PCM queue occupancy—expected because no module was run. Saved to `recovery/gp0-timing-hardware-empty-20261126.json`.

A subsequent cable status query confirmed protocol5 and all three expected module slots (saved `recovery/gp0-timing-postflash-status-20261126.txt`). The reference field firmware, optimized reference, and independently calibrated 2MiB SPI backup remain intact. Bench reservation `gp0-timing-smoke` must be explicitly released when done.

**Next physical test:** on dummy load, unplug programming cable, launch CATALOG → SOTTO GP0, hold PTT for about 3–5 s while speaking (or release when GP0 ENGINE appears), reconnect programming cable **without powering radio off**, and immediately read BOTH GPF1 and GPT1 in ONE cable session, before any prior status/serial operation:

```bash
python3 tools/k6_sotto_gp0_timing.py --port /dev/ttyUSB0 --ack-reboot --save /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-foreground-live-20261126.json
```

The new report disambiguates error 3 (invalid decimator input, full PCM ring, bad ring offset, already lost/other), records 0–4 queued blocks and partial-block samples at stop, resource-read count/max/total modulo-16-bit TIM17 ticks, and count/max-duration of synchronous FSK op0. A 16-bit TIM17 wrap (~683us) can make a single resource duration ambiguous; be cautious interpreting peak/mean, and compare with the GPF1 IRQ count/peak and SDR evidence. GPT1 timings add some observer overhead and are not a guarantee of Sotto TX success.

**Recovery:** known-good firmware `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin` (SHA256 `8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d`), complete independently verified SPI backup `recovery/k6-spi-before-sotto-spi-latency-test-20261126.bin` (SHA256 `18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd`). Do not erase SPI or replace signed modules for firmware-only rollback.
