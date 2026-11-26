# Flux / Quansheng K6 B-chip: self-contained Sotto-1200 technical dossier

**Evidence freeze: 2026-11-25 PST.** This is an integration map, reproducibility and handoff document for **Sotto codec + native 4,800-bit/s 2-FSK + microphone ADC → MCU PCM + firmware-owned DMA/interrupt + half-duplex RF receive and playback, all on one K6**. It indexes the actual implementation and the many detailed per-test reports already checked into this repository. Consult the linked original reports for exact test fixtures, captures, version-specific details, hashes and limitations. **It is not a claim that complete on-device two-way voice currently works.**

## 1. Snapshot: what is actually proven

| Subsystem / assertion | Physical K6 result | ARM/virtual result | Current qualification |
| --- | --- | --- | --- |
| Analog external mic → FD6818B RFIC | Injected 1/2 kHz tone audible on normal FM; RFIC VOX increased with tone | RFIC route simulation | **Proven for analog path**, not sufficient for MCU PCM |
| Original PA11/TIM17 digital PCM path | TIM17 sampler produced data but no sustained recognizable 1-kHz waveform; startup/DC transients dominated, repeated controls | API30 ISR functional in emulator | **Not a qualified live audio source** |
| RFIC GP0 selector12 → PB1 → DMA1 CH1 | Input-correlated 1/2 kHz bit density at ~5.5 Mbit/s; generator-off control | Real code and modeled DMA | **Proven as a viable, unmodified-board microphone tap** |
| GP0 bit density → signed 8-kHz MCU PCM | Three attended tone/control tests produced 1,824 signed samples each; frequency tracking and no clipping/overrun | Fixed-point decimator tests | **Physical short-window PCM conversion proven**, not intelligible speech |
| GP0 DMA half-transfer IRQ9 continuous PCM producer | ~5,150 samples generated, 5,120 consumed, 6,826 IRQs, 0 missed; 24.61 µs peak ISR vs ~94.3 µs half-buffer interval | Real Thumb/PIC ISR runs under Unicorn with injected failure tests | **Physical producer with mocked foreground load proven**; codec/SPI contention open |
| Sotto1200 software encode / decode | Generated-tone Sotto native encoder ran on K6; SDR recovered packetized codec data, independent decoder got exact reference PCM | C codec, compiled ARM sender/receiver, SoapyFang IQ paths | **Proven for bounded synthesized PCM** |
| Native RFIC 4,800-bit/s FSK transmit | 6/6 physical sessions, 18/18 CRC-valid BEGIN/VOICE/END packets with qualified continuous-PA direct-register sequence | API29 callbacks and FSK FIFO/replay fault models | **Proven for that exact sequence**; generic async callback not thereby qualified |
| Operator-held SOTTO MIC RF TX | 1 BEGIN + 26 VOICE + 1 END on SDR, 28 CRC-valid, 0 rejected; PTT/LED and genuine FSK observed | Mock microphone sessions of 80/320/800 ms pass | **Packet transport proven; real voice FAILED** (almost entirely silent decoded PCM) |
| API31 4,800-baud FIFO RX / rearm | No complete two-way over-the-air K6 receive + decoded speaker audio demonstrated | Compiled linked ARM callbacks, C module runner, FIFO 33-word drain/rearm and 6-packet virtual IQ sessions pass | **Software-only qualified**, RFIC live RX + speaker unproven |
| Half-duplex RX ↔ TX ↔ RX, same installed image | No end-to-end physical two-way acceptance | Explicit mock state machine with cancellation, preemption and cleanup tested | **Integration still outstanding** |
| Compact firmware-owned GP0 + Sotto combined TX | Experimental base flashed: earliest images stuck on splash; later phase-instrumented image reached VFO; live serial still silent. Latest UART probe built but **not flashed** | Firmware compiled; tests pass; constrained RAM audits | **Not release-ready** |

### Evidence is not interchangeable

