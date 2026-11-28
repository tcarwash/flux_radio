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

## First attended tone-on run yielded no complete record; diagnostic correction

The user completed the physical generator-on MIC ROUTES test and reconnected serial. At **2026-11-25 20:17:08 UTC**, K6 live serial confirmed standby idle, TX=false, 7.75V, and mailbox response `available=false`. The response was permanently saved as `routes-v1/mic-routes-tone-on-01.json`, SHA256 `495d66fe8f0e534bea6716a43c5123f36218d3db3257562bd073977104aabdaf`. **No AF/GP0 measurements were recovered from that run**; do not report a null/negative audio result. The original module emitted `MAB1:CLR` at launch but stored only completed nine-stage runs, providing no post-reconnection indication of the failure reason. The physical cause of the abort is not directly proven because there was no serial cable attached during capture.

Code review identified a concrete TX/GPIO state ordering issue: `MIC ROUTES` saved `REG_33` *before* `tx_on`, but TX state transitions can change band/LED GPIO bits. The module later attempted `fd_write(0x33, saved33 & ~0x0100)` to enable GP0, which the host's strict `REG_33` gate rejects if *any* bits other than GP0 enable/value would change. The correction saves current `REG_33` and `REG_34` **after** the TX lease and `mic_stream(0)` have entered their steady routing state, before GP0 writes. A native host regression now deliberately toggles a non-GP0 TX LED/GPIO bit during `tx_on` and verifies the 9-stage run is still accepted and the original radio GPIO state restored at `tx_off`.

Another improvement: failures **after PA-muted TX lease begins** generate a CRC-protected version 5 `MAB1` mailbox report containing positive error code and number of completed stages, without treating the result as a successful version 4 measurement. The run also displays `MIC ROUTES ABORT / Error saved for serial` rather than silently returning. The reader (`tools/k6_mic_result.py`) maps failure reasons (PTT released/PCM overrun, bad AF write/readback, GP0 enable refusal, readback failure, time limit, restoration error). The original primary error is preserved even if a cleanup write also fails. The success path still emits v4 with full nine-stage measurements. Validation: 41 focused tests passed after changes, including simulated TX-register mutation, success, cancellation, forced GP0 write failure, v5 recovery, decoder and v1-v3 compatibility. No base firmware change required.

**Next physical run:** after installing revised signed MIC ROUTES in the existing slot25, repeat the 1 kHz/nominal 0.1V tone-on attended test. Save result with a new exclusive filename `routes-v1/mic-routes-tone-on-02.json` (because `tone-on-01.json` already exists and is only an empty result). If it returns v5 instead of v4, use error code/stage count to fix the abort before another attended run. Only after a successful v4 tone-on result run the tone-OFF control to compare channels. Preserve slot30 unchanged.

### Corrected MIC ROUTES v2 installed and serial preflight verified

The correction in commit `e6c0c48a` passed 41 focused tests and was installed on the physical radio (bench `flux-mic-routes-v2-install`) after verifying that base firmware is `5ad086ad63aa3a2a`, the radio is idle, and the old slot25 is exactly `0824c121a2fadeec80a5705c2b3ebca08c3539363c160f82cf2c276a6d7822a7`. The updated **slot25 MIC ROUTES** has serial `1795638003`, 2416B code, 2440B envelope, SHA256 `8e7f9061f335749471ccf7d48f71637f072d8009c03af08c7d9ef6df5d6a0702`; physical readback matched the staged signed envelope exactly. Slot30 SOTTO MIC was unchanged before/after. The base firmware was not reflashed, no RF was emitted, and the full SPI backup is unchanged. Staged signed image and physical proof artifacts are under `/home/tyler/flux-bench/sotto-mic-calibration-20261125/routes-v2/`.

