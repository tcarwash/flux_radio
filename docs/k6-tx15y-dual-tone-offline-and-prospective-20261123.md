# TX-15Y — B-chip second-tone / DTMF FM vector: offline source and emulator qualification

**Checkpoint:** 2026-11-23 14:10 Pacific. **Queue:** [Flux #16](https://github.com/tcarwash/flux_radio/issues/16), unranked/offline only, coordinated through [#11](https://github.com/tcarwash/flux_radio/issues/11). **Source branch:** `research/tx15y-dual-tone-offline-20261123`; separate worktree `/storage5/dev/flux-tx15y-dual-tone-offline`. **Bench lease:** NONE; another agent has a pending/exclusive request for the higher-priority FSK-09S-G/FIFO work. **No serial port, SDR, hardware key-up, RF, SPI, or firmware flashing in this TX-15Y study.**

## Question and evidence

Can the FD6818B/BK4829 B-chip independently generate TWO software-controlled FM audio tones inside a continuous TX burst, without requiring external microphone input? If so, could one or both be gated by REG70 at a useful update rate? This would add a second source oscillator for constrained audio-driven FM and subsequent relative RF-carrier-phase research, **not** independently programmable RF I/Q.

**Source-supported candidate:** The B-specific Aura driver (generated at `firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs`) explicitly states:
- `REG_3F = 0x0800` enters DTMF work mode; the same register uses `0x2000` for native-FSK RX and `0x8000` for FSK TX interrupt mode.
- `REG_71` drives tone 1, while `REG_72` is **tone 2 frequency in DTMF mode** and **FSK baud in the FSK work mode**. Do not write 0x72 speculatively when FSK hardware owns the modem.
- `REG_70` enables tone1 using bit15, its gain in bits14..8; tone2 uses bit7, its gain in bits6..0. Driver `AFSK_REG70_MARK=0x8000|(60<<8)` and `AFSK_REG70_SPACE=0x0080|60` mirror this.
- The B driver `enter_dtmf_mode(tx=true)` writes `REG_24` DTMF detector configuration, the dual-tone gain `0xE0E0`, and `REG_3F=0x0800`, while `set_dtmf_digit` writes both REG71 and REG72 frequency words. These source routines are evidence of **intended register semantics**, NOT physical validation of the exact test radio.
- A previous `tonewr` K9 physical recording (in `docs/k6-range.md`) found that REG72 tone2 appeared **silent** when only the tone2 enable bit was used. The test did **not** establish that the proper B-chip DTMF work mode was selected. Therefore this is a useful **negative prior** to distinguish, not a conclusive B-chip impossibility result. Older-family observations are not automatically equivalent to FD6818B.

This test is independent of physically verified REG71 tone-frequency, REG70 gating and gain (TX-15U/V/W/X) and of TX-17C's negative active-TX direct synthesizer frequency-word test.

## Independent radio-inert candidate code

`c/k6/mod/tx15y_dual_tone_probe.c` builds **inert by default** (`TX15Y_BENCH_ENABLE=0`). Two opt-in **emulator-only**, then prospective guarded physical variants: optional DTMF detector setup off (`TX15Y_DTMF_DETECT_SETUP=0`) or matching source driver `REG_24=0xC17F` (`...=1`). Both must be distinguished because `REG24` may affect decoder state but need not be necessary to transmit tones. Do not infer one variant will work from the other's source.

The source snapshots original `REG_3F, 0x70, 0x71, 0x72` (and REG24 when modified). It checks the work mode is **zero after normal TXON**; if nonzero it refuses before ANY RFIC writes. Only after confirming that no FSK work mode is active does it configure tone1 REG71. It writes REG72 **ONLY AFTER switching to and verifying DTMF REG3F=0800**. REG70 controls exactly nine predeclared intervals while the PA remains keyed in a future authorized RF run; the source restores gain, tone words, work mode, optional detector configuration, and calls TXOFF on success/abort. It does not access REG59/5F/FIFO.

| Interval index | Duration | Mode/source state | Role |
|---|---:|---|---|
| 0 | 120 ms | non-DTMF tone1, 782 Hz | training pilot |
| 1 | 120 ms | DTMF tone1, 782 Hz | training pilot / mode control |
| 2 | 120 ms | tone2 only, 1424 Hz | heldout |
| 3 | 120 ms | both 782 + 1424 Hz | heldout |
| 4 | 80 ms | both OFF | heldout leakage control |
| 5 | 120 ms | tone2 only repeated | heldout stability |
| 6 | 120 ms | tone1 only repeated | heldout source specificity |
| 7 | 120 ms | both repeated | heldout summed-tone specificity |
| 8 | 90 ms | both OFF / shutdown guard | heldout silence, no terminal-tail scoring |

Tone1 word **8074**, tone2 word **14702**, REG70 tone1 **0x9800**, tone2 **0x0098**, both **0x9898**, OFF zero. Total patterned duration 1.01 s plus 70 ms setup lead and fixed checks; firmware TX lease **1800 ms**, lab frequency **433.500 MHz**. PA-on compilation requires four separate source flags for bench enable, RF enable, operator dummy-load acknowledgment and watchdog acknowledgment. PA-off still permits RF leakage; this is NOT a no-RF guarantee.

Emulated compiled Cortex-M0 versions (exact source binary SHA256 after final safety edit):

| DTMF detector setup | PA | Code size | SHA256 |
|---|---|---:|---|
| 0 | off | 936 B | `70f4ca2984afec538bf86e3f68dd312fcce90778c4877d0fe29dcdc6d99c97c6` |
| 0 | on | 948 B | `76c68cc62946a0ff92174b8f0c6cf973dcb14cb6aa8cb84995bcc60dd74b34e8` |
| 1 | off | 1016 B | `ac11574c78145335329b1bee4e35ba2e0237dab298cf38df1cacc4b0fe24ccd8` |
| 1 | on | 1028 B | `661a49bbc53f62948a7eecfd8dc01c9eeaad78e199b908ceb8d1282f5ab881f0` |

**These hashes are offline build provenance, not permission to transmit.** They are not verified against physical firmware/module API, nor do they establish RF compliance.

## Predeclared receiver analysis and falsification

`tools/k6_tx15y_dual_tone_analyze.py` is READ ONLY. It accepts archived raw RTL-SDR complex-IQ samples and uses the existing Flux RF onset/frequency-discriminator path to obtain received instantaneous-frequency deviation. Tone1 pilot windows 0 and 1 each fit their own phase and DC while learning one common tone1 frequency within ±3 Hz; their tone1 amplitudes must each be ≥300 Hz. Tone2 analysis frequency follows the exact previously used register-word ratio multiplied by the pilot-derived relative clock rate.

All heldout segments 2..8 are evaluated with a fixed two-tone sine/cosine + DC basis and **18-ms settled interval guard**. The model requires:
- Tone2 on both isolated tone2 intervals ≥20% tone1 pilot amplitude and unwanted tone1 ≤20%.
- Both frequencies independently ≥20% in each dual-tone interval, with a repeatable tone1-only state (≥60% tone1, tone2 ≤20%).
- Both tone generators OFF have each tone ≤20% pilot reference.
- Repeated tone2 amplitude agrees within 25%; no false tone2 energy >20% in pilot 0/1.

The independent receiver tests supply a synthetic dual-tone FM signal and negative controls for missing tone2, incorrect tone1-instead-of-tone2, absent sum, leakage, failing DTMF mode and gain instability. **They test the scorer only and are not RF data.**

`tests/test_k6_tx15y_dual_tone_probe.py` uses Cortex-M0 Unicorn simulation to check opt-in PA-off/on builds, exact state/word ordering, the "never write REG72 in FSK mode" guard, full snapshot/restore including key abort, no writes on conflicting work mode, chip/frequency guard, compile-time RF-ACK rejection and invalid variants. **30 new tests passed; no physical B-chip dual-tone TX claim.**

## Physical qualification gate — do NOT auto-run

Before any physical test of this source, the responsible agent must:
1. Reread **#11, #9 and #15** newest comments; honor the exclusive FSK-09S-G FIFO user's bench request and other active claims. Obtain **explicit operator-recognized hardware lease ACK** and only then acquire the canonical Flux `serial_lock('/dev/ttyUSB0')` nonblocking for the **whole** module install, SDR capture, single bounded TX and verified rollback. `tools/k6_bench.py` is for the IC-7100 and **is not** the Flux hardware mutex. The queue dashboard's planned manual Claim control is not yet implemented.
2. Freeze and commit an **exact-hash-matched one-shot RF harness** and physical protocol, with expected B-chip ID **0x4829**, exact firmware/build, trusted original signed slot30 executable and VFOs, channel occupancy and SDR baseline check, saved serial watermark/slot envelope, no auto-retry, timeout and PA-off/readback preflight. Restore REG24/3F/70/71/72, module slot and both original VFOs after every attempt. No permanent flash firmware changes or calibration writes.
3. Emit *at most* one source-controlled candidate variant per lease-approved trial under the confirmed dummy-load / air-coupled SDR fixture. Keep raw unaltered IQ, SHA256, source executable SHA, complete radio/SDR transaction receipt and verification, analyzer JSON, good or negative outcome. Do NOT run even PA-off while another FIFO agent is using REG72/FSK.
4. Only after controlled, independent RF emissions with a frozen pilot/holdout scorer may this path be labeled B-chip **RF-positive**; otherwise record the negative outcome. This investigation does not establish phase continuity, update rate, spectral mask or arbitrary RF-IQ.

## Other modulation routes retained

- **TX-15Z**: source-selected differential phase alphabet based on TX15U, with a separate ongoing candidate search **in someone else's worktree**; do not modify that uncommitted research.
- **TX-15X**: already physically evaluated fast REG70 gain dynamics (read current report before repeating); a second oscillator could complement fast gain and free-running output gating.
- **REG40**: FM deviation scalar, independent from REG70 tone-gain, remains a B-chip unisolated vector and needs a separate source audit.
- **TX-16**: CTCSS/DCS near-sub-audio FM source; separate active work.
- **TX-17D**: direct synthesizer word with explicit PLL latch/state reentry, following TX17C's negative direct live write.
- **TX-20/21**: APC envelope and eventual phase+amplitude polar combination; no physical simultaneous IQ claim.
- **TX-18/23**: possible B-specific digital baseband or external coherent I/Q daughterboard for real arbitrary RF waveform requirements.

**Decision:** Source evidence makes DTMF-mode tone2 a credible *specific* second-oscillator RF modulation candidate. The old negative audio result requires re-testing the **proper B work-mode**, but test ownership and bench permission take precedence. Current status **OFFLINE READY; PHYSICAL EFFECT UNPROVEN**.