A module's success return, the red PTT LED, a CRC-valid digital packet, a radio VFO rendering, and audible decoded voice are different gates. Earlier errors were caused both by **physical packet corruption near RF burst termination** and by **SDR USB-read boundary processing**; one must not blame the RFIC merely because the live monitor lost a packet. Similarly, a clean BEGIN/VOICE/END session can contain all-silence codec frames. Treat RF packet transport, codec audio validity, microphone capture, speaker output and recovery as separately audited results.

## 2. Intended single-radio architecture

**Operating mode: half-duplex only.** PTT preempts receive and speaker playback. One radio does not transmit and receive simultaneously. The desired normal flow:

```text
RX / listening:
  RFIC 4800 2FSK demod → IRQ/status + 33-word FIFO threshold/drain
  → 66-byte checked packet → PN9 / CRC16 / FDV1 BEGIN-VOICE-END
  → Sotto1200 decoder (pinned codebook) → bounded 8kHz PCM queue
  → native DAC/PWM/speaker playout (20ms cadence; loss concealment)

PTT down:
  immediately stop RX acquisition, invalidate queued speaker PCM
  → grant bounded TX lease with independent TX watchdog, PA initially muted
  → enable RFIC MICIN + GP0 selector12 + PB1 DMA1 CH1 + TIM17 timer
  → 1024B raw bit-density DMA ring / half-transfer IRQ9
  → measured-rate fractional decimator + DC rejection → signed 8kHz PCM
  → 4 × 80-sample blocks (~40ms) → Sotto1200 encoder + SPI codebook
  → FDV1 framing + 2FSK native FIFO at 4800bps / 33 16-bit words
  → guard/settle/PA lifecycle with bounded finite packet TX

PTT up, cancel, timeout, power-down or fault:
  terminate or explicitly abort finite FDV1 session within watchdog lease
  → stop DMA & TIM17, restore IRQ9 vector/priority and RFIC register snapshot
  → de-key PA FIRST, restore RFIC, ensure serial lease released
  → clear stale queues and re-arm 4800-FSK RX / normal VFO
```

**Separation of concerns:** transmitter, receiver and codecs can share *mutually exclusive* scratch RAM because half-duplex, but the IRQ stack, live library loader, protected RAM0, OLED page buffer, C/Rust ABI, low-level RFIC register owner, plus persistent codebook data must be accounted for independently. The existing FIFO primitive handles finite frames; it is not automatically a sample-streaming RF interface. No arbitrary-IQ TX hardware claim follows from these FSK findings.

Reference: [half-duplex state contract](k6-sotto-half-duplex-design-20261124.md), [flashable acceptance gates](k6-sotto-two-way-flashable-gates-20261124.md).

## 3. Physical audio acquisition: why GP0 replaces PA11

### 3.1 PA11 failed the *audio* gate

The historical `API30 mic_stream(op=0)` used a PA11/TIM17 software sigma-delta sampler. MIC CAL, MIC TIME, MIC RAW, MIC SWEEP, A/B and tone-on/off controls showed normal RFIC VOX changes and brief ADC startup/transients, but PA11's raw stream settled near static HIGH after ~80–120ms in later tests. A 1kHz sine did **not** produce sustained PCM tone energy or expected sign crossings. Some early tone-dependent peak and variance readings were misleading; follow-up raw-bit evidence made them insufficient to declare continuous audio. The external mic was independently heard over analog FM. `mic_stream(op=0)` also owns TIM17, so it cannot be active concurrently with GP0 DMA TIM17 timing. See [full chronological mic failure and calibration log](k6-sotto-mic-e2e-handoff-20261125.md), [mic routes](k6-sotto-mic-route-experiments-20261125.md).

### 3.2 The physical bypass: RFIC digital GP0

The B-chip's RFIC GP0 route, **REG34 selector12 → PB1** with microphone enabled via **REG30 MICIN**, emitted a fast bit-density signal correlated with injected microphone tone. With DMA1 channel1 sampling GPIOB via TIM17, the measured ~64:1 density rate varied with run (**84,810–87,260 samples/s**, corresponding to approximately 5.43–5.58 M sampled bit values/s); 0.1 V nominal generator tones of 1 and 2 kHz produced different frequency-dependent spectral scores, and OFF had a substantially lower VOX/signal floor. This does not require probing or changing the PCB.

