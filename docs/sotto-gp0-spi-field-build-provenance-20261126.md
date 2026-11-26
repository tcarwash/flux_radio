# GP0 Sotto SPI timing: recovered field firmware build provenance

**November 26, 2026 — verified offline, pending hardware measurement.**

The previously documented 316-byte mismatch was resolved by recovering the actual original Cargo rustc feature fingerprint from the untouched historical build cache in the Flux workspace. The corresponding fingerprint mtime is **2026-11-26 21:17:11.426911 UTC**, only 0.095 seconds before the known-good 116,796-byte firmware artifact mtime. This anchors the flags to the actual fielded build, rather than an inferred feature pack.

## Recovered field profile

The Rust Cargo features were: blowfang-host, fd6818b, lean, spilib, txdog-quiet (txdog is transitive).

On: K6_MODE_REGISTRY, K6_PACKET_MAILBOX, K6_SOTTO_RESOURCES, K6_SOTTO_ASYNC_FSK, K6_SOTTO_MIC, K6_USER_STORE, K6_RAM0=1, K6_CONFIG_FSK, K6_LEAN_TXDOG, K6_FONT_SUBSET, K6_RASTER_BOOT, K6_SERIAL_TRANSPORT.

Off: K6_MEMORY_BANK, K6_EL_POWER, K6_HEALTH_TELEMETRY, K6_NATIVE_FM, K6_LIVE_SERIAL, K6_REMOTE_UI, K6_SOTTO_SERVICE, K6_FSK4800_RX, K6_SERIAL_LAB, K6_OTA_SERVICE. The Scan service and lower-level FSK RX service are active.

The exact original Rust --cfg list was recovered from:
firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/build/aura/74cdf36134f33756/fingerprint/bin-aura.json

The pinned, detached clean checkout is /storage5/dev/flux-sotto-spi-clean-build-20261126 at commit 8e6491ca. It has the pinned Sotto submodule revision 598f1bce and Aura revision c3aeb17.

## Bitwise reproduction

The clean *uninstrumented* rebuild is **116,796 bytes**, exactly the fielded firmware size, and has exactly the same RAM0 and module arena audit.

- Fielded SHA256: 8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d
- Clean rebuild SHA256: d36a7b93ee43122625e9a5c8ada78452807ed10c920762af7a2d08411942f03e
- A raw byte comparison found exactly **eight differing bytes** in the 116,796-byte image, at offsets 0x1C558 through 0x1C55F. All other bytes are identical.
- These eight bytes are the value of the 8-byte ELF GNU --build-id=fast note, inside the 24-byte .k6_id output section. The note header is identical in both builds (04 00 00 00 08 00 00 00 03 00 00 00 47 4e 55 00); linker-generated ID bytes differ across clean builds.
- Fielded ID: b58ea09cddd1bcc4. Clean rebuild ID: 4ccd2df6da433470.
- The note sits immediately before the unchanged 732-byte .data load section. There are **no executable-code, rodata, or linked-data differences**.

Because the build ID is intentionally used in the radio's executable system-page binding, the newly generated ID must not be silently replaced with the fielded ID. The comparison deliberately normalizes only those eight bytes, with a unique header and fixed location; every other difference fails closed.

## Instrumented firmware

A complete normal clean build was performed with the recovered exact flags, plus explicit K6_SOTTO_FAST_SPI=1 and K6_SOTTO_SPI_BENCH=1.

- Size **117,676 bytes**; flash free **5,204 bytes**.
- RAM0 used **3,768**, free **72**; module arena **14,976**, reserved stack **2,812**, matching fielded memory contract.
- SHA256: c2b0dbb63e30fbdc8178e3022bcd38ec5691734a08147e3baa8573c889c5eee7.
- New GNU build ID: e7a882ab75a630df.
- Saved (NOT flashed): /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/spi-exact-flags-instrumented/spi-exact-flags-diagnostic-offline.bin

The updated tools/k6_sotto_spi_profile_gate.py accepts only *bit-identical binary contents after masking the exact eight-byte GNU note*, while still requiring reference SHA256 pin, unique probe marker SPB1, intact KUL2, flash fit and matching RAM/stack contract. It explicitly reports a build-ID change requiring dependent SPI-page review. **Its output means ready for attended review, NOT permission for automatic flashing.** All 18 selected gate and overlay tests passed. Report:
recovery/spi-exact-flags-reviewed-profile-20261126.json

## SPI library build-ID audit

Three preserved, complete 2MB SPI backups were examined offline. All contained 32 occupied KUL2 logical IDs but **zero** K6M flags-of-(LIB|PAGE) executable objects. The separate KSD1 system overlay at 0x19C000 exists in the snapshots, but is bound to a different older build ID (aba032cfc1d2b5e1), already incompatible with the fielded firmware's b58ea09cddd1bcc4. The fielded profile disables K6_SYSTEM_INSTALL. No existing KUL2 module or card is to be erased or migrated for the SPI timing test.

A new read-only serial STATUS on the physically connected K6 confirmed working cable protocol 5 and the restored SOTTO GP0 at logical slot24 (9,784 bytes), MIC IRQ at slot25, and SOTTO MIC at slot30. A new full SPI backup is being acquired before any firmware operation; initial 500kbaud burst transfer was unreliable, and the installer fell back to 115200baud. The backup must be completed and its calibration-sector repeat-read validated before any flash.

## Physical test constraints

The on-device SPI benchmark uses a cable-only command 0x73 and 100us TIM14 ticks. It does **not** key PTT, use the microphone ISR, or write external SPI flash. It reports two 128-read timing batches at each length (20 and 128 bytes), verifies full data equivalence, and is driven by tools/k6_sotto_spi_bench.py with explicit --ack-reboot.

Before hardware: finish new backup; validate current KUL2 slot types and current module images; ensure existing known-good reference image and recovery procedure remain intact; use radio's stock bootloader only and check post-flash boot/catalog. Flashing/rebooting may require physical operator intervention if a fault occurs. Do not transmit RF for this diagnostic. Restore known-good firmware after the measurement unless the operator explicitly approves retaining the experimental image.