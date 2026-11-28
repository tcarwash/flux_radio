# K6 Sotto GP0 SPI benchmark: fielded-profile reconstruction

**2026-11-26 — offline only; STOP_DO_NOT_FLASH.** No serial connection, module write, firmware flash, PTT, or RF transmission was attempted. The K6 remains on its restored, bootable API30 KUL2 firmware. Bench is free.

## Pinned reference

Original fielded firmware: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin

- SHA256: 8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d
- Flash: 116,796 / 122,880 B, leaving 6,084 B.
- Arena: 14,976 B; RAM0 used: 3,768 B, free: 72 B; stack reservation: 2,812 B.
- The exact original full build command was not retained in the release manifest. Earlier records establish K6_USER_STORE=1, but not the entire K6_* environment.

## Independent clean builds

A new, detached worktree at /storage5/dev/flux-sotto-spi-clean-build-20261126 used Flux commit 8e6491ca, pinned Sotto submodule 598f1bce87c22ebada2b9d0d1fcfd89107b33a09, and pinned Aura c3aeb17aec0fd09dccf931b52f6c7d5dbf72e7ac. Each variant used K6_AURA_RESET_GENERATED=1 and the normal complete build-fd6818b.sh build, with independent output and logs preserved under the recovery directory.

Common flags: K6_SOTTO_RESOURCES=1, K6_SOTTO_ASYNC_FSK=1, K6_SOTTO_MIC=1, K6_USER_STORE=1, K6_RAM0=1, K6_LEAN_TXDOG=1, K6_MODE_REGISTRY=1, K6_PACKET_MAILBOX=1, K6_SOTTO_SERVICE=0, K6_LIVE_SERIAL=0, K6_REMOTE_UI=0, K6_NATIVE_FM=0, K6_FSK4800_RX=0.

| Additional flags beyond common | Size | RAM0 used |
|---|---:|---:|
| None; reduced Scan/Data | 102,016 B | 3,748 B |
| SCAN_SERVICE | 113,256 B | 3,752 B |
| SCAN_SERVICE + FSK_RX_SERVICE | 117,072 B | 3,752 B |
| Prior + FONT_SUBSET + RASTER_BOOT | 114,820 B | 3,752 B |
| Prior + SERIAL_TRANSPORT | 115,408 B | 3,768 B |
| Prior + MEMORY_BANK | 116,064 B | 3,768 B |
| Prior + HEALTH_TELEMETRY + EL_POWER | **116,480 B** | **3,768 B** |
| Prior + CONFIG_FSK | 117,784 B | 3,768 B |
| Prior + SERIAL_LAB | 122,412 B | 3,768 B; stack differs |
| Closest 116,480 B profile, FCC TX profile instead of CE | 116,480 B | 3,768 B; hash differs |

Closest reconstructed baseline, under recovery/spi-field-el-health-baseline/:
- Size: **116,480 B** (316 B less than fielded).
- SHA256: e3240de066e8ece8904858a7efb0042a65227b7b31f8784c4ee48fa0978c8454
- All recorded memory contract fields match the fielded image exactly.
- Code sections differ: baseline .text 103,360 B, .rodata 12,184 B; fielded .text 103,704 B, .rodata 12,156 B.
- **Not a byte-identical build.** Matching memory values do not prove runtime feature equivalence.

## Instrumented field-like candidate

The same closest profile plus K6_SOTTO_FAST_SPI=1 and K6_SOTTO_SPI_BENCH=1 built cleanly:

- 117,376 B, 5,504 B flash headroom.
- RAM0 3,768 used / 72 free, arena 14,976, stack 2,812.
- SHA256: f18ba01207478ad9bb75378bbefbfbadca860134b88b93291e3efc0e8483be37.
- Path: recovery/spi-fieldlike-instrumented/spi-fieldlike-offline-only.bin
- 0x73/SPB1 cable-only benchmark measures baseline vs streaming 20- and 128-byte SPI resource reads, verifies digests and byte-for-byte spot reads, never transmits or writes SPI.
- Previously, the base instrumented probe, virtual RF/PCM, decoder and overlay suite passed 58 tests.
- **Not flashed or hardware-timed. Not yet a drop-in radio image.**

## Automated fail-closed gate

tools/k6_sotto_spi_profile_gate.py compares the uninstrumented clean baseline byte-for-byte against the exact pinned reference, checks KUL2, exclusive SPB1 probe signature, flash size, and equality of RAM/stack contracts. It creates a JSON report but contains no flash or radio control actions. A passing gate only labels a build READY_FOR_ATTENDED_REVIEW, not permission to flash.

Actual report: recovery/spi-fieldlike-profile-gate-20261126.json

- status: **STOP_DO_NOT_FLASH**
- ram0_contract_matches: true
- baseline_matches_reference: false
- safety_failures: ['uninstrumented clean rebuild differs from known-good firmware']

All 14 focused gate and overlay tests passed, including five new tests for fail-closed cases.

## Next step

Recover the exact original build flags/source generation behavior so a new uninstrumented build reproduces the known-good 116,796-byte binary byte-for-byte, pass the profile gate with the instrumented image, and perform a recovery/stack audit. Only then consider an attended non-transmitting firmware swap and single read-only SPI benchmark. Do not repeat the previous tight-scratch diagnostic-module launches. Current radio and SPI library backups remain untouched.
