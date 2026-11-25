# K6 microphone-to-MCU route probe — AF bypass and GP0/PB1, without opening PCB

## Motivation and prior evidence

Physical `MIC RAW` experiments showed PA11 sigma-delta bits settling to essentially logic HIGH by 80 ms, with the same waveform whether an injected 1 kHz microphone tone was on or off. The RFIC MIC/VOX signal is present, but PA11 provides no sustained microphone samples. The prior apparent 1 kHz energy in a `MIC CAL` one-second average was dominated by the startup transient. See `docs/k6-sotto-mic-e2e-handoff-20261125.md` and separately saved `mic-raw-live-01.json` / `mic-raw-control-01.json` in `~/flux-bench/sotto-mic-calibration-20261125/raw-pa11-v1/`.

Research already performed in `docs/k6-fd6818-audio.md` and `docs/k6-afsweep.md` establishes that the B chip's GP0→MCU PB1 path is operational for *receive IF*. Prior `i2sprobe` found no I2S frame clock or MCU microphone samples: GP0 selectors 12, 13, 14 all generated the same ~6.5 MHz one-bit receive-IF signal; selectors 9/11 were slow detector signals. This evidence **does not prove GP0 exposes TX microphone audio**. `REG_47` AF=4 is BK4819-style BASEBAND1/RAW, AF=9 is an undocumented BYP label in egzumer; they are **not** documented direct microphone sample sources. These tests check their response during a PA-muted TX microphone lease and do not assume positive findings.

## New standalone `MIC ROUTES` module

`c/k6/mod/sotto_mic_routes.c`, API30, catalog slot25 candidate. No base firmware changes, no standalone UART audio, and **never enables the RF PA**. The radio's existing installed API30 PA11 TIM17 sampler and boot-local 0x86 serial results mailbox are reused. Requires physical PTT while the microphone jack has the 1 kHz signal generator; live UART/programming cable and microphone are mutually exclusive, so a cooperative cable-connected preflight returns `-4`.

The module verifies PB1 remains a GPIO input, waits for deliberate physical PTT, keys a bounded ~4.5 s *muted PA* lease, initializes the 8kHz PCM sampler, and discards the first **120 ms** to eliminate the known PA11 startup artifact. It then collects **nine 200-ms stages** (first 40 ms settle, next 160 ms measured). Stages 1–4 switch AF selections `[4 BASEBAND1, 9 BYP, 5 BASEBAND2, 1 FM]` via `REG_47[11:8]`, without resetting the microphone stream. For each stage it stores mean absolute reconstructed PCM, coherent 1 kHz PCM amplitude and raw PA11 HIGH-bit fraction. A genuine mic-AF output that reaches PA11 would need a reproducible *tone-correlated* steady 1kHz component absent from a generator-off control.

Stages 5–9 restore AF4, temporarily enable only GP0 output by clearing `REG_33[8]`, and select `[9,11,12,13,14]` on `REG_34[3:0]`. The main loop samples MCU PB1 (`GPIOB_IDR`) 256 times per 10ms PCM block, recording a coarse fraction of HIGH samples and PB1 edges, plus approximate maximum RFIC VOX per stage. Sampling is asynchronous and **not sufficient to demodulate** a ~6.5Mbit/s IF, establish a TX voice path, or rule out all possible encoded signals. However, signal-dependent changes between controlled generator-on/off runs would justify a narrower follow-up.

`fd_write` restricts REG_33 to its GP0 bits, and every register is restored: REG_34 then REG_33 explicitly, REG_47/48/7D/30 by the existing host `mic_stream(1)` service, and the host `tx_off` returns the radio to RX. Aborted runs store no completed result. No SPI flash erases/writes, and no modification of slot30 `SOTTO MIC`.

The record fits the existing `MAB1:` + 44-char boot RAM mailbox, CRC16/XMODEM protected: binary version4, four AF entries × three 8-bit fields (PCM mean log-quantized, coherent 1k amplitude log-quantized, PA11 high fraction), five GP0 entries × three 8-bit fields (PB1 high fraction, PB1 transition fraction, VOX~4-count quantization), max RFIC VOX uint16 LE, completed count=9, CRC16. The `tools/k6_mic_result.py` parser supports v1/v2/v3/v4; no extra firmware flash is needed.

## Ground truth, controls and limits

