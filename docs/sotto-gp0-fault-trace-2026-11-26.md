# Sotto GP0 firmware-owned abort trace (GPF1)

November 26, 2026. This opt-in diagnostic distinguishes faults at three service boundaries without changing the physically launchable SOTTO GP0 module.

## Problem

The installed SOTTO GP0 code reports GP0 ENGINE for a generic negative return. This could mean PCM/DMA ring loss, codec resource-read failure, asynchronous FSK failure, or an unrelated encoder/state error. The original module contains 9,784 bytes and has only 48 bytes of spare scratch headroom; adding new LCD strings produced oversized modules and was rejected offline. The unchanged original code SHA256 is 551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e.

## Design and API

Enable the firmware overlay with environment K6_SOTTO_GP0_TRACE=1. It requires K6_RAM0=1 and the existing Sotto resource, async FSK and mic services. The default value 0 leaves the field profile unmodified. The reversible generated-source overlay is implemented in firmware/k6-aura/apply-sotto-gp0-fault.py and firmware/k6-aura/sotto-gp0-fault.rs.inc. Repeated enable/disable restores every affected generated source byte-for-byte.

The diagnostic uses 24 extra bytes of protected RAM0 (no module arena or main-stack change), a single boot-local GPF1 record, and a read-only cable command 0x75. The original module and ABI remain unchanged; the DMA IRQ handler is unchanged. Only firmware callback wrappers record failure counts.

The record captures: module serial and actual return, GP0 capture start, PCM loss before stop, resource-read failures, FSK async errors and last FSK error, DMA error, IRQ count, peak IRQ ticks, and missed DMA halves. Counters saturate. A negative internal codec result with no resource error can remain unclassified. No stale DMA counters are reused if capture never started.

Wire report is exactly 44 bytes: ASCII GPF1 plus ten little-endian 32-bit fields (module serial, signed return, flags, resource errors, FSK errors, signed last FSK error, DMA error, IRQ count, ISR peak, missed halves). Host decoder tools/k6_sotto_gp0_fault.py validates length, magic, flags and internal consistency. The read request never transmits, keys PTT, or writes external SPI flash. Ending the cable session reboots the receiver.

## Offline image and memory audit

Candidate image: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fault-trace-ram0-final/gp0-fault-ram0-final-offline.bin

SHA256: be72435fe7d1af7165a389138a765b7c0f0d6b21af2a3f5b2702b8d085c616fa. Size: 117,308 bytes, with 5,572 flash bytes free.

Memory comparison, known-good versus trace:
- Arena base: 0x20001930 versus 0x20001930.
- Arena: 14,976 bytes versus 14,976 bytes.
- Main stack reservation: 2,812 bytes versus 2,812 bytes.
- RAM0 used: 3,768 bytes versus 3,792 bytes.
- RAM0 remaining: 72 bytes versus 48 bytes.

The fail-closed offline gate in tools/k6_sotto_gp0_fault_gate.py passed READY_FOR_ATTENDED_REVIEW with zero failures. It verified the original known-good firmware SHA, clean baseline identity except the eight-byte GNU linker ID, identical module SHA, KUL2 signature, unique GPF1, absence of SPI benchmark SPB1, flash fit, and memory constraints. Report: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-fault-trace-final-gate-20261126.json. The gate does not authorize unattended flashing.

Recovery image: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/firmware-gp0-tx.bin, SHA256 8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d.

Verified 2MB SPI backup with factory calibration: /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/k6-spi-before-sotto-spi-latency-test-20261126.bin, SHA256 18fd1d3cb118c36eecf6111ec805f77ff70bd1732ed7576d459183f341d74dbd.

## Physical test, only after diagnostic firmware installation

1. Check radio boots to VFO, is on a dummy load, and shows no unintended transmission; confirm module slots24/25/30.
2. Unplug the programming cable. Open CATALOG, run SOTTO GP0 and hold PTT for 3–5 seconds while speaking; release if the module reports an error. Normal TX lease and dekey safeguards remain.
3. Reconnect cable WITHOUT turning radio off or cycling the battery. A hard reset loses RAM-only GPF1 evidence. Read the result immediately with:

    python3 tools/k6_sotto_gp0_fault.py --port /dev/ttyUSB0 --ack-reboot --save /storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/gp0-live-failure-20261126.json

4. The read command reports fault classes and reboots on cable-session exit. Do not call a rebooting status command beforehand.
5. If necessary, restore the original known-good image with the existing installer under attended bootloader/cable control. Do not erase SPI flash or replace module slot24 for a firmware-only diagnostic.

Tests test_k6_sotto_gp0_fault_trace.py and test_k6_sotto_gp0_fault_gate.py verify all report and gate invariants, overlay reversibility, and unmodified original module source, alongside the existing virtual end-to-end TX/RX tests. No physical GP0 abort classification has been captured yet.