The GP0 route-only API addition is **`mic_stream(op=7)`**: it enables the RFIC microphone route while leaving the old PA11 sampler inactive. Preconditions include PA-muted TX lease, MICIN register set, no conflicting active mic/FSK resource, UART/microphone cable exclusion and safe teardown. It is separate from legacy op0. The **known-good** host built with this route and live serial is image ID `b6a3df0d360d3110`, firmware SHA256 `751995d2dd346a2cfbf7503f8b4d722076cec9164a61efa495b3ca884dc67ccb` (122,744 B). Sources: [DMA spectrum physical tests](k6-sotto-gp0-mic-dma-20261125.md), [route + calibration](k6-sotto-gp0-mic-pcm-20261125.md).

### 3.3 PCM conversion: physical short capture proof

`c/k6/mod/sotto_gp0_pcm.h` implements a fixed-point population-count / fractional 8-kHz decimator, DC rejection and saturation without FPU/dynamic allocation. The physical **MIC PCM** diagnostic generated exactly **1,824 signed 8-kHz samples per run**, discarding 800 startup samples and summarizing four 256-sample windows; it followed 1kHz and 2kHz injected tones with a lower generator-OFF control, no clipping or ring overrun. These results establish **real digitized MCU microphone audio frequency dependence**, not intelligible recorded speech or flat frequency response. The reports contain *summaries*, not full PCM WAV recordings. Potential high-frequency noise, filter response, rate drift and generator reference remain open.

### 3.4 Preemptible continuous IRQ producer: physical pass

`c/k6/mod/sotto_gp0_irq_capture.h` + PIC IRQ handler uses **DMA1 CH1 half-transfer IRQ9** with **1024B raw ring** and a **4×80-sample/644B PCM queue**. Module context lives in relocatable SRAM; the Thumb trampoline is executed in the independent compiled ARM emulator with its vector installed/restored safely. The physical 2kHz tone test under **synthetic 2ms foreground work per 10ms** yielded:

| Physical measurement | Result |
| --- | ---: |
| Density rate | 84,810 samples/s (64:1) |
| PCM produced / consumed | 5,150 / 5,120 samples |
| Complete 80-sample PCM blocks | 64 |
| DMA half-buffer IRQ count | 6,826 |
| Average / peak ISR | 2,222 / 2,363 nominal 96-MHz ticks (~23.15 / 24.61µs) |
| Estimated half-buffer service deadline | ~94.3 µs |
| Missed halves / clipped samples / IRQ errors | 0 / 0 / 0 |
| Peak PCM queue depth | 1 of 4 blocks |
| PCM peak / RMS | 16,632 / ~8,832 |

**Important limit:** ~24.6µs under mocked foreground work does not bound an interrupt blocked by `PRIMASK`, the Sotto encoder's SPI codebook access, or real RFIC transmit scheduling. The exact simultaneous-encoder ISR latency still needs physical instrumentation. The ISR must never access SPI, UART, LCD, flash or module callbacks. The result was obtained with RF PA **off**, i.e., mic bias under a TX lease but **not an RF transmission**. Source: [DMA IRQ implementation, physical evidence and size gate](k6-sotto-gp0-dma-irq-20261125.md).

## 4. On-radio Sotto codec, RF framing and TX FIFO

### 4.1 Framing, codec and resources

Use pinned upstream Sotto revision `598f1bce87c22ebada2b9d0d1fcfd89107b33a09` for the captured qualification. The goal uses **Sotto-1200**, 8kHz signed PCM and 6-byte compressed frames grouped in guarded **FDV1** BEGIN → VOICE → END sessions, carried by 66-byte 4,800-baud native 2FSK bursts and 33 16-bit FIFO words (including the 2-byte guard). Preamble is 16×`0x55` plus public four-byte sync, followed by PN9/CRC-checked frame. The typed codebook **K6CB** and Sotto data/deck resources **K6SC** are held in the SPI library; validate resource resolution, CRC and availability, rather than assuming a generic installer's 600/400 profile warning means Sotto1200 is missing.