- Each test uses the same nominal 0.1V/1kHz external microphone signal, with **dummy load attached**. Compare **tone on vs generator output off** with otherwise identical connectors and PTT duration.
- If every AF mode has near-zero steady-state 1kHz, PA11 still has no demonstrated mic audio path; a null result is not proof that the RFIC has no *other* undocumented mode.
- GP0's observed digital activity alone is not microphone audio. Interpret only **repeatable tone-dependent differences** relative to the tone-off control, and note the reduced sampling rate and possible aliasing.
- REG_47 output mode switches may affect the speaker/AF routing, but the transmitter remains PA-muted; its microphone circuit and VOX are still sampled.
- Port reading while TIM17 is active does some MCU work; the installed v3 `MIC RAW` test already showed 6,284/12,000 worst ISR cycles with simpler foreground work. Any actual `lost` signal aborts the new module without persisting a result.
- The module report is in SRAM **only until the next attempted test or reboot**. Run `python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result --save <newfile.json>` after each cable swap, without power-cycling the K6.
- If no GP0 mode differentiates tone-on from tone-off, further software-only work should focus on documented RFIC mic ADC internals or alternative audio interfaces, rather than retraining the encoder on startup transients.

## Offline validation

`tests/test_k6_sotto_mic_routes.py` compiles the actual Cortex-M0 PIC and runs the identical C module natively using a fake RFIC, 8kHz ISR PCM ring and GPIOB input. It tests sustained 1kHz in simulated AF9, silence without the generator, output selector activity differences, complete register and PA teardown, reject cable-attached PTT, failure cleanup and v4 serial CRC validation. The synthetic AF9 signal is a **test fixture**, *not evidence that the physical FD6818B bypass mode contains microphone audio*. The prior v1/v2/v3 serial mailbox tests remain in the test suite.

## Installed on the development radio (2026-11-25)

Physical installation complete from source commit `9e5e82c6` on branch `feature/sotto-mic-continuous-pa-20261125` after 41 focused tests passed. The base firmware was **not flashed** and remains ID **`5ad086ad63aa3a2a`**. Bench reserved `flux-mic-routes-install`, radio idle/standby, 7.75 V battery and VFO A 443.500 MHz. Verified previous slot25 exact image against saved `MIC RAW` envelope SHA256 `bb1ceaea2ba532d9930b74163be3cfa662098778e6dde6773ce14632125cc4c6` and slot30 `SOTTO MIC` SHA256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`. Then installed signed **`MIC ROUTES` into slot25**, serial `1795637663`, 2278B code / 2302B envelope, SHA256 `0824c121a2fadeec80a5705c2b3ebca08c3539363c160f82cf2c276a6d7822a7`. Readback from radio was byte-identical; slot30 was byte-identical before/after. Permanent staged artifacts: `/home/tyler/flux-bench/sotto-mic-calibration-20261125/routes-v1/slot25-mic-routes.k6m`, `install-report.json`, `cable-preflight.json`, `final-live-status.json`, `empty-after-preflight.json`.

Cable-connected `run-coop 25` correctly loaded 2278B, emitted `MAB1:CLR`, and returned `-4` because UART blocks physical mic/PTT acquisition. Radio remained standby idle and transmitting=false, 7.75V; `mic-result` returned an explicitly empty mailbox, and library inventory confirmed slot25 and slot30 unchanged from their intended values. No RF was emitted. A **positive 1kHz audio-route test has not yet occurred**; the two operator-controlled generator ON/OFF runs are required, including explicit `MIC ROUTES SAVED` display before reconnecting the cable.

### Next physical runs

With dummy load attached: unplug programming cable, connect existing signal generator to mic pads at **1 kHz, 0.1 V, output ON**, select `CATALOG → MIC ROUTES`, hold physical PTT for about **3 seconds** until `MIC ROUTES SAVED` appears. Reconnect serial without rebooting; save v4 via `tools/k6_live.py mic-result --save ~/flux-bench/sotto-mic-calibration-20261125/routes-v1/mic-routes-tone-on-01.json`. The test needs ~2 seconds of continuous PTT; early release will abort and intentionally not save a completed result. After successful retrieval, repeat with generator output **OFF** but same cable and PTT to get `mic-routes-tone-off-01.json`. Compare each AF mode's late 1kHz amplitude and GP0 mode's PB1 statistics across the paired runs. Do not infer an audio feed from GP0 HIGH/transition percentages alone.