Cable-connected `run-coop 25` loaded exactly 2416B and returned expected `-4` due to shared UART/mic exclusion, emitting `MAB1:CLR` without transmitting or freezing. Live `status` after preflight reported standby idle, TX=false, 7.76V; `mic-result` reported empty as expected until an attended physical test. Library status verified slot25 new image and slot30 unchanged. The bench lock was released after installation.

**Next attended run must be re-done**: previous tone-on attempt generated no saved measurements and cannot be reconstructed. With generator ON at 1kHz/0.1V and dummy load connected, launch `CATALOG → MIC ROUTES`, hold physical PTT at least ~3 seconds until `MIC ROUTES SAVED` (success) or `MIC ROUTES ABORT` (error stored). Reconnect cable WITHOUT restarting, and read `mic-result --save .../routes-v1/mic-routes-tone-on-02.json` (avoid overwriting `tone-on-01.json`, which contains `available=false`). If version5 returns, use error code and completed stage count to identify hardware guard or runtime issue. If version4 returns with all nine stages, proceed to generator-OFF control as planned. The user does not need a scope or PCB access for either test.

### Corrected v2 physical tone-on result retrieved, 2026-11-25 20:22 UTC

After the operator ran the 1 kHz/nominal 0.1 V tone-on test with PTT and reconnected without power cycling, the radio was idle/standby, TX=false, battery 7.75V and VFO A 443.500MHz. The new `MIC ROUTES` module completed **all nine stages** and returned a CRC-valid version4 mailbox. Saved host JSON `/home/tyler/flux-bench/sotto-mic-calibration-20261125/routes-v1/mic-routes-tone-on-02.json`, SHA256 `fb8d680df5b3d612a97608f9446862ea33d72f95458b24ced0529f389cc6fd15`. UTC timestamp `2026-11-25T20:22:21.637559+00:00` is retrieval, not physical capture, time.

| AF selection (speaker AF path) | Steady mean abs PCM | Steady 1kHz peak PCM | PA11 HIGH fraction |
|---|---:|---:|---:|
| 4 BASEBAND1/raw | 1 | 0 | 1.0 |
| 9 BYP | 1 | 0 | 1.0 |
| 5 BASEBAND2 | 1 | 0 | 1.0 |
| 1 FM AF | 1 | 0 | 1.0 |

| GP0 output select → PB1 | PB1 HIGH fraction | PB1 asynchronous bit-transition fraction | approximate per-stage VOX max |
|---|---:|---:|---:|
| 9 | 0.0 | 0.0 | 184 |
| 11 | 0.0 | 0.0 | 184 |
| 12 | 0.5020 | 0.5725 | 180 |
| 13 | 1.0 | 0.0 | 180 |
| 14 | 0.0 | 0.0 | 180 |

The overall maximum RFIC VOX was **197**, noticeably lower than the original physically injected tone-on MIC RAW run VOX **660** and prior tone-off control VOX **315**. This may reflect configuration differences, route-specific VOX behavior, generator output/amplitude differences, or environmental state; it prevents treating this run as an independently **verified analog tone at the RFIC**. Do not infer a clean mic signal or assert that the tone was actually present in every stage solely from the operator's setting. The *exact* codec, AF, GPIO results are real and useful even if there is no validated tone correlation.

**Result:** all AF selections tested leave the existing PA11 raw sampler at 100% HIGH with near-zero PCM and 1kHz amplitude; no observed sustained microphone PCM at PA11. GP0 selection 12 displays robust asynchronous PB1 bit activity, but this was previously known as a possible alternate high-rate digital signal and is **not demonstrated TX microphone samples**. The modes 9/11 were low, 13 high, 14 low in this TX state, which is distinctly different from previously characterized receive IF in GP0=13. The coarse 256 reads per 10ms are not high-rate enough to demodulate signal content, and aliasing/route switching are alternative explanations.

