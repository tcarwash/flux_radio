# B-chip K6 physical-model calibration, 2026-11-25

**Scope:** Independent *read-only* simulator work, branch `research/k6-hardware-model-cal-20261125`, worktree `/storage5/dev/flux-radio-model-cal-20261125`. No physical radio, bench lock, RF transmissions, audio jack, or other ongoing development worktree was accessed or changed. All calibration sources were inspected and summarized from local recorded evidence, **not synthesized**.

## What this adds

- `tests/fixtures/k6_bchip_20261125_calibration.json`: **21** radio+RTL-SDR TX/packet experiment records, **6** real K6 GP0/PB1/PCM tone records, with original evidence basename, SHA256, per-run counts/measurements, and explicit caveats. Each run is scored by the differences between `good_after/good_before`, `bad_after/bad_before`; the monitor's `events[]` list is cumulative and must *not* be treated as this experiment's packet outcomes. The extractor reads only an explicit allowlist of experiment families; it rejects missing control runs, invalid counter deltas and incomplete GP0 windows.
- `tools/k6_build_physical_model_fixtures.py`: regenerates the fixture from independent external evidence without copying IQ, serial dumps, private microphone waveforms or firmware bytes. Source hashes permit exact source verification on the original workstation.
- `sim/k6emu/rf/physical_calibration.py`: `PhysicalTxCalibration` exact-condition lookup; `PaFifoTimeline` opt-in register-sequence observer; `Gp0MicCalibration` observed-only source metadata, four-window spectral averages, empirical sample selection, GPIO DMA rate bounds, and 1024-byte ring turnover estimates. No extrapolation from 1kHz/2kHz to other audio bands. No fitted absolute amplitude response.
- `sim/k6emu/rf/virtual_link.py`: optional `startup_observer`/`startup_calibration` and PA event interface. Register writes to FIFO (`0x5F`) and TX start (`0x59`) produce an **annotation** in `tx_status()['startup_evidence']`. The observer needs explicit PA transitions (`note_pa_state`) and optionally configured settling/interframe gaps. FSK enable alone is **not** interpreted as a PA state. The observer never fabricates CRC pass/fail, changes the emitted IQ or silently toggles any RF behavior. The default virtual link remains unchanged.
- `sim/k6emu/rx_dma.py`: `GpioRxDma.measured_tx_microphone()` opt-in uses the recorded TX microphone DMA rates from particular stimulus runs, with provenance metadata. The existing legacy RX `MEASURED_FREE_RUNNING_HZ=5,710,000` is **not overwritten**; that clock was measured in a different receive configuration.
- `tools/k6_physical_model_report.py` and `results/k6_bchip_20261125_physical_calibration_report.json`: repeatable JSON summary for other agents, including leave-one-run-out coverage checks. The report explicitly says `is_validated_silicon_model=false`.

## Primary physical observations

| Test configuration | Source runs | Measured CRC-valid packets | Complete sessions | Interpretation |
|---|---:|---:|---:|---|
| Direct PA-enabled preload, 20ms initial settling, 60ms gap | 5 | 15/15 | 5/5 | Repeatedly observed in this tested setup |
| On-radio PCM codec, PA enabled during preload, 20ms settling, 0ms gap | 6 | 18/18 | 6/6 | Repeatedly observed in this tested setup |
| On-radio PCM codec, no settling, 0ms gap | 4 | 11/12, with one CRC rejection | 3/4 | Works sometimes; do not promote zero guard to a universally safe setting |
| PA-muted preload control, commanded 20ms wait | 1 | 0/3 confirmed | 0/1 | No valid packets observed; exact failure mechanism unmeasured |
| Rekey-between-packets controls | 2 | 4/6 confirmed, one CRC reject and one not observed | 0/2 | Incomplete sessions; don't assume rekey is safe |
| Legacy synchronous SDR reader vs dedicated raw reader | 1 each | 0/8 (four CRC rejects, four unobserved) vs 8/8 | 0/1 vs 1/1 | **Receiver USB acquisition pathology, not RFIC corruption** |

These numbers are observations, not inferred hardware probabilities or deterministic timing limits. Conditions that do not match a documented bench case are reported as `unobserved`. Matching a few successful sessions is not a universal guarantee. Leave-one-run-out for the two repeatable successful configurations yields 4 training cases + 1 held-out success, and 5 training cases + 1 held-out success, respectively. That is a **coverage/regression check**, not independent proof of unseen PA hardware behavior.

**Physical GP0 evidence:** PA-muted physical TX with RFIC MIC ADC enabled, GP0 selector12, GPIOB/PB1 one-bit DMA, and measured 8kHz PCM output. The three measured PCM modes gave:

| Generator stimulus | RFIC VOX | Average PCM 1kHz score | Average PCM 2kHz score | Approx PCM RMS | Raw DMA rate |
|---|---:|---:|---:|---:|---:|
| OFF | 169 | 380 | 473 | 1872 | 5.585 MS/s |
| 1kHz, nominal 0.1V | 515 | **2195** | 340 | 9616 | 5.428 MS/s |
| 2kHz, nominal 0.1V | 434 | 432 | **23867** | 8704 | 5.585 MS/s |