The Base/Data API evolution is tracked in [API31 receiver development](k6-sotto-api31-rx-development-20261124.md): API26 mode registry; API27 mailbox; API28 typed SPI resources; API29 PA-muted async finite FSK TX; API30 microphone service; API31 native 4,800 RX. New builds should drop compatibility baggage only with explicit test evidence; the experimental branches still mix API30/31 consumers, and an API31 *number* alone does not guarantee correct behavior for older modules.

### 4.2 Physical TX proof and PA lifecycle

An initial API31 known-packet test at 433.500MHz proved a launched module and actual FSK energy, but it produced **two CRC-rejected tails** and no valid packet. Later investigation established that the live SDR application had a **128ms USB-read boundary bug**: old raw IQ decoded **8/8** offline, while live monitor originally reported **0/8**. A dedicated USB reader thread and a bounded 24-block queue fixed this, recovering **8/8** with no overflow. The boundary bug and actual transmitter PA sequencing must be evaluated separately.

The bounded **on-radio synthesized-tone** test then qualified a direct register sequence: one TX lease, **PA enabled while preloading FIFO**, 20ms PA/RFIC settling before each frame, PA kept on across the three-frame session, **zero interframe gap**, then PA off/restoration. Result: **6/6 complete sessions, 18/18 CRC-valid packets, 0 rejected, 0 sync errors**. The recovered 320ms/2,560-sample decoded PCM matched the independent reference. Cooperative cancellation during encode and during RF returned to RX with PA de-keyed; subsequent packets still passed. These results are for the explicitly tested direct-register sequence and synthesized voice, **not** proof that production `API29 fsk_async` (which mutes PA on preload) is equivalent. See [RF qualification](k6-sotto-rf-qualification-goal-20261125.md), [known packet predecessor](k6-sotto-api31-physical-known-packet-20261125.md), [live-serial bench](k6-sotto-live-serial-bench-20261125.md).

### 4.3 Physical user-microphone Sotto RF: packet pass, audio fail

A slot30 SOTTO MIC session, operator-held PTT, on dummy load and independently monitored SDR at **443.500 MHz**, yielded **1 BEGIN, 26 VOICE, 1 END; 28 CRC-valid, 0 CRC-rejected, 205 six-byte codec frames**. A mistaken monitor tune of 433.500 MHz initially hid these packets. The decoded 8-kHz waveform comprised **65,600 samples (~8.2s)** but just **639 nonzero samples**, all within the first ~320ms. Of the 205 compressed frames, **201 were two alternating codewords**—near-silence rather than speech. RF packet success was genuine; live audio was not. This prompted PA11 timing/calibration investigations and ultimately the GP0 route work. Details and alternate module hashes: [physical SOTTO MIC and calibration handoff](k6-sotto-mic-e2e-handoff-20261125.md).

### 4.4 FIFO risks still open

Finite 33-word FSK bursts can fail from an early TX-done indication, partial FIFO occupancy, PA off before the last bit, lost TX-ready, interframe reset or SDR acquisition. Native API29 mock fault tests showed **success** can be returned on injected early `0x0c` TX-done while an on-air guard/CRC tail is incomplete; this is **hypothetical IRQ semantics**, not a measurement of that exact physical behavior. Avoid interpreting module result=1 as an RF-delivery receipt. Physical FD6818B IRQ flags and FIFO refill behavior need instrumented timing correlated with SDR IQ. Long term, streaming speech can be implemented as bounded back-to-back packets with a sustained PA lease, not by assuming the stock FIFO streams indefinitely. Sources: [native callback fault cases](k6-sotto-api29-native-mock-20261125.md), [blind start and FIFO replay](k6-frequency-burst-fifo-20261124.md), [burst rearm / clock recovery / parity](k6-native-fsk-burst-clock-parity-20261124.md).

## 5. RX and speaker path: software proof ≠ physical installation