**Next control, not yet performed:** keep precisely same connector setup, VFO, module, PTT duration but set signal-generator output OFF; collect `routes-v1/mic-routes-tone-off-01.json`. Look for repeatable VOX difference to confirm whether tone-on stimulus was effective, and especially any change in GP0 selector12 bit statistics. If the tone OFF and ON runs match, do not claim the selector12 activity is microphone-driven. If they differ, design a time-resolved, oversampled GP0 selector12 route-specific experiment with independently verified 1kHz spectral content, rather than concluding from sampled bit-transition fractions alone. No additional firmware flash is needed for the OFF control.

### Corrected MIC ROUTES tone-OFF control (2026-11-25 20:23 UTC)

Operator performed the generator-output-OFF control, reconnecting serial **without a reboot**. The radio remained idle/standby, transmitting=false and at 7.75V. The result was a valid version4 nine-stage capture stored at `/home/tyler/flux-bench/sotto-mic-calibration-20261125/routes-v1/mic-routes-tone-off-01.json`, SHA256 `12b6d3435e29a6f01061c4c4bdea88f0a40f4029ddede45c329a895e8b6abdb2`, read back `2026-11-25T20:23:54.428281+00:00` UTC. The companion tone-on result is `mic-routes-tone-on-02.json`, SHA256 `fb8d680df5b3d612a97608f9446862ea33d72f95458b24ced0529f389cc6fd15`.

| Route | Tone ON | Generator OFF | Interpretation |
|---|---|---|---|
| PA11 AF 4 (raw) | PCM abs=1, 1kHz=0, raw high=100% | Same | No useful PA11 audio in either run |
| PA11 AF 9 (bypass) | PCM abs=1, 1kHz=0, raw high=100% | Same | No useful PA11 audio in either run |
| PA11 AF 5 (baseband2) | PCM abs=1, 1kHz=0, raw high=100% | Same | No useful PA11 audio in either run |
| PA11 AF 1 (FM) | PCM abs=1, 1kHz=0, raw high=100% | Same | No useful PA11 audio in either run |
| GP0 mode 9 | PB1 high=0%, transitions=0% | Same | Static LOW |
| GP0 mode 11 | PB1 high=0%, transitions=0% | Same | Static LOW |
| GP0 mode 12 | PB1 high=50.20%, transitions=57.25% | PB1 high=49.41%, transitions=56.47% | Digital switching is similar with generator ON/OFF; **no demonstrated mic correlation** |
| GP0 mode 13 | PB1 high=100%, transitions=0% | Same | Static HIGH |
| GP0 mode 14 | PB1 high=0%, transitions=0% | Same | Static LOW |

Crucial stimulus-quality caveat: the captured **overall RFIC VOX maxima are practically equal** (ON 197; OFF 198), and per-stage VOX is also similar (ON GP0 stages roughly 180–184 vs OFF 180–196). That differs from the earlier physical MIC RAW tone-on/off pair (max VOX 660 vs 315), so this test pair has not independently confirmed that its nominal 1kHz generator stimulus entered the RFIC during the tested TX states. The ~0.8 percentage-point differences in GP0 selector12 unsynchronized bit fractions could be ordinary sampling phase/aliasing jitter; they cannot validate voice capture without an actual measured 1kHz response. Thus results are **negative in the tested configurations**, not a definitive proof of no possible internal RFIC→MCU path.

Recommended next diagnostic is an instrumented **RFIC VOX tone-present gate** with a low-threshold/captured time series before sweeping routes, or a verified generator amplitude/tone check through ordinary analog-FM TX, then only repeat the relevant GP0 path when an independently demonstrated ON/OFF mic stimulus difference exists. Avoid further unattended route sweeps on an unvalidated stimulus. Because the already-known PA11 initialization transient is invariant ON/OFF, AF route sweeps alone are not likely to fix PCM capture. No further firmware or SPI modification has been made after these results; `SOTTO MIC` stays in slot30.

### Verified generator-awake tone ON re-run, 2026-11-25 20:26Z

