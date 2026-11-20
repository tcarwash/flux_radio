# Serial/Data development checkpoint (2026-11-19)

Experimental branch, not release firmware. The last measured four-pack image
(Base + Scan + Designer + Data) uses 121920 of 122880 bytes; 960 remain.
Baseline 119648; watchdog +1460, EL +108, configured FSK +704. Subsequent
checkpoint edits require another size measurement. Native broadcast FM is
omitted in this experiment; replacement system module is outstanding.

Reproduce the experimental image on Linux:

```sh
K6_NATIVE_FM=0 K6_HEALTH_TELEMETRY=1 K6_FONT_SUBSET=1 K6_RASTER_BOOT=1 \
K6_SERIAL_TRANSPORT=1 K6_SERIAL_IQ=1 K6_EL_POWER=1 K6_LEAN_TXDOG=1 \
K6_CONFIG_FSK=1 K6_SCAN_SERVICE=1 K6_DESIGNER_SERVICE=1 K6_FSK_RX_SERVICE=1 \
sh firmware/k6-aura/build-fd6818b.sh
```

API 24 adds raw native 2FSK (1200/2400 baud, 1–128 words, gain 16–96).
Card Link now requires API 24; its new RF framing has not been validated.
Legacy API offsets remain stable. EL is approximate, APC 7/16 of Low,
72 on blank calibration; no calibrated wattage claim. Unknown bands and
fallback behavior need review. TX watchdog is TIM14-based, not IWDG;
requires stuck-TX and fault/reset tests on hardware. Do not ship as validated.

Simulator profiles use distinct 2 MiB virtual SPI images and CRC32 tags for
cable-only firmware. Key handling uses scan observations with timed fallback.
Module launch and timing need full regression testing. No physical radio
flash is required for the simulator. Preserve signing keys and backups outside Git.

Next: upstream bootloader RAM research (f47312f, docs/k6-sotto-play.md),
reclaim 0x20000100..0x20001000, reserve vectors and bootloader trampolines;
measure stack headroom before expanding arena. IWDG soak only with RX/bench
conditions. FM/channel editor modules, configurable FSK RF validation,
FIFO streaming and 4FSK remain open.