An opt-in compiled **API31** callback can configure the 4,800-baud 2FSK receiver, drain/rearm the **33-word FIFO** into a bounded 66-byte scratch packet, and safely stop/restore registers under fault, PTT, timeout and user abort. Operations are conceptually `fsk4800_rx(op=0)` start, `op=1` bounded poll/drain and `op=2` stop/restore. A native Rust callback test uses the *actual included callback source*, not only a behavioral rewrite. ARM firmware/module emulator and SoapyFang-derived virtual IQ have recovered **six CRC-valid packets** and guarded interruption paths. The source and host/module boundaries have tests for RX ownership, low-active PTT, malformed pointers, deadline and FIFO threshold events. **No confirmed on-radio RFIC 4800-FSK receive plus live Sotto decoder speaker playback yet.**

The incremental Sotto decoder uses pinned native C and 20ms speaker blocks, bounding explicit queued storage to **4 PCM blocks = 1,280B** plus **16 compressed frames = 96B** in the model (excluding output scratch, decoder state, overhead). This is a host-implementation estimate, **not a validated K6 SRAM/speaker deadline**. Outstanding: microphone-to-RX handoff, 20ms speaker scheduling, real DAC/PWM, tail loss concealment, packet CRC rejection, preempted playback, rearm timing and coexistence with flash/cable VFO UI. See [API31 RX development](k6-sotto-api31-rx-development-20261124.md), [virtual link](k6-sotto-virtual-link-20261124.md), [SoapyFang virtual voice](k6-soapyfang-sotto-lab-20261124.md), [flashable gates](k6-sotto-two-way-flashable-gates-20261124.md).

## 6. Memory / firmware ABI and module packaging

The MCU application limit is **122,880 bytes**. The expanded movable module arena is **14,976 bytes**, protected RAM0 **3,840 bytes**, with independently audited main stack reservation **≥2,780 bytes** in the experimental profile. The earlier GP0 IRQ module + full native encoder **did not fit** when simply concatenated: measured **~1.2 KiB arena shortfall** with the 9,780B SOTTO MIC plus DMA/PCM/interrupt state. Do **not** solve this by silently cutting the physically proven 1024B DMA ring or the 4×80-sample PCM queue; this would tighten IRQ latency and risk regressions.

Strategy under investigation: put the IRQ/decimator service into a firmware resident **API30** callable (frees module PIC code but increases core flash and protected state), retain small PIC TX module containing codec/wire/FIFO, share encoder/decoder scratch only while mutually exclusive, page cold code via system SPI and trim obsolete compatibility APIs. The experimental firmware-owned implementation was compiled, but subsequent boots and live serial are not fully qualified; the first attempt used all RAM0, a 28-byte reduction did not cure splash hanging, and the boot-phase image with **3,828/3,840 RAM0** reached VFO while serial remained silent. A further diagnostic image with **3,836/3,840 RAM0** and IRQ/RX LCD counters was successfully built (121,644 bytes, tests 31 passed/3 skipped) but **not flashed** as of this record (stock bootloader did not answer command 0x01; no bytes transferred). Source: [GP0 ownership/memory](k6-sotto-gp0-dma-irq-20261125.md), [phase boot investigation](k6-gp0-startup-phase-diagnostic-20261125.md), [UART probe](k6-gp0-uart-probe-20261125.md).

The known-good radio firmware is `b6a3df0d360d3110`. After the last successful diagnostic flash, the physical VFO rendered but serial status timed out. Maintain full SPI backup, do not erase user/library banks or copy a new codebook without preflight hashes. The programmable UART cable and physical mic/PTT share hardware constraints; module start with cable attached has returned **-4** intentionally. The desired **mic→MCU→RF** mode must work stand-alone with the cable disconnected, while debug results are stored in volatile boot-local mailbox for readback after reconnection *without rebooting*.

## 7. Debug / storage / test infrastructure

