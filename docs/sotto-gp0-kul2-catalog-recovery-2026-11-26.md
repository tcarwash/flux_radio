# K6 SOTTO GP0: missing catalog after November 26 firmware flash

## Observed, not inferred

- The K6 booted after the cable-only GP0 firmware flash and the module menu displayed only `AURA RADIO`. No SOTTO TX was attempted.
- The read-only post-flash SPI image is `/storage5/dev/flux-sotto-gp0-ptt-optimized-release-20261125/preflash/k6-spi-after-firmware-flash-20261126.bin` (2,097,152 bytes). Other preflash backups are in the same directory. The earlier session reported only 133 changed bytes near `0x88d4`, outside the module store.
- Both packed KUL2 directory copies at SPI `0x110000` and `0x111000` pass CRC, generation-complement, allocation, and overlap checks. The newer valid generation is **418**, with 32 occupied logical IDs. The logical slot 24 module is `SOTTO GP0`, level 30, code 10,800 bytes; logical slots 25 and 30 are `MIC IRQ` and `SOTTO MIC`.
- The **legacy cable status** reported `SOTTO GP0` as physical fixed slot 23, not logical KUL2 slot 24. Its physical 16 KiB index is not its packed logical ID.

## Reproduced root cause

Compiled `c/k6/k6_lib.c` + `user_store.c` from the pinned GP0 source against the actual post-flash 512 KiB library snapshot, using a read-only mock NOR backend and API level 30:

- **Without** `KM_USER_STORE`: `kl_info()` discovers **0** usable foreground modules in logical 0–31.
- **With** `KM_USER_STORE`: `kl_info()` discovers **15** API30-compatible foreground modules, including slot 24 `SOTTO GP0`, slot 25 `MIC IRQ`, and slot 30 `SOTTO MIC`. Slot 28 (`SOTTO RX`) requires API31 and is not supposed to appear on the API30 TX-only image.

This reproduces the menu failure without changing the radio. It establishes a fixed-slot/KUL2 format mismatch as the probable cause, while a boot-time readback on corrected hardware is still needed to confirm the complete fix. The previously flashed 119,820-byte binary lacks the compiled `KUL2` signature; the corrected binary includes it.

## Corrected candidate and offline qualification

Isolated source worktree: `/storage5/dev/flux-sotto-kul2-catalog-20261126` on branch `fix/sotto-gp0-kul2-catalog-20261126`, based on `d73612b0` (the released 9,700-byte PTT-optimized GP0 module).

The new firmware build explicitly uses `K6_USER_STORE=1`, compiling `-DKM_USER_STORE` in the C library and `--cfg k6_user_store` in the Rust firmware. It retains the TX-only GP0 service and the rest of the API28–30 services, but excludes native 4800-baud RX and live serial. The build passed the RAM0 audit: 14,976-byte arena, 2,812-byte stack reservation, RAM0 3,768/3,840 bytes. The image is **116,796/122,880 bytes** (6,084 bytes spare), SHA256 `8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d`.

Focused ARM GP0 service, virtual TX, release and module tests passed **20/20**. Packed-store and cache tests passed **15/15**. A separate native C test using the actual SPI snapshot yielded the counts above. The exact optimized 9,700-byte SOTTO GP0 module was preserved (SHA256 `2c40039adc836be76b8f6f9415de0007fce27af18a721f618ba53d24870a4167`).

To prevent repeating the format mismatch, `tools/k6_sotto_gp0_release.py` now rejects firmware without a compiled `KUL2` signature and records `library_layout: KUL2`. This is a coarse release guard; hardware validation and format-aware reads are still required.

## Hardware next steps, **not yet performed**

1. Reconfirm dummy load and bench ownership. Verify complete SPI backup hashes and preserve the old logical slot 24 signed module envelope.
2. Flash the **new** KUL2-capable image in an attended session; do not clear/format/migrate SPI. Check that the on-radio menu lists SOTTO GP0 and MIC IRQ. This is only a firmware/catalog verification, **not** permission for automatic TX.
3. Check that logical slot 24 is the older 10,800-byte SOTTO GP0 before deliberately replacing it with the new 9,700-byte release module; confirm exact readback.
4. With cable disconnected and SDR monitor on the known frequency, perform a bounded operator-held PTT mic→GP0→encoder→async FSK→SDR voice test. Record IRQ/PCM queue health, codec output, BEGIN/VOICE/END CRCs, RF drain/dekey and actual module result. Preserve old module and SPI backup for recovery.

As of this document, **no new flash, SPI write, or transmitter key-up** has occurred during the November 26 catalog recovery work.