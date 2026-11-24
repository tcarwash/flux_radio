# TX-34 — phase and frequency transition response (offline, not RF evidence)

GitHub issue #37, Phase A. This is a **synthetic-only** reproducible experiment.
No physical radio, SDR, serial port, PTT, bench lock, firmware flash or K6 hardware
is accessed.

The research code generates phase-continuous FM-IQ at 240 kS/s from either a
single-write-per-transaction causal 3-wire clock or a separate native-FSK FIFO
symbol clock. Samples are recorded as **synthetic SigMF** and consumed by the
existing receive-only SoapyFang SigmfSource -> RFBackend -> ChipModel. A tap
reads the actual channel-FIR complex output and an independent FM
discriminator scores only held-out symbols. Calibration estimates pilot delay,
carrier/DC frequency offset and level separation on a known **80-symbol**
alternating preamble; the **256-symbol heldout** random sequence is never
used for fitting. Level sign is precommitted, not corrected from payload bits.

Models:
- Native 4800-baud FSK FIFO, assumed +/-1650 Hz deviation, separate internal
  symbol clock; no imaginary 3-wire updates between FIFO symbols.
- REG51 signed 400 symbols/s, assumed +/-900 Hz deviation. One queued 3-wire
  write with 117 us per transaction. A default 1.3 kHz causal single-pole
  subaudio path is an **unverified host approximation**, not FD6818B truth.
- REG40 per-frame FSK deviation scale 0.7, held CONSTANT for the entire
  simulated 4800-baud frame; no mid-frame REG40 writes.

The group delay **includes SoapyFang receive FIR** and is not the silicon's
TX delay. Median transition amplitude, eye separation, and settling estimates
are diagnostic metrics, not hardware-calibrated thresholds. Raw IQ phase
increments include channel edge/transient effects; do not mistake them
for proven physical TX phase hops.

## Initial Phase A results — November 24, 2026

| Synthetic path | Pilot / heldout BER | Eye (sigma) | Median step | RX+fit delay |
| --- | --- | --- | --- | --- |
| Native 4800 FIFO clock | 0 / 0 | 10.72 | 2946 Hz | 83 us |
| REG51 signed 400/s, 117 us bus | 0 / 0 | 27.10 | 1798 Hz | 167 us |
| REG40 frame scale 0.7 | 0 / 0 | 10.72 | 2060 Hz | 83 us |

Synthetic negative controls:
- Reversed signed levels: 100% heldout BER, rejected by fixed mapping.
- Missing RF gate: ~47-54% BER, negligible pilot eye.
- 4500-us serialized bus updates on the 400-symbol/s path: ~53% BER
  because the 3-wire operation exceeds the 2500-us symbol period.
- Forced 134.4-Hz subaudio low-pass: 400/s transition 1229 Hz vs
  1798 baseline, eye 5.99 sigma vs 27.10; zero BER in a noise-light
  synthetic test is NOT hardware acceptance.
- Noise and abrupt 0.9-rad phase jump can degrade the RX eye/increase
  transient measures. No claim of hardware-spur characterization.
- 5-us fast-SPI is a hypothetical comparison only, NOT a validated B-chip
  register transaction time. It requires separate physical #21 evidence.

## Reproduce (read-only SoapyFang receive; synthetic IQ)

    ~/.local/share/flux-rf-venv/bin/python tools/k6_tx34_step_response.py --json /tmp/tx34-offline.json
    ~/.local/share/flux-rf-venv/bin/python tools/k6_tx34_step_response.py --paths reg51_signed_400 --control slow_bus
    ~/.local/share/flux-rf-venv/bin/python tools/k6_tx34_step_response.py --paths reg51_signed_400 --spi-us 5
    ~/.local/share/flux-rf-venv/bin/python -m pytest -q tests/test_k6_tx34_step_response.py tests/test_k6_rf_backend.py

Safe queue registration pins the clean commit and every required test/entry
module to exact SHA256 locally. The offline runner invokes pytest only,
never GitHub-supplied commands or parameters. It creates no hardware manifest.

## Physical gates (deferred and individually authorized)

Issue #37 explicitly states **NO LEASE / DO NOT TRANSMIT NOW**.
REG51 needs #20 to qualify first; REG40 frame-scale needs #26; real fast SPI
requires #21. The native-FSK baseline remains synthetic until a distinct
reviewed #11 physical plan is explicitly authorized. All physical tests
require a fresh one-shot whole-bench lock/lease, dummy load, independent
attenuated SDR, bounded TX, and verified restore. No mid-frame REG40,
four-FSK/OFDM, simultaneous effects or live DV capability follows
from this model. Report blocked dependencies, including negative findings.
