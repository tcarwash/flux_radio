# CW conducted TX response sweep: capture-ready analyzer and bounded offline shaping

**2026-11-24 · CW/non-Sotto modulation track · no physical RF test has run.**

The new targeted physical-hardware queue experiment is [CW TX-35 / issue #38](https://github.com/tcarwash/flux_radio/issues/38). It requests a **conducted RF envelope/filter response sweep** of the B-chip K6 transmitter as a prerequisite for real click-free CW. It is `OPEN / UNCLAIMED / RADIO-EXCLUSIVE DEFERRED`. Posting the issue does **not** reserve the bench, key PTT, open `/dev/ttyUSB0`, or instruct another agent to do an unapproved test. The existing TX-29 (#22) protective PA-driver guard and the [shared queue #11](https://github.com/tcarwash/flux_radio/issues/11) have priority.

This commit provides the **offline analyzer** and model-only what-if experiment; it contains **no transmit firmware, module, PA register driver, serial interface, RF SDR connection or bench command**. It consumes captured IQ *after* a separately authorized measurement and outputs a reproducible response report for review. It never claims that a simulated 90-Hz corner was measured on the chip.

## What to measure in hardware

The requested conducted physical protocol (the issue has the complete safety gates) compares **three distinctly interpreted physical actuators**, sequentially and only when a previous path proves safe: stock closed-loop APC target, bounded downward-only driver gain `REG36[5:0]` with its unrelated register bits preserved, and bounded downward-only bias `REG19[3:0]`. These are not interchangeable with actual TX audio/filter/CTCSS controls. The actual low-power code and safe PA turnoff must be read back and verified; they cannot be inferred from the simulated `REG30[3]` gate. Old CW trials found unpredictable low-power labeling and a slow/hysteretic closed-loop APC response.

For each qualified physical actuator use separate fixed-gain, AGC-off SDR captures of **10, 20, 40, 80, 120, 200, 350, 500, 800 and 1,000 Hz** bounded *RF amplitude modulation* requests, with positive RF carrier and guard periods. Capture **complex IQ, actual normalized per-sample command waveform**, PTT/PA timeline, register snapshots and trace, calibration/attenuation, SDR sample clock/center frequency and power/duty. The analyzer cannot reconstruct a faithful command trace from merely *desired* sine values when firmware update timing is uncertain. A missing/unsafe frequency or failed TX response should be reported as missing or failed, **not imputed**.

A separate [TX-27 / #20](https://github.com/tcarwash/flux_radio/issues/20) subaudio *frequency-deviation* filter response test would have different excitation/receiver metrics; CW's primary problem is **RF amplitude envelope** response and on/off isolation. Do not conflate the two filter paths.

## Offline analyzer, synthetic verification

`sim/modulation/cw_tx_response.py` performs coherent amplitude-response analysis on matched command and complex IQ sampled from one clock. It reports each modulation's normalized amplitude gain (SDR gain-independent under fixed gain), lag, envelope harmonic distortion, carrier-frequency offset and residual carrier phase modulation (AM→PM). To avoid inventing distortion from a non-integral capture duration, the THD estimator fits the first three harmonics **jointly**. The frequency-response fit estimates one *optional* first-order low-pass cutoff with a residual-fit score; a poor/non-single-pole response produces `fit_credible=false`. A fitted single-pole frequency corner does **not** prove the full PA step response, hysteresis, carrier phase, turnoff dynamics or clean spectrum.

Run the completely synthetic calibration test:

```sh
python tools/k6_cw_tx_sweep_analyze.py --synthetic \
  --synthetic-cutoff-hz 90 --synthetic-am-pm-deg 12 \
  --json /tmp/flux-cw-90hz-synthetic.json

python tools/k6_cw_tx_sweep_analyze.py --synthetic \
  --synthetic-cutoff-hz 350 \
  --json /tmp/flux-cw-350hz-synthetic.json

python -m pytest -q tests/test_k6_cw_tx_sweep_analyze.py
```

The generated records are **synthetic** and reproducible, not a physical radio capture. The 90-Hz fixture fitted approximately **90.06 Hz**, with ~0.005 dB RMS error against the first-order frequency response. The 350-Hz fixture fitted about **350.27 Hz**. A deliberately poor multi-peak response is rejected as a useful single-pole approximation; noise-only, missing, unpaired, out-of-range and malformed input fails closed.

## Conditional CW ramp comparison (same modeled frequency response)

The same CLI compares nominal 20-WPM `CQ`, with 4/5/8/12 ms raised-cosine ramps, through a **hypothetical one-pole analog PA response** and 117-µs single-register write slots. It also evaluates **bounded feedforward compensation**, the clipped discrete-time inverse `u[n] = (target[n] - (1-alpha)*target[n-1]) / alpha`, where `alpha=1-exp(-2*pi*corner_hz/fs)`. The command is clamped to 0..1 relative to an already established safe maximum and is forced zero whenever the target is zero. This is not an executable radio program or a safe MCU `REG36` value table. The physical PA's nonlinearity/latency may invalidate the inverse.

| Hypothetical corner | CW ramp | Naive max shape error | Bounded inverse max error | Naive vs inverse RF envelope +5 ms after key-up |
| --- | --- | --- | --- | --- |
| **90 Hz** | 4 ms | 47.3% | 19.6% | 2.18% → 1.02% |
| **90 Hz** | **5 ms** | **41.6%** | **14.4%** | **1.75% → 0.74%** |
| **90 Hz** | 8 ms | 30.1% | 6.8% | 0.98% → 0.34% |
| **90 Hz** | 12 ms | 21.4% | 3.3% | 0.53% → 0.16% |
| **350 Hz** | 5 ms | 15.2% | 1.9% | Approximately zero in this model |

**Important trade-off:** as compensation more closely matches the ideal fast edge, its modeled energy outside ±50 Hz generally **increases** versus the uncorrected sluggish signal (though approaches the *ideal raised-cosine* leakage). Lower naive leakage is not automatically better CW: an inadvertently over-filtered carrier can have inaccurate Morse timing, long RF tail or an RF phase chirp. Neither spectrum number establishes compliance or physical receiver audio quality. The optimizer cannot solve large APC hysteresis, PA clipping or RF gating by simply adding gain pulses.

Crucially, the `post_keyoff_5ms_peak` metric examines **every mark-to-space transition**, not the long quiet gap at the end of `CQ`, which would otherwise make a lingering RF tail appear to be zero. The model leaves independent gate/PA restoration unproven, as required by the hardware queue.

## Analyze recorded data after the separately approved RF sweep

The manifest expects actual complex64 little-endian RF IQ (`*.sigmf-data`, no header) and an **aligned float32 normalized commanded RF amplitude** array (`0..1`) at exactly the same sample rate and sample index origin. Names are illustrative; no example is claimed to exist on the physical K6:

```json
{
  "source_kind": "operator_declared_conducted_iq",
  "sample_rate_hz": 48000,
  "sdr_agc_disabled": true,
  "sdr_fixed_gain": true,
  "external_dummy_load_confirmed": true,
  "gain_settings_recorded": true,
  "tx_restore_verified": true,
  "runs": [
    {"frequency_hz": 10, "iq_cf32le": "cw_10hz.sigmf-data", "command_f32le": "cw_10hz_control.bin"},
    {"frequency_hz": 20, "iq_cf32le": "cw_20hz.sigmf-data", "command_f32le": "cw_20hz_control.bin"},
    {"frequency_hz": 40, "iq_cf32le": "cw_40hz.sigmf-data", "command_f32le": "cw_40hz_control.bin"},
    {"frequency_hz": 80, "iq_cf32le": "cw_80hz.sigmf-data", "command_f32le": "cw_80hz_control.bin"}
  ]
}
```

(The full hardware sweep should contain at least ten points when safe, and may report unavailable points explicitly in its evidence outside the manifest.) The analysis program rejects physical-origin manifests lacking explicit AGC-off, fixed-gain, dummy-load and restore assertions. **These are operator declarations, not independent hardware verification**; the JSON result always sets `physical_rf_verified=false`. The reported `measured_physical_rf` is only `operator_declared_not_independently_verified`. The analyzer deliberately does not infer physical origin, device serial identity or legal frequency from a filename or IQ bytes.

Then run **offline**:

```sh
python tools/k6_cw_tx_sweep_analyze.py \
  --manifest /path/to/approved-capture/manifest.json \
  --json /tmp/cw-tx-physical-report.json
```

Both paths can emit a conditional `cw_forecasts` table; review the raw IQ, response-fit residual, and independent phase/SDR metadata *before* using any recommended ramp parameters. If the radio behaves unlike a single low-pass, use the measured sweep points and queue a follow-up rather than applying the clipped inverse.

## Next

- When a bench holder accepts #38 and meets #22's safe down-only firmware guard, record short conducted RF IQ **without other agents' interruptions**. This work does not request a live lease.
- Use the analyzer's frequency-by-frequency values to distinguish APC lowpass from open-loop driver or bias, measure actual AM-to-PM/chirp, key-off and RF-on current. Publish raw calibrated traces, *not only* a fitted filter corner.
- Only after physical calibration, build a *separate* bounded firmware scheduler for clean CW, with independent PA-off watchdog and full mask/readback/restore verification. Keep Sotto's receiver/firmware implementation in its separate thread.