- **Serial API**: `tools/k6_live.py` (`caps`, `status`, `mic-result`, `run-coop`, `screenshot`, `vfo`, `ptt-burst`, optional `ram-status`); `tools/k6_install.py` only for explicit firmware flash, SPI backup, signed library changes. Production live control expects compatible firmware handshake at 115200baud; 500kbaud was used for selected reads, not a blanket host default. The last physical image currently does not answer read-only `caps` at 115200/192000/500000.
- **Boot-local microphone telemetry**: `MAB1` version8 PCM-success / version9 errors; version10 IRQ PCM success / version11 errors. CRC16/XMODEM mailbox via live opcode `0x86`. Return to VFO standby before reading; a Catalog/menu `BUSY` is not evidence of stuck RF. Read results *before power cycle* or lost on reboot. Code: `tools/k6_mic_result.py`.
- **Bench**: claim `tools/k6_bench.py` lock before experiments; confirm dummy load, live power >7V, PTT=false, frequency, module hashes and serial ownership. Restore slots and release lock; all destructive flashing requires stock UPDATE mode. Don't use the cable for physical mic PTT test when the port conflicts.
- **ARM gates**: `tools/k6_sotto_gp0_irq_arm_gate.py` tests actual Thumb PIC ISR; `tools/k6_sotto_gp0_route_arm_gate.py`, `tools/k6_sotto_api29_async_gate.py`, `tools/k6_sotto_live_arm_gate.py`, `tools/k6_sotto_rx4800_firmware_gate.py`, `tools/k6_sotto_rx4800_dispatch_gate.py`, `tools/k6_sotto_firmware_audit.py` verify the respective code paths/space; see their corresponding tests.
- **RF replay**: `tools/k6_sotto_rf_goal_bench.py`, `tools/k6_sotto_pcm_rf_build.py`, `tools/k6_fsk_emulator_replay.py`, `tools/k6_sotto_virtual_link.py`, SoapyFang virtual IQ with independently recovered packet CRC. Do not count offline known-payload matching as blind RF receive; don't leak expected payload into demod algorithm.
- **Reference source/bundles**: `firmware/k6-aura/sotto-api28-resources.rs.inc`, `sotto-api29-async-fsk.rs.inc`, `sotto-api30-mic.rs.inc`, `sotto-api30-gp0-capture.rs`, `sotto-fsk4800-rx.rs.inc`; module C in `c/k6/mod/`; `tools/k6_sotto_module.py`, `tools/k6_build.py`, `tools/k6_sotto_pcm_rf_build.py`. Full file manifests accompany this document.

### Local, *not automatically checked into public Git* evidence

| Item | Workstation evidence path / identity |
| --- | --- |
| Physical GP0 PCM 1kHz, 2kHz, generator-OFF JSON | `/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-pcm-v1/` |
| Physical continuous GP0 IRQ v10 mailbox | `/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-irq-v1/tone-2khz-01.json` (SHA256 `158dcc4928f3eb8b2e41c171275828b93c54adc5d05454bc5ef8c0cf37363259`) |
| Known-good recovery image | `/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-dma-v1/flux-gp0-mic-route-only.bin` (SHA256 `751995d2dd346a2cfbf7503f8b4d722076cec9164a61efa495b3ca884dc67ccb`) |
| Full 2MiB radio SPI backup | `/storage5/dev/flux-sotto-gp0-ptt-optimized-release-20261125/preflash/k6-spi-before-gp0.bin` (SHA256 `ec856e231599ef83734f436560380470aaf06ab3c9effe7aaff49d237cebbeee`); may include private device calibration/library |
| Physical 4800-FSK known-packet tests | `/storage5/dev/sotto-monitor/evidence/bench-api31-20261125/` |
| Qualified continuous-PA synthesized-PCM RF sessions | `/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125/` |
| Diagnostic UART image built, **not flashed** | `/storage5/dev/flux-gp0-phase-probe-20261125-release/flux-fd6818b-gp0-uart-probe.bin` SHA256 `bc063e85bf59cfb4419b98e155b4ae8431fbbd944efc455e34fa7eeca2e2f4c2` |

Eight measurement-only GP0 tone/control/IRQ JSON reports are now [checked in with original SHA256 checksums](evidence/k6-sotto-20261125/README.md), separate from sensitive raw captures. These paths identify the remaining retained local evidence, **not public download URLs**. Full SPI dumps and raw microphone/IQ audio have not been published here because they may contain calibration and user/private radio data and large binary samples. The existing checked-in docs describe the measurements and tests; checksums allow reproducibility without embedding those assets.