The operator suspected the generator had fallen asleep during the first nominal tone-on run, woke it and re-ran **the same installed MIC ROUTES v2 module** with nominal 1kHz/0.1V output ON, then reconnected UART without power cycling. Physical live status was standby idle, transmitting=false, 7.75V, VFO A 443.500MHz. The CRC-valid v4 report contains all nine stages; permanently saved as `/home/tyler/flux-bench/sotto-mic-calibration-20261125/routes-v1/mic-routes-tone-on-03.json` with SHA256 `e31976edc22b15a28e2e3f96926182448e94337f92971472774d5eace59bc3d9`, serial retrieval UTC `2026-11-25T20:26:27.210556+00:00` (not physical capture timestamp).

**Stimulus confirmation:** total maximum RFIC VOX **537** with generator awake vs **198** with generator OFF and **197** during the apparently sleeping original ON run. The GP0-stage RFIC VOX increased from about 180–196 OFF to about **524–528** ON. That is direct evidence the RFIC is sensing a substantial injected microphone signal during this re-run. VOX is an envelope detector rather than proof the injected tone's frequency is precisely 1kHz, but it independently validates a meaningful ON/OFF stimulus contrast.

| Routing diagnostic | OFF control | Verified tone ON redo |
|---|---|---|
| AF4 PA11 1kHz amplitude / mean abs PCM / raw HIGH | 0 / 1 / 100% | 0 / 1 / 100% |
| AF9 bypass PA11 1kHz amplitude / mean abs PCM / raw HIGH | 0 / 1 / 100% | 0 / 1 / 100% |
| AF5 PA11 1kHz amplitude / mean abs PCM / raw HIGH | 0 / 1 / 100% | 0 / 1 / 100% |
| AF1 PA11 1kHz amplitude / mean abs PCM / raw HIGH | 0 / 1 / 100% | 0 / 1 / 100% |
| GP0 mode9 PB1 HIGH / edge fractions | 0% / 0% | 0% / 0% |
| GP0 mode11 PB1 HIGH / edge fractions | 0% / 0% | 0% / 0% |
| GP0 mode12 PB1 HIGH / edge fractions | **49.41% / 56.47%** | **50.59% / 61.18%** |
| GP0 mode13 PB1 HIGH / edge fractions | 100% / 0% | 0% / 0% |
| GP0 mode14 PB1 HIGH / edge fractions | 0% / 0% | 100% / 0% |

**Meaning:** a genuine RFIC microphone stimulus now fails to yield *any* steady PCM/1kHz component in all four tested AF routing selections, strongly ruling out those direct paths to the existing PA11 sampler. GP0 selector12 shows an increase in **asynchronously sampled** PB1 transition fraction of 4.71 percentage points and a 1.18 percentage-point increase in HIGH fraction, but the probe takes only 256 samples per 10ms; timing/aliasing, RFIC output-frequency changes, and CPU sampling cadence could explain the difference. GP0 selectors13 and14 exchanged static digital HIGH/LOW states despite the same configured selector sequence, further warning against simple binary interpretations. **No recovered PCM or 1kHz spectral signature from GP0 has been demonstrated.** This re-run supersedes the first nominal tone-on trial for stimulus validation; do not average the two as though generator output was known ON in both.

Next highest-value non-invasive follow-up, if pursued: a **dedicated selector12 time-series capture** with the PA muted, physical PTT, a calibrated generator-off baseline and distinct 1kHz vs 2kHz input runs; record oversampled / DMA PB1 bit-density blocks rather than only one-bit HIGH/edge fractions. Confirm ROI timing and check for input-frequency-correlated spectral energy using a reproducible host-side demodulator. Asynchronous 25.6k reads/s may not resolve the ~6.5Mb/s sigma-delta digital stream; investigate the existing GP0 DMA and timer infrastructure for capture timing and interference with the PA11 TIM17 ISR before making any new changes. Preserve the good Sotto module in slot30; do not treat GP0 activity alone as mic access.