An independent GP0 bit-density DMA module measured ~5.437–5.617 MS/s. At 1024 bytes per ring, the observed PCM capture rates imply **183.36–188.66µs per wrap**. This helps qualify buffering and CPU scheduling but is **not** an emulator cycle-accuracy claim. No audio bandwidth or fidelity transfer function can be fit from two tone frequencies, and 1kHz vs 2kHz coherent scores have markedly different gains. The PCM record consists of summaries, not sampled waveforms. Model output refuses unmeasured tones rather than generating fabricated microphone audio.

## Reproduction

```sh
cd /storage5/dev/flux-radio-model-cal-20261125
VENV=/home/tyler/.local/share/flux-rf-venv/bin/python

# Regenerate from real measured recordings (never launches the radio)
$VENV tools/k6_build_physical_model_fixtures.py \
  --tx-root /storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125 \
  --mic-root /home/tyler/flux-bench/sotto-mic-calibration-20261125 \
  --output tests/fixtures/k6_bchip_20261125_calibration.json

# Report can run without the original recordings once fixture is captured
$VENV tools/k6_physical_model_report.py \
  --output results/k6_bchip_20261125_physical_calibration_report.json

# Tests include simulator default path and positive/negative controls
$VENV -m pytest -q tests/test_k6_physical_calibration.py \
  tests/test_k6_physical_model_report.py \
  tests/test_k6_virtual_link.py tests/test_k6_rf_backend.py
```

Example opt-in PA sequence observer:

```python
from sim.k6emu.rf.physical_calibration import PaFifoTimeline, PhysicalTxCalibration
from sim.k6emu.rf.virtual_link import VirtualAir, VirtualTxBackend

observer = PaFifoTimeline(receiver_mode="direct_fsk")
backend = VirtualTxBackend(VirtualAir(), 240_000, 433_500_000,
                           startup_observer=observer,
                           startup_calibration=PhysicalTxCalibration(),
                           configured_settle_ms=20, interframe_gap_ms=60)
backend.note_pa_state(True, 0.010)
# Existing emulator code writes FIFO then FSK register at t=0.030s.
# backend.tx_status()["startup_evidence"] will include the exact-condition
# observed evidence if the register sequence really occurred. It will not
# change, truncate or fabricate the RF waveform.
```

## Next high-value hardware experiments

No additional attended hardware runs are needed to use this fixture set. To improve fidelity rather than merely validation coverage, request **a controlled PA-on/ramp vs first-packet RF IQ timing sweep** with fixed-gain SDR raw IQ and exact GPIO PA on/off timestamps; include PA-muted preload and zero/5/10/20/40ms guard controls. This would calibrate real transmit startup samples instead of a symbolic observer. Distinguish the SDR acquisition-chain test (raw recorder vs synchronous vs threaded) so 128ms USB corruption isn't mislabeled a chip fault. For microphone fidelity, request a modest **300Hz–3.4kHz multi-frequency, multi-level GP0 PCM and noise capture with 0.5–2s raw 8kHz samples**, plus cycle/overrun events; the existing four 32ms spectral summaries prove the path but do not reconstruct speech. For receiver calibration, separate controlled CFO/AFC and RSSI vs level sweeps from receive-side IF zero crossing references. Keep propagation/fading under RFSim (`~/dev/rfsim`) and RFIC TX/ADC, FIFO timing and receiver register behavior in Flux/SoapyFang.

All tests here are offline and stop at measured-vs-model comparisons. **No silicon-accurate analog PA response, extra supported modulation mode, unrestricted TX behavior, full-duplex mic capture, or production Sotto requalification is claimed.**

## Test closeout and isolation

- New calibration fixtures, exact-conditions TX observer, measured TX-microphone DMA clock, report CLI and regression tests: **32 targeted tests pass**.
- Broader Flux receiver/modem/backend/regression selection: **75 passed, 1 deselected**. The deselected `test_exact_real_arm_packets` has a pre-existing dependency-pin check (`tools/k6_sotto_module.py` expects Sotto `598f1bce...`), but `git -C sotto rev-parse HEAD` resolves to this Flux worktree commit rather than an independent pinned Sotto checkout. This is **not a behavior failure of this patch**, and the Sotto dependency was not modified. Without deselection the full selection gave 75 passes and one setup error.
- RFSim baseline from its own repository/venv directory: **39 passed, 11 skipped, 9 subtests passed**. The first attempt from the Flux working directory did not find the standalone `experiments` package and was corrected by running pytest from `~/dev/rfsim`; no RFSim files were changed.
- A regression gate now explicitly refuses to mark a claimed 20ms settling configuration as qualified when the virtual PA timeline only shows 1ms. The evidence labels are not a substitute for checking actual simulated PA lifecycle events.
- The first held-out checks for the two repeated success families passed, but **there is no measured RF PA analog response, no calibrated CRC impairment generator and no full-band GP0 waveform model yet**. Further claims need new physical IQ and PCM waveform capture.
