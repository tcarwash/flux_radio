# Autonomous synthetic-microphone Sotto RF experiments — 2026-11-26

**Status: 16 complete software-only, virtual-RF runs, 7 accepted; no physical radio transmission.**

The runnable tool is [`tools/k6_sotto_autonomous_pcm_matrix.py`](../tools/k6_sotto_autonomous_pcm_matrix.py). It injects deterministic, local 8 kHz signed 16-bit PCM after the modeled microphone ADC/decimator boundary, directly into the Sotto GP0 four-block PCM producer/consumer ring. The encoder is the *real compiled Cortex-M0 ARM PIC module*, using pinned Sotto codebook resources; the FSK channel and microphone interrupt timing are virtual and the resulting frames are checked by the compiled native receiver and independent reference codec.

Run without radio access, a programming cable, operator PTT, or an RF transmitter:

```sh
python -m tools.k6_sotto_autonomous_pcm_matrix --out /tmp/flux-sotto-autonomous-test
```

The saved machine-readable [matrix JSON](evidence/sotto-autonomous-virtual-pcm-matrix-20261126.json) contains waveform, module checksum, PCM progress, queue high-water, codec correctness and TX dekey per trial. The unsigned **optimized four-pass Sotto GP0 module** is the same 9,784-byte image already staged offline, SHA256 `9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0`; it was **not installed**.

## Experimental knobs

Each waveform contains 8,293 samples (1.036625 s): silence, 320+870 Hz multitone, a 250→2850 Hz chirp, and alternating 1,040 Hz bursts. Compare the **original** GP0 module against the four-opt optimized module (fast division, history copy, paired codebook scores, paired lag scores). Model the installed cooperative FSK preload (`fsk_cooperative_model=True`), codebook SPI access ~6.19 µs/call + 0.94 µs/byte, RFIC access 60 µs, ADC IRQ cost 4,000 emulated cycles and CPU cost multiplier 2.5 (high load) or 3.0 (stress). These are simulator calibration assumptions, not direct live MCU cycle readings.

| PCM input | Original @ 2.5 CPI | Optimized @ 2.5 CPI | Original @ 3.0 CPI | Optimized @ 3.0 CPI |
|---|---|---|---|---|
| Silence | **Complete** | **Complete** | **Complete** | **Complete** |
| Multitone | Overflow at 2,000 samples | **Complete** | Overflow at 1,040 | Overflow at 5,440 |
| Chirp | Overflow at 1,600 | **Complete** | Overflow at 1,040 | Overflow at 7,360 |
| Bursts | Overflow at 2,240 | **Complete** | Overflow at 1,040 | Overflow at 6,160 |

Every completed run consumed all **8,293 samples**, emitted **26 native codec frames**, and produced **six valid simulated receiver packets**, with byte-exact codec output and no left-keyed RF backend. At 2.5 CPI, the optimized module's maximum PCM ring occupancy was **2/4** across all four waveforms. Under 3.0 CPI simulated contention, it still overflows for non-silent input, but gets considerably farther than the original. All failed runs dekeyed and were **not misclassified** as completed voice sessions.

**Inference:** codec workload depends strongly on PCM content. The current on-radio failure (four-block overflow around 200 ms) is consistent with the model. The optimized module is promising but **not proven on real K6 hardware**. The next high-impact physical A/B experiment is an optimized encoder with an otherwise identical GP0 IRQ and MCU firmware; any difference in real GPF1/GPT1 reports would validate its margin.

## Autonomous hardware blocker measured, not guessed

The current diagnostic MCU image (`c69a8742…a83ec`) has `K6_LIVE_SERIAL=0`. It allows passive UART observation and post-run GPF1/GPT1 retrieval, but **not** remote module launch. SOTTO GP0 also waits for **physical PTT** and explicitly requires a microphone/GP0 producer before encoding. Therefore the existing hardware setup cannot currently execute a newly uploaded module and synthesize microphone input autonomously just because a user authorizes TX. The programming cable shares the analog MIC/PTT jack; a USB session cannot be assumed to be a valid analog mic source.

In an **isolated** worktree `/storage5/dev/flux-sotto-autonomy-research-20261126`, a live-serial-enabled build was attempted in three profiles; no flash was performed. The full diagnostics+live image overflowed the 3,840-byte protected SRAM0 by 64 bytes (48 without the optional accelerated serial transport). A trial that moved foreground-only control records to main SRAM linked with 16 bytes of RAM0 free but **failed the mandatory 2,780-byte main-stack reservation** (measured 2,724 bytes). Dropping the API26/27 registry and mailbox was rejected by the Sotto resources build-time guard. Those invalid candidates are deliberately not on the radio; do not bypass memory audits to make them appear flashable.

A safe next iteration needs a specially qualified, small live-control host or a physical GPIO actuation method, *plus* an opt-in synthetic PCM producer (preferably in the same IRQ context as the real 8kHz microphone) and a bounded self-terminating RF module with watchdog, legal frequency and station identification. The user's TX authorization permits testing but doesn't remove hardware limits or RF restrictions. Do not equate virtual FSK success with an on-air measurement.
