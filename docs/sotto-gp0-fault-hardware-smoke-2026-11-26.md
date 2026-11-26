# GPF1 on-radio diagnostic installation and readout smoke test

Date: November 26, 2026. Bench was reserved as gp0-fault-trace. No PTT, RF transmit, module launch or SPI flash write was performed in the qualification.

The pinned experimental firmware image /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fault-trace-ram0-final/gp0-fault-ram0-final-offline.bin (117,308 B, SHA256 be72435fe7d1af7165a389138a765b7c0f0d6b21af2a3f5b2702b8d085c616fa) was flashed through the stock K6 bootloader. All 115 of 115 1KiB blocks were acknowledged and the firmware restarted.

The image had passed the independent offline gate READY_FOR_ATTENDED_REVIEW with no safety failures, 5,572 bytes free MCU flash, original arena base 0x20001930, unchanged stack reservation 2812, and 48 RAM0 bytes remaining. The original SOTTO GP0 slot24 code SHA256 remained 551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e.

Post-install smoke test:
- Connected with K6 protocol 5 cable (no module execution).
- Read cable-only 0x75 GPF1 diagnostic from firmware RAM.
- Response correctly decoded as GPF1 with valid=false, serial=0, return=0, no capture started, no DMA or FSK or resource failures, and zero IRQ counters. Expected because the microphone module had not been launched in the new boot.
- Saved JSON: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fault-trace-before-ptt-20261126.json
- Ended cable session and rebooted; a new cable protocol 5 catalog query succeeded.
- Preserved modules: slot24 SOTTO GP0 9,784 B, serial1795730062; slot25 MIC IRQ 3,771 B; slot30 SOTTO MIC 9,780 B.
- Catalog evidence: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fault-trace-postflash-status-20261126.txt

The temporary GPF1 diagnostic firmware intentionally remains installed to collect a future attended GP0 PTT session. The known-good rollback image remains intact at /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin, SHA256 8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d, and the complete verified 2 MB SPI backup remains unchanged. No actual GP0 abort reason is yet known.

Next attended step: keep K6 on dummy load. Unplug programming cable, launch CATALOG > SOTTO GP0, hold physical PTT while speaking 3-5 seconds, release, then reconnect cable WITHOUT a power cycle. Do not use a rebooting status query before retrieving the boot-local GPF1 RAM record. Retrieve it with python3 tools/k6_sotto_gp0_fault.py --port /dev/ttyUSB0 --ack-reboot --save NEWFILE.json. If physical launch freezes and requires a battery pull, evidence may be lost and should not be inferred.
