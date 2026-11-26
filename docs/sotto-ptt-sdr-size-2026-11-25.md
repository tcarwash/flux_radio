# Sotto K6 PTT exit, fresh RF/SDR qualification, and GP0 size reduction

November 25, 2026 (Pacific time). Independent worktree `fix/sotto-ptt-sdr-size-20261125`.

## Current radio (not re-flashed)

- Running build ID `b6a3df0d360d3110`, live module API max 31, active VFO A 433.500 MHz after the test.
- Slot 30 remains the original `SOTTO MIC` (9,780-byte code); slot 27 is restored to `FSK DIAG` from its exact saved code image. Bench lock released, radio idle, not transmitting.
- An earlier cable-connected slot-30 `SOTTO MIC` session returned `-4` after about 8.9 seconds. The firmware `mic_stream(op=3)` deliberately refuses physical PTT preflight when `LIVE_ACTIVE` owns the external mic/programming UART. Do not conflate this with a verified physical PTT button defect: a cable-unplugged live microphone test is still needed.

## Fresh physical K6 TX to RTL-SDR decode

Evidence: `/storage5/dev/sotto-monitor/evidence/bench-ptt-sdr-size-20261125/pcm-rf-current-fw-02/`

- Installed a previously independently qualified `SOTTO PCM RF` fixture temporarily into slot 27, verified the radio was idle and tuned 433.500 MHz, sent one bounded fixture transmission and recorded SDR diagnostics and IQ evidence.
- The host Sotto monitor decoded exactly **three new CRC-valid packets**, `begin`, `voice`, `end`; **zero CRC-rejected packets** and zero sync-bit errors for these three events. The module returned normally with result `8` after ~1.25 seconds.
- Immediately restored slot 27 `FSK DIAG` from the original saved envelope. Radio final status confirmed idle. This verifies *the installed firmware's fixture PCM→Sotto RF→RTL-SDR decoder* path, **not** physical mic→ADC→GP0→encoder on the new experimental image.
- Monitor was configured for 433.500 MHz nominal receive. Preserve the monitor's stored capture-center/offset fields; do not assume SDR center equals nominal tuned frequency.

## GP0 TX module changes (offline, not installed)

- Replaced split immediate-held-PTT and fresh-PTT debounce paths with a single bounded 60 ms stable-assertion check, always rejecting unsupported/UART-owned PTT or another keypad input; no autonomous PTT.
- `SOTTO GP0` code shrunk from **9,796 B** to **9,700 B** (96 B / ~0.98%). Estimated scratch grows from **4,116 B** to **4,212 B**, changing reserve over 4,080 B required from **36 B to 132 B**. The memory-capacity test now checks the real computed arena accounting rather than rejecting smaller code via a hardcoded upper limit.
- Validation: 19 targeted tests passed with Unicorn Cortex-M0 and native codec / independent virtual receiver (`tests/test_k6_sotto_gp0_end_to_end.py`, `test_k6_sotto_module.py`, `test_k6_sotto_gp0_release.py`). These are offline tests; not a DMA timing measurement.

## Flash-ready candidate staged; NOT yet flashed/boot-qualified

- `/storage5/dev/flux-sotto-gp0-ptt-optimized-release-20261125/`
- Archive: `/storage5/dev/flux-sotto-gp0-ptt-optimized-release-20261125.tar.gz`
- Candidate core firmware `firmware-gp0-tx.bin`: **119,820 B**, **3,060 B** below the 122,880 B flash ceiling, SHA256 `f84e8a4700b4b2a8a079acb3c0721b33fe249f42d24d51213103e442ae6eea79`.
- Candidate `SOTTO GP0` module: **9,700 B**, SHA256 `2c40039adc836be76b8f6f9415de0007fce27af18a721f618ba53d24870a4167`.
- Includes pinned `K6CB` codebook, `K6SC` deck, `module-bundle/`, `release.json`, readme and SHA256SUMS. The existing core firmware binary was **not** rebuilt or shrunk in this work; the savings apply to the loadable module and scratch. Core has sufficient baseline headroom but should still receive size-profiling work.
- This is a transmitter-focused firmware candidate; native 4800-baud RX is omitted and SDR decoding remains external. Preserve existing installed firmware until attended flash/recovery and GP0 live mic tests succeed.

## Remaining physical gate

With the radio on a dummy load and SDR monitor receiving, stage/install the GP0 candidate after backing up existing SPI and module-slot envelopes. Disconnect programming/mic UART before PTT; key one bounded spoken/test-tone transmission; reconnect without reboot to retrieve boot-local diagnostic state. Check microphone sample production, encoder frames, TX completion/flush, CRC-valid voice packets and decoded audio. If the button immediately closes again, capture the exact module return and distinguish `mic_stream(op=3)` refusal from keypad cancellation, TX lease failure or encoder underrun. Do not infer live-microphone success from the fixture RF test.
