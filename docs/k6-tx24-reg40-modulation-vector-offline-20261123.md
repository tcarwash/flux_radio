# TX-24 / FSK-03C — FD6818B REG40 deviation modulation: offline investigation

**Checkpoint:** 2026-11-23 Pacific afternoon. **Queue:** [#17](https://github.com/tcarwash/flux_radio/issues/17), shared coordination [#11](https://github.com/tcarwash/flux_radio/issues/11). **Status: OFFLINE SOURCE / EMULATOR / SYNTHETIC SCORER DONE; NO B-CHIP RF PROOF.** Unranked, subordinate to P0 FIFO #9, P1 Sotto #10, P2 microphone #12 and ongoing root-cause #15. **Hardware:** no lease; no serial port, SDR, TX, SPI, calibration flash or radio reset. Branch research/tx24-reg40-deviation-offline-20261123, isolated worktree /storage5/dev/flux-tx24-reg40-deviation-offline.

## Independent modulation lead

The existing K6 native binary-FSK path and REG70 tone-gain FM-deviation actuator have separate real RF evidence. The independent 16-bit **REG40** control was deliberately held fixed in those experiments.

- The B-specific generated Aura driver defines **REG_DEVIATION = 0x40** and **DEVIATION_VALUE = 0x3516**, which the driver writes during initialization. Its voice-scrambler operation also writes REG40's lower 12 bits as 0x0450 while preserving the upper nibble. That establishes a supported source path for another programmed deviation configuration; it does **not** establish B physical scale, timing or live TX behavior.
- Previous read-only K6 B-chip hardware fingerprint in docs/k6-micin-bypass-lab.md observed 0x3516 on the exact 0x4829 chip.
- Older FD6818 / BK4819 family guide lists deviation enable/shift/level fields, roughly proportional to (256 + lower_byte) shifted by a field. **Do not assume its bit map or transfer law applies identically to B silicon**.
- [BK4829 functional block diagram](https://manuals.plus/m/b03fb6dba016158ab9f4b5b3d1aeb1d9ea178efc44cc11c2c08398621553cdc9.pdf) places FM modulation inside the RF synthesizer. This is compatible with a gain stage, **not a general signed instantaneous RF-offset DAC**.
- Existing FSK-03 roadmap in docs/k6-fsk-02-archived-iq.md and docs/k6-tx-next-experiments.md already mentions REG40. TX-24 narrows this into a separately qualified **tone-only first** experiment, preserving the higher-ranked FIFO work. No duplicate physical test performed.

## Default-inert candidate source

File: c/k6/mod/tx24_reg40_tone_probe.c. Compiles inert by default (TX24_BENCH_ENABLE=0). Future physical build requires separately authorized, full dummy-load/SDR lease and compiled fixture/watchdog/RF acknowledgments. A hardcoded 433.500-MHz guard, B chip ID 0x4829, clear-channel check, original REG40 value = 0x3516 check, 1800-ms firmware TX lease, 80-ms source lead and 85-ms non-scored terminal tone OFF guard constrain it.

The waveform uses a **single unchanged REG71 tone word 11264 (~1091 Hz)** and **REG70 gain24 = 0x9800**. It only changes REG40's low eight bits while holding its upper byte 0x35 constant. No REG59/5F/72 FIFO work mode, APC REG36 or direct PLL frequency write. Every register write is read back. Original REG40/70/71 values are restored on success and key-abort; a non-default initial REG40 refuses with **zero modulation register writes**.

| Slot | REG40 word | Use |
|---:|---|---|
| 0 | 0x3516 | Training reference |
| 1 | 0x3540 | Heldout |
| 2 | 0x3516 | Training reference |
| 3 | 0x3580 | Heldout |
| 4 | 0x3516 | Heldout reference |
| 5 | 0x3500 | Heldout |
| 6 | 0x3516 | Heldout reference |
| 7 | 0x3540 | Heldout replay |
| 8 | 0x3516 | Heldout reference |
| 9 | 0x3580 | Heldout replay |
| 10 | 0x3516 | Heldout reference |
| 11 | 0x3500 | Heldout replay |
| 12 | 0x3516 | Heldout terminal reference |

Each slot lasts 90 ms. Slots 0/2 are the **only pilot-training slots**; all additional data is holdout. The upper byte remains 0x35 and lower-byte steps 0x00, 0x16, 0x40, 0x80 are hypotheses, not a calibrated B transfer law.

**Offline compiled code SHA256 (source variants, NOT RF emissions):**

| Code | Bytes | SHA256 |
|---|---:|---|
| Inert default | 92 | 21218e6ba3d99727cfd7aeb545f9a9df243d7a4c66b425901c52e94fcca6a36a |
| PA-off diagnostic (still RF leakage possible) | 800 | 3d0776a621dc3ca76fa3efbaa1225082caa66a2aaffc3b448ea62cbaf61f2ea3 |
| PA-on future candidate | 808 | e792a8bd77d2f055a16f9e1da025a4f7a4555b6dbb9d3417817612e5e21024f0 |

These compiled artifacts were tested **only in a simulated Cortex-M0**. There is no hardware approval or device access implied.

## Read-only analysis and physical acceptance gates

File: tools/k6_tx24_reg40_deviation_analyze.py. It reads archived complex SDR IQ, uses existing Flux receive-FM-discriminator extraction and fits only slots 0/2 to learn common tone frequency, phase and deviation amplitude. The frequency fit searches nominal ±4 Hz with a deterministic coarse grid then local refinement, because separated pilot windows produce multiple frequency-fit lobes. The two pilot slots may have separate receiver DC baselines. Every later interval is evaluated with the frozen pilot frequency and source phase, after a fixed 19-ms margin. It reports per-slot audio-FM deviation, phase error, residual RMS and receiver carrier/DC offset.

**Predeclared candidate qualification**, which B chip may fail: REG40 low-byte 00 < 16 < 40 < 80 in measured deviation, adjacent normalized separation ≥3.5%; repeated 0x16 reference max-min variation ≤15%, heldout phase RMS ≤18°; both repeated nonbaseline levels stable within 15%; and settled candidate amplitude/residual-noise ratio ≥4. The scorer publishes all measurements even if qualification fails. A nonlinear but useful effect or flat/no-effect result is not hidden.

**24 focused tests passed**: 12 Cortex-M0 source/emulator checks including default inert, strict hardware guards, exact 13-step write order, no unrelated register writes, abort and full restore; and 12 synthetic receiver tests with positive four-level FM amplitudes, negative ignored/reversed/stale/phase-jump/noisy sources, pilot-holdout independence and input validation. **Synthetic results are not physical measurements.** They do not prove any RF power, spectrum, IF/Q EVM, deviation bandwidth or native 4FSK.

## Future physical gate — never automatically run from this issue

Coordinate a distinct explicit K6/SDR bench lease via #11 and check fresh #9/#10/#15/#16 comments before requesting it. After confirmed operator acknowledgement, acquire the canonical **nonblocking Flux serial_lock('/dev/ttyUSB0')** throughout the entire identity/preflight, signed original slot/VFO backup, single dummy-load TX plus independent SDR IQ, raw evidence SHA, and readback-restoration transaction. A GitHub CLAIM is not a mutex. Precommit hash-pinned one-shot harness and physical protocol, confirm original firmware and RFIC identity, no firmware/calibration flash writes, respect both currently watched VFOs even if their state differs from earlier trials, never automatic retry. PA-off is not RF-off. Preserve negative results.

**Only after first separate REG40 tone-FM qualification** should we propose a second, independent native FSK test changing REG40 in long known-symbol intervals. FIFO studies currently observe unexpected leading **0x00F8** and failed Sotto CRC. Do not conflate the current FIFO corruption with REG40 effect, and do not promise continuous 4FSK, 2 bits per symbol, QPSK, QAM or arbitrary IQ. Four nominal sign×gain combinations require **four actually measured independent RF frequency levels** and accurate timing/spectral qualification.

## Additional source-backed, lower-priority modulation controls from the same audit

- **TX-25A, TX audio response coefficients:** B driver defines REG_AF_TX_3K = 0x74 and REG_AF_TX_300_D1/D2 = 0x44 / 0x45, with eight documented coefficient presets each exposed by set_af_response(tx=true,...). These are programmable filter-shaping controls and could improve distortion/occupied spectrum for a future electrically qualified MICIN shaped 4FSK waveform. **Not a new source of samples**; duplicates some goals of existing TX-12 MICIN work, so don't create a competing hardware task. No real TX filter response measured here.
- **TX-25B, voice-inversion oscillator:** B driver set_scramble() writes REG_SCRAMBLE = 0x31 bit1, selects one of three REG71 oscillator frequency words (0x8517, 0x770E, 0x7A90), and changes REG40 lower deviation field to 0x0450. This is a possible analog-mixing/inversion actuator **only if suitable voice audio exists** and likely shares/conflicts with TX-15 frequency control; not a separate arbitrary signed FM port. Lower priority than REG40 direct scale and clean MICIN.
- **RX/USB/AM routing:** B REG3D zero-IF, REG47 AFOUT, and REG73 AFC are receiver/IF controls; no grounded evidence they are independent MCU-to-TX I/Q sample interfaces. Don't confuse them with a transmitter baseband DAC.

**Decision:** the confirmed source-backed **REG40 candidate** merits isolated physical calibration when queue resources permit; TX-25A audio response coefficients are a secondary conditional tool for TX-12; TX-25B scrambling is low-confidence for arbitrary waveform synthesis. No shared hardware, queue priorities, or other agents' active worktrees were changed.
