# SOTTO GP0 cooperative preload: first on-radio test and next diagnostic

**2026-11-26 Pacific — observed live failure; follow-up offline only.**

## Operator's first test after firmware flash

Firmware `117,052` bytes, SHA256 `ce8ebab442b63915d653eef19b79f72aadb5aae0fc9843e1210d6fbf701f473a`, from commit `932435d1` on `experiment/sotto-cooperative-fsk-preload-20261126`, was flashed through the stock K6 bootloader with all 115/115 KiB blocks acknowledged. A subsequent physical protocol-5 cable/catalog read succeeded, and KUL2 logical slot24 still held `SOTTO GP0`, 9,784 bytes, serial `1795730062`; slots25 `MIC IRQ` and 30 `SOTTO MIC` remained readable. No external flash/module write was made in this session.

The operator then reported **`GP0 ENGINE` again**, plus what appeared to be **very brief RF with some modulation**. This is a visual operator observation, not a recorded SDR IQ capture or independently decoded valid Sotto packet. In this module `GP0 ENGINE` is the generic LCD string for `sl_engine_tick` returning negative and exit `-12` after capture/TX cleanup. It is **not a success indicator**, and it does not classify the specific internal cause.

**This firmware was built with `K6_SOTTO_FSK_COOP_PRELOAD=1` but `K6_SOTTO_FAST_SPI=0`, `K6_SOTTO_GP0_FAST_ISR=0`, `K6_SOTTO_GP0_TRACE=0`, `K6_SOTTO_GP0_TIMING=0`.** It therefore did not include the previously qualified accelerated GP0 ISR/SPI services or the boot-local GPF1/GPT1 trace endpoints. There is **no meaningful GPF1/GPT1 error readback for this attempt**; do not misattribute a trace from an earlier firmware or infer this specific live error code from the LCD alone. The existing slot24 encoder module is unchanged, so previous codec optimization builds have not yet been physically tested.

## Why PCM overflow remains the leading hypothesis

Previous **actual radio** GPF1/GPT1 traces, while using the original Sotto GP0 module, found `gp0_dma_error=3`, **reason 2: all four 80-sample PCM blocks full**, after roughly 1,746 successful DMA half-buffer interrupts (~160ms live capture), with no missed halves, resource callback error, or FSK callback error. The same capture measured 506 successful resource reads totaling ~32.7ms elapsed, a 14.8ms synchronous FSK preload, and a 3,221-tick GP0 IRQ peak. See `docs/sotto-gp0-foreground-live-timing-results-2026-11-26.md`. The cooperative feature spreads only RFIC preload into seven polls; it does **not** reduce total RFIC work, codec arithmetic cost, or resource-read traffic. The preceding model likewise retained four-block PCM overflows in harsher Cortex-M0 profiles despite cooperative FSK. These facts make another foreground-throughput overrun plausible, **not proven** for this particular RF observation.

## Separately staged offline diagnostic profile — not flashed

Worktree: `/storage5/dev/flux-sotto-coop-buildcheck-20261126`, pinned Aura `c3aeb17aec0fd09dccf931b52f6c7d5dbf72e7ac`, Sotto `598f1bce87c22ebada2b9d0d1fcfd89107b33a09`. Rebuilt with the same recovered field feature profile, enabling:

- `K6_SOTTO_FSK_COOP_PRELOAD=1`
- `K6_SOTTO_FAST_SPI=1` and `K6_SOTTO_GP0_FAST_ISR=1`
- `K6_SOTTO_GP0_TRACE=1` and `K6_SOTTO_GP0_TIMING=1`

The resulting **118,408-byte** MCU image, SHA256 `c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec`, has **4,472 bytes flash free**, **3,816/3,840 RAM0 used (24 free)**, unchanged **14,976-byte module arena** and **2,812-byte main stack reservation**. It contains one `GPF1` and one `GPT1` marker, KUL2 support, and no optional SPB1 benchmark. Compared to the *same* cooperative + fast ISR/SPI build without trace (117,356 bytes, SHA256 `9dd86d1e9fdfbdbe7eb4886ac4954b8e1f40db4aa4f7cc21a127af29b707f4b6`), trace costs 1,052 flash bytes and exactly 40 RAM0 bytes (24 for fault + 16 for timing). These checks do not establish physical boot or RF reliability. The build and linked ELF are preserved at:

`/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/coop-preload-diagnostic-offline-20261126/`

**Offline regression:** **70/70 tests passed** on the matched diagnostic ELF and binary (124.94 seconds), including real linked Cortex-M0 API29 cooperative FIFO/independent IQ packet decode, GP0 DMA IRQ9 start/stop with traced callback, Rust host fault cases, GP0 ISR exact-output tests, GPF1/GPT1 parsers, existing codec and resource-path virtual RF tests. The older linked GP0 IRQ harness initially failed solely because it assumed the untraced `mod_sotto_mic_stream` symbol; GPF1+GPT1 registers `mod_sotto_mic_stream_traced`. The fixed harness now checks the actual registered API30 function pointer against the linked implementation; it passes both the traced and untraced ELF images. The full suite passed after that correction; there is **no physical radio evidence in these tests**.

An **alternative module** was also built offline from the four opt-ins (`fast_div`, `fast_history_copy`, `fast_cb_pair`, `fast_lag_pair`): **9,784 B**, SHA256 `9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0`, **48 bytes scratch margin**. It is **unsigned and has not replaced slot24**. Leave the original module installed for the first diagnostic test to avoid changing two variables simultaneously.

## Controlled next hardware test, only after explicit operator authorization

1. Reconfirm bench ownership, attached dummy load, external SPI backup and original firmware, read-only current radio status, and stock bootloader recovery. Flash only the separately qualified diagnostic **MCU** image, **not** the module/deck, and preserve KUL2. Verify VFO boot, protocol5, slots24/25/30 and empty GPF1/GPT1 reports before TX. Flashing alone never proves a fix.
2. After operator confirms normal VFO and dummy load, disconnect programming cable, launch `CATALOG → SOTTO GP0`, hold physical PTT for at most 3–5s or stop earlier on an error, then release. Monitor the actual tuned VFO with SDR, saving raw IQ/payload decode when possible. **Do not power-cycle before fetching diagnostics.**
3. Reconnect the cable and make the **first** read a single `tools/k6_sotto_gp0_timing.py --port /dev/ttyUSB0 --ack-reboot --save <new-file.json>` query. This retrieves both boot-local GPF1/GPT1 records in one session, saves raw bytes before the cable end/restart, and distinguishes PCM queue full (`reason=2`), invalid decimator input, stalled resource reads, FSK callback error, and other cases. The timing trace measures the *longest individual* cooperative FSK host callback (`op=0` or each `op=1`) rather than total multi-poll preload time.
4. Only if the new evidence still points to encoder foreground throughput, consider a separately authorized, signed and exact-slot-readback-verified optimized Sotto GP0 module replacement with tested rollback. Do not overwrite logical slot24 speculatively; earlier oversized modules froze the radio.

**No reboot, flash, serial session, automatic PTT, SPI/module write, or RF transmission was performed in this follow-up investigation.** The user-reported brief RF is not independently verified.
