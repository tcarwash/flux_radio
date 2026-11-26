# K6 SOTTO GP0: first physical engine abort

Date: 2026-11-26. Radio: K6 B-chip, Flux KUL2 firmware API30, dummy-load human-operated PTT. No automatic RF test was triggered.

## Physical evidence

- Corrected KUL2 firmware boots and enumerates the preserved module library, including SOTTO GP0 in **logical slot 24**.
- With optimized 9,700-byte module, operator saw TX/PTT indicator for about one second, no observable RF, then return to catalog. RTL-SDR monitor at nominal **433.500 MHz** with **−30.5 kHz** decoder offset captured no sync candidates, rejected packets, or valid packets; monitor reported no USB/capture errors. Actual RF channel must still be confirmed from radio VFO; a zero packet count does not independently prove no RF.
- Stage-1 module (9,744 bytes) displayed **`GP0 RUN ABORT`**. Its `mic_stream(8,...)` startup call returned success, but subsequent live code failed. This by itself does **not** prove any PCM ISR executed.
- Stage-2 module (9,784 bytes) displayed **`GP0 ENGINE`**, which establishes `sl_engine_tick` returned negative. This is compatible with PCM ring loss, encoder/SPI resource failure, or FSK callback rejection. It does not isolate which.

## Stage-3 diagnostic

- Only the module in packed KUL2 **logical slot 24** is replaced. Base firmware, K6CB, IVF deck, `MIC IRQ` (slot25) and `SOTTO MIC` (slot30) are untouched.
- In `c/k6/mod/sotto_gp0_irq_tx.c`, a negative `sl_engine_tick` branches to the existing PCM error handler when `s->mic.lost` is set. The LCD displays **`GP0 PCM`** on either a lost PCM ring or the existing >100ms completed-block stall. Other paths display **`GP0 OTHER`**, including engine failure without `mic.lost`, PTT cancellation and other startup failures. This is a *binary classification*, not yet a complete failure-code telemetry protocol.
- Runtime constraints require extreme size discipline: the diagnostic removes the initial module LCD prompt and early nonessential `say` messages. It also removes the 300µs idle foreground delay and two early redundant keypad checks. Crucially, it **retains a keypad/PTT check after the 800ms PA-muted microphone settling period, checks throughout active capture, requires real PTT throughout, and preserves the 20-second module limit, guarded 25-second TX lease, synchronous mic stop and `tx_off` on abort**. The IRQ remains preemptible. Since the no-delay change alters scheduling, its hardware performance is experimental; do not assume identical RF/PCM timing to the previous build.
- Cortex-M0 module **9,816 code bytes**, SHA256 `2019167f0c09ef69f1867ffd1fc85f9541d76ece6b797aeb1685741c718a927d`, entry16, level30. Calculated scratch **4,096 bytes**, required **4,080**, margin **16**.
- Matching virtual GP0 PCM→Sotto encoder→FSK→independent RX, scratch, module, API/IRQ and PCM suites: **44 passed**, run with `/home/tyler/.local/share/flux-rf-venv/bin/python` (includes Unicorn). This is offline simulation, not physical end-to-end proof.
- Verified physical installation: 170/170 transfer chunks accepted; 9,840-byte signed K6M envelope read back exactly from logical slot24 and adjacent slots25/30 unchanged. Previous stage-2 envelope backed up under the release recovery folder as `slot24-stage2-engine-9784.k6m`. Installation report: `engine-cause-install-v5-20261126.json`.

## Next attended observation

Connect the radio to the dummy load, confirm VFO channel (nominal 433.500MHz), disconnect the UART/microphone-shared cable, select SOTTO GP0 (the new diagnostic deliberately displays no initial prompt), hold real PTT for 3–5 seconds unless it exits earlier, and note the LCD result. `GP0 PCM` suggests a DMA producer/consumer or PCM-stall issue; `GP0 OTHER` excludes the `s->mic.lost` case during engine failure but does **not** rule out a resource/codec/FSK problem. Reconnect UART without rebooting. Do not claim successful TX until the external SDR actually decodes a full BEGIN/VOICE/END sequence.

## Critical regression discovered on stage-3 hardware (2026-11-26)

**DO NOT INSTALL OR RUN stage-3 9,816-byte diagnostic, SHA256 `2019167f0c09ef69f1867ffd1fc85f9541d76ece6b797aeb1685741c718a927d`.** The operator reports that simply launching this module hangs the K6. Root cause unproven. The stage-3 diagnostic removed the initial LCD prompt and removed early keypad cancellation checks to stay below its arena limit, which can make the 30-second wait-for-PTT loop appear frozen. It also removed a foreground delay and had only 16 bytes calculated scratch margin. A real runtime crash or memory pressure remains possible. Do not attempt further PTT or relaunch with stage-3.

The source has been rolled back to the previously hardware-launchable stage-2 `GP0 ENGINE` diagnostic (9,784 bytes, SHA256 `551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e`). Its full pre-install library envelope is saved under `recovery/slot24-stage2-engine-9784.k6m` and has 48 bytes declared scratch margin. A guarded one-shot rollback installer, `recovery/rollback-gp0-hang-v5-to-v4-once.py`, has been created offline. It requires the K6 to boot normally and have its serial programming cable attached; it verifies the currently installed 9,840-byte envelope SHA256 `28dae554875702ed6b151b9aa58dca126531aab2d37d466913335c59955f9b71` before any writes, saves it, writes only packed KUL2 logical slot24, then verifies readback and neighboring modules. **No rollback has been written to hardware as of this note.** The radio was last reported to be unresponsive after a module launch; do not attempt serial writes before the operator confirms it has been safely powered down and booted to VFO.

## Physical rollback completed (2026-11-26)

After the operator confirmed the K6 could boot to VFO, the previously prepared guarded recovery script ran over the existing programming cable. Preflight verified the **exact** installed stage-3 9,840-byte module envelope SHA256 `28dae554875702ed6b151b9aa58dca126531aab2d37d466913335c59955f9b71`, then saved a rollback copy. It restored the prior stage-2 GP0 module with code SHA256 `551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e` to logical slot 24. All 170 transfer chunks were accepted, and the new **9,808-byte** on-radio envelope read back exactly (SHA256 `0dccf301c1c6bd93bdc25cb06805e79ef0ba2d35715d689b1ed2e16a8259e143`). Slots 25 and 30 were verified intact. A second independent cable `status --legacy` session confirmed slot 24 is now SOTTO GP0 **9,784 B code**, slots 25 and 30 still MIC IRQ and SOTTO MIC. No firmware reflash, SPI format, or RF transmission was involved. Evidence: `/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/rollback-hung-gp0-v5-to-v4-20261126.json`.

The on-radio module listing was verified; the operator has not yet confirmed a fresh **manual** launch of the restored stage-2 module after the rollback. Keep the TX experiment paused until the actual root cause and safe, adequately sized diagnostics are established.
