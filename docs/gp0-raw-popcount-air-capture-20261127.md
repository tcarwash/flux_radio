# GP0 RAW AIR — attended pre-decimator capture, November 27, 2026

## What this measures

The previous `GP0 PCM AIR` test sent **already-decimated 8kHz-labeled signed PCM**. This new test uses a **default-disabled firmware API30 `mic_stream(op=9)`** to store 4,096 **unfiltered uint8 popcounts** of the existing `gp0_bits_64()` GPIO DMA groups, in order, *before* the fractional boxcar, DC rejection, or Sotto encoder. Each byte is precisely 0..64. At the inferred ~93k groups/s the 4,096-group acquisition takes ~44ms; the actual group clock is independently inferred from radio 100µs ticks in the packet header. These are not the underlying 64 individual bits within each group.

`GP0 RAW AIR` is a **separate 1,881-byte loadable module**. It does nothing when installed or entered until a human holds **physical PTT for >60ms**. It explicitly gates B-chip RFIC identity `0x4829`, active VFO **443.500MHz**, transmit watchdog, physical PTT held throughout, host microphone route, and bounded capture timeout. It lets the ISR finish all 4,096 samples, then stops/releases DMA and mic route before starting finite **86 × 66-byte** guarded FSK packets, and returns to RX on all exit paths. RF transfer uses magic `DA` version1, 48 count bytes per packet, PN9 whitening, CCITT CRC16, per-session token, packet sequence and capture ticks. It is not 8kHz PCM and not Sotto voice.

The new host-only `tools/k6_gp0_popcount_air_rx.py` defaults to **28 seconds** of 443.500MHz receive at the same RTL-SDR fine offset of −31,300Hz used by earlier proven D9 PCM captures. It creates fresh `.c64` (raw IQ), `.bin` (only if **86/86 CRC-valid complete**), and `.json` evidence; it refuses to overwrite existing files, never keys RF or touches the serial port, and leaves incomplete IQ for offline salvage. The exact original K6 framebuffer or SPI flash is not exposed. **An incomplete RF recording is not a complete sample capture.** Receiver must start *before* operator starts PTT.

## Software qualification — does not imply physical qualification

- Source on isolated branch `experiment/gp0-popcount-air-20261127`, worktree `/storage5/dev/flux-gp0-popcount-air-20261127`.
- Actual C PIC module compiles and fits the permitted module arena. Fake hardware simulator recovers all 4,096 count bytes from **86 valid real C FSK frames**, including 7 fault/cancel conditions with no radio left keyed, packet replay/CRC checks and independent synthetic PHY demodulation.
- Full linked ARM image uses pinned Aura `c3aeb17` and pinned Sotto `598f1bc`, recovered API30 GP0, KUL2/RAM0, FSK and watchdog feature set. New build differs from earlier same-profile 118,408-byte baseline by **68 bytes**: image **118,476 bytes**, app limit 122,880, headroom **4,404 bytes**, protected RAM0 free **24 bytes**, stack reserved **2,812 bytes**, module arena **14,976 bytes**. SHA256 `bd74b0270b41ea21ce43c1db176244fdc1b91ea478b4553d68ac2da4496299e6`.
- Linked Cortex-M0 emulator executed **512 physical-group DMA half-frame interrupts** with actual candidate ELF, capturing 4,096 verified exact synthetic popcounts, `produced=1`, `lost=0`, observed max 776 emulator instructions/IRQ. This is **not** physical timing, frequency, startup or RF qualification. Linked standard GP0 op8 service start/stop test also passes.
- New feature flag `K6_SOTTO_GP0_POPCOUNT_AIR=1` defaults off; requires MIC/RAM0/FAST_ISR/FSK and all anti-alias options off. Normal `mic_stream(op=8)` remains untouched. The 7,500 clock correction is not required and is **OFF** for the raw-group capture.

## Recovery verified before device changes

- Previously successfully booted KUL2 firmware (preserved and hash verified): `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/coop-preload-diagnostic-physical-flash-20261126/coop-gpf1-gpt1.bin`, 118,408 bytes, SHA256 `c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec`.
- Original pre-PCM-AIR **2,097,152-byte** SPI backup (preserved and hash verified): `/storage5/dev/flux-sotto-gp0-pcm-telemetry-20261126/recovery/pcm_air/k6-spiflash-before-pcm-air-20261126T2143PST.bin`, SHA256 `b8eb8419e76933cd5504d483525fbb953f12023c34a6e03d9b7b7373f48ff3c7`.
- Existing signed `SOTTO GP0` slot24 stays untouched. Before any new module installation, query actual library slot occupancy and verify/readback/back up the chosen slot (the prior `GP0 PCM AIR` occupied slot25).

## Human-attended hardware sequence (not automatically invoked)

1. Exclusive local bench lease, dummy load, no PTT. Swap generator for programming cable; radio in **UPDATE** mode, user must confirm. Do not transmit through the programming cable.
2. Flash the new **MCU-only** candidate through the stock bootloader, verify progress and boot back to VFO. Restore the exact verified booted baseline if splash/phase hang occurs. Do not migrate, wipe, defrag or otherwise touch SPI module library or calibration.
3. With radio in normal cable mode, inspect its module catalog and read/backup existing destination, install only signed `GP0 RAW AIR` module under API30 with the trusted local module key, verify readback; preserve slot24 and all other modules. The staged **slot-independent** module bundle is `recovery/popcount_prepare/bundle`, code SHA256 `f846966248474b67bce1991b78fb23ff6eb59d425feccbbbf620eb8464064c82`; no slot is chosen until device status is checked.
4. Disconnect UART from the microphone jack, reconnect generator and leave OUTPUT OFF, set VFO A to **443.500MHz**, keep the dummy load connected, stop Sotto Monitor/other RTL-SDR users.
5. Launch RX-only PC recording **before** starting `CATALOG → GP0 RAW AIR`. Hold physical PTT until LCD **`GP0 RAW TX DONE / 86 packets sent`** (expect roughly 15 seconds; avoid releasing early). Verify 86/86 RF CRC valid, 4,096 exact bytes in 0..64, and roughly 35–65ms acquisition ticks.
6. With output still safely controlled, repeat new independent RF run with **500Hz/0.1Vpp**, then 1kHz/0.1Vpp if desired. Always use **new** output prefixes. Compare raw count-stream PSD, count variance, and replays of exact counts through the pinned Rust decimator. Do not claim the previously measured PCM noise was necessarily all aliasing without this evidence.

No raw count dump had been transmitted over actual RF at the time of preparing this note. A successful build or linked emulator cannot authorize unattended flash or transmit. Station ID/operating rules and equipment authorization remain the user's responsibility.