## 8. Proposed acceptance plan, in dependency order

1. **Restore trustworthy UART observability.** In stock UPDATE mode test the bounded `IRQ YES/NO` and `RX YES/NO` image (do not assume `UPDATE` from cable path alone). Confirm main VFO and host command bytes. Recover known-good base if diagnostic UART stays silent; verify exact build ID.
2. **Finish firmware-owned GP0 producer with code + RAM headroom.** Include strict RAM0/stack audit, vector install/restore, failure exit paths and UART mic-result logging. Confirm GP0 signal still has tone-on/off frequency-dependent energy, not startup transients. Re-run physical ISR with actual encoder SPI and synthetic FIFO servicing as real concurrent load.
3. **Fit a native live TX module** with actual Sotto encoder and GP0 PCM ring inside real signed library/arena boundaries (no 1.2KiB gap). Run linked ARM and SoapyFang PCM→codec→FDV1→FIFO IQ→independent decoder tests, including overflow, zero/multiple PTT, release/flush, cancellation and stuck PA. Preserve analog FM fallback.
4. **Test TX on dummy load under attended SDR observation.** Correlate generator ON/OFF and actual speech with decoded WAV, not just CRC-valid VOICE. Verify no clipping, 20ms codec pacing, PA de-key/restore and no FIFO IRQ termination issue. Prefer distinct SOTTO GP0 module name/slot to avoid duplicate Catalog entries. Keep normal mic/cable unplugged during PTT.
5. **Implement/measure native RX** in the same flash image: real FD6818B 4,800-FSK FIFO thresholds, framing CRC, session timeout/rearm, decoder/codebook and 20ms speaker output. Observe actual incoming RF with a second known transmitter/fixture or sufficiently controlled test; SoapyFang alone cannot sign this off.
6. **Close half-duplex transition under hardware.** RX→PTT TX→finish/abort→RX, repeated indefinitely, battery/power-save/scan cases, menu/install interactions, corrupted packet loss and watchdog power-down. Demand validated on-radio voice both directions without the PC providing PCM, IQ or timing.
7. **Only then release.** Freeze exact hashes for firmware/modules/codebooks, flash/arena/IRQ/stack bounds, serial build ID, regression/replay fixtures and operator-visible smoke instructions; merge reviewed implementation separately from this evidence dossier.

## 9. Important engineering footguns

- Sotto Monitor must track actual VFO A, not its default (one 443.500MHz vs 433.500MHz mismatch produced a false negative). SDR center frequency and digital offset are separate.
- The monitor's old interleaved USB read/FFT decode corrupted apparently good packet tails every ~128ms; capture raw IQ and replay before changing RFIC register sequences.
- PA must remain stable over the finite on-air Sotto session in the **qualified** TX primitive; API29 PA-muted FIFO preload is not equivalent to proven direct-register PA-on preload.
- ISR throughput measured without actual SPI codec calls is a feasibility bound only; measure worst-case disabled interrupts and DMA half-overrun under full load.
- Reaching VFO on the phase diagnostic does *not* mean live serial handshake, catalog installation or microphone interrupt works. Boot phase markers can be transient and overwritten by normal UI.
- An early GP0 firmware build with no RAM0 headroom hung on splash; the 28-byte trim alone did not fix it. The observed successful VFO boot used different diagnostic code and memory layout, so don't claim a proven root-cause fix.
- As-built half-duplex design allows reuse of encoder/decoder scratch, but ROM/firmware flash and hardware TX/RX ownership still have separate limits.

## 10. Historical evidence and source index

This document is a front door to the **complete source-tracked evidence set**, not a replacement for the detailed reports. For up-to-date, exhaustive filename inventory and single-click relative links, see [Sotto/GPO evidence index](K6_SOTTO_EVIDENCE_INDEX.md). When an experimental branch or workstation-only artifact differs from main, use explicit SHA/build ID above and link to the branch; never pretend the working tree equals a release.
