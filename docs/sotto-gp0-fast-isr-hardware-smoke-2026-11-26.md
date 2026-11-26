# Optimized Sotto GP0 ISR: non-transmitting on-radio smoke test

**November 26, 2026.** The opt-in optimization was built and tested on branch `optimize/sotto-gp0-isr-20261126`, commit `dfd2fdeb`. The integrated experimental firmware is **117,628 bytes**, SHA256 `89437801bbb3c6bdce3ed1ee4c232086f0de5340a3abd20ee419b4f2b9d22458`, and contains three features: exact reciprocal GP0 divider, unrolled volatile PDM reads, and previously hardware-measured faster resource-only SPI, retaining the working GPF1 firmware error trace. Its original loadable SOTTO GP0 module binary was not touched.

Before flashing, the full GP0/Sotto regression passed **80 tests, 2 skipped**; the independent memory gate was `READY_FOR_ATTENDED_REVIEW`, with zero failures. A new clean build with ALL opt-ins off reproduced the exact fielded image except eight GNU linker build-ID bytes, preserving the default firmware.

**Physical installation:** The stock K6 bootloader accepted **115/115 KB blocks**, then restarted. No PTT or RF transmission occurred during installation.

**Post-install smoke checks:**
- Cable protocol 5 responded normally.
- Cable-only `0x75` GPF1 report parsed successfully: `valid:false`, IRQ count 0, no errors. This is the required initial condition; it does not indicate Sotto TX works yet.
- The radio restarted and all original catalog modules were present: slot24 SOTTO GP0 9,784 bytes, serial1795730062; slot25 MIC IRQ 3,771 bytes; slot30 SOTTO MIC 9,780 bytes.
- Boot/catalog evidence `recovery/gp0-fast-isr-postflash-status-20261126.txt`; empty readout `recovery/gp0-fast-isr-smoke-empty-gpf1-20261126.json`.
- The Sotto RTL-SDR monitor at `http://127.0.0.1:8024` was idle but receiving samples at 433.500 MHz with -30,500 Hz correction; zero good/bad frames and no error at check time.
- No external SPI writes or module installation/replacement occurred. The known-good field firmware and complete verified 2 MiB SPI backup retain their recorded SHA256 hashes.

**Next required measurement (operator-attended dummy load):** Unplug programming cable; launch CATALOG → SOTTO GP0; hold PTT and speak 3–5 seconds; release; reconnect cable **without switching the radio off**. Immediately use the existing read-only `tools/k6_sotto_gp0_fault.py` to retrieve `GPF1` as the **first** cable request after reconnection, before any status command. Also check the SDR monitor. Compare the ISR peak to old **4,866 ticks**, and look for `gp0_dma_error=0`, `pcm_lost=false`, and evidence of BEGIN/VOICE/END packets. If it still aborts, preserve and inspect exact GPF1 rather than guessing.

**Current device state:** experimental optimized firmware remains installed, full original 9,784-byte SOTTO GP0 module unchanged. **Do not claim a physical fix before a manual TX test.** Recovery firmware is `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin`, SHA256 `8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d`; backup is `recovery/k6-spi-before-sotto-spi-latency-test-20261126.bin`, SHA256 `18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd`.
