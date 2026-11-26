# Flux virtual on-air modulation sweeps (RFSim + SoapyFang)

**2026-11-25 · research branch · OFFLINE ONLY.** This is the next step after
the existing CW/AM/SSB/QPSK/OFDM host waveform suite, the K6 TX-path
combination matrix, and the receive-only SoapyFang replay experiments.
Nothing in this experiment flashes, keys or otherwise operates a radio.

## Model boundaries

Pipeline:

1. `tools/k6_soapyfang_modlab.py::fixture` makes the public deterministic
   reference waveform, with reference bits/audio/schedule.
2. `tools/k6_rfsim_onair_sweep.py` chooses one explicitly labeled TX model:
   `ideal` (unrestricted linear IQ, upper bound), `phase_only` (unrestricted
   instantaneous phase, not a known B-chip feature), or `k6_path` (the
   schematic actuator + PA envelope from `sim/modulation/tx_paths.py`).
3. RFSim `ChannelSession.apply` adds time-varying multipath, shadowing,
   Doppler, one seeded noise stream and optionally carrier frequency offset.
4. SoapyFang's actual SigMF receiver frontend `RFBackend/ChipModel`
   processes the propagated IQ, and a host reference decoder observes
   its filtered complex IQ. **This is not a real K6 modem/decoder.**
5. Each independent trial records channel, seed, SNR, CFO, TX hypothesis,
   decoder result, and an explicit not-hardware-qualified marker.

The existing SoapyFang host demodulators use known frame start and
fixture-assisted timing/complex gain; this suite is **not blind acquisition**.
Even a very high modeled success rate is *not* physical RF, measured
sensitivity, symbol-clock recovery, PA qualification, regulatory emission
compliance, or proof that the B-chip can transmit a particular waveform.

## Reproduce

From the Flux repository, with `numpy`, `scipy`, `pytest`,
SoapyFang's existing dependencies, and a local RFSim checkout:

```sh
export PYTHONPATH="$HOME/dev/rfsim:$PYTHONPATH"
python -m pytest -q tests/test_k6_rfsim_onair_sweep.py

# Real RFSim channel + real SoapyFang virtual RX; 16 seeded cases
python tools/k6_rfsim_onair_sweep.py \
  --modes qpsk,ofdm \
  --profiles static,mobile-rural \
  --snr-db 30,10 --seeds 0,1 \
  --json /tmp/flux-onair-baseline.json

# Hypothetical phase-only RF source: contrast with ideal-IQ ceiling
python tools/k6_rfsim_onair_sweep.py \
  --modes qpsk,ofdm,am,ssb \
  --profiles static,fixed,mobile-rural \
  --snr-db 30,15,5 --seeds 0,1 \
  --tx-model phase_only \
  --json /tmp/flux-onair-phase-only.json

# Model stock-like constant-envelope carrier plus a hypothetical FM actuator
python tools/k6_rfsim_onair_sweep.py \
  --modes qpsk,ofdm \
  --tx-model k6_path --actors native_fsk \
  --envelope constant_pa --profiles static,mobile-rural \
  --snr-db 30,10 --seeds 0,1 \
  --json /tmp/flux-onair-native-fsk-hypothesis.json

# Compare unproven signed deviation + slow envelope model (research only)
python tools/k6_rfsim_onair_sweep.py \
  --modes qpsk,ssb \
  --tx-model k6_path --actors signed_dac \
  --envelope apc_slow --profiles static \
  --snr-db 30 --seeds 0 \
  --json /tmp/flux-onair-signed-dac-hypothesis.json
```

No real receiver or SDR is attached. This is *not* a valid on-air proof.

The runner accepts unique comma-separated values for modes, profiles,
SNRs, seeds and CFOs. A run is capped at 500 cases to bound runtime.
The output is a deterministic JSON matrix without raw private recordings.
`decoded` means the **reference-assisted host decoder passed**.
`decode_failed` is a meaningful negative PHY/model outcome and does not
cause a nonzero process exit. `harness_error` means the test did not run
correctly and **does** cause exit code 2. Preserve all three outcomes.

Supported RFSim channel profiles are `static`, `fixed`, `fixed-fringe`,
`knife-edge`, `pedestrian`, `mobile-rural`, `mobile-urban`,
`mobile-bad-urban`, `mobile-hilly`. These are synthetic published-style
profiles, not site-specific measured K6 paths.

CI pins RFSim commit
`241970f328dd4711f46377d3107cd8f5747887aa` to avoid silent changes
to propagation assumptions. The CI smoke runs QPSK/OFDM at static/rural,
30/10 dB, two seeds, and checks that **none of 16 cases is a harness error**;
decode failures remain visible rather than making the harness falsely red.

## Interpretation and next experiments

The most useful comparisons are **within** a mode using the same seed,
profile, SNR and CFO across ideal vs phase-only vs K6-path models. Do not rank
across modes solely by a boolean pass. QPSK/OFDM reference EVM/BER are
genie-aligned; AM modulation-depth/correlation, SSB image rejection, and
CW envelope timing are not payload packet-error rates.

Near-term directions:

* Add real blind preamble/sync and carrier/timing estimation to the host
  decoder; a CFO-induced alignment failure alone does not prove a transmitter
  or channel is unusable.
* Sweep signed-FM DQPSK (not conventional RRC QPSK) with the dedicated
  `tools/k6_dqpsk_polar_lab.py`, at multiple symbol rates, LPF bandwidths,
  DAC quantization levels and SPI timings, before claiming an on-air mode.
* Substitute *measured* TX spectral/step response and receive captures when
  they exist with attributable fixture, firmware, radio, date and gain; keep
  old recordings held out during fitting.
* Do not enable KISS, on-device modules or new transmit registers on the
  strength of these results. Actual RF always needs a separate validated
  fixture, current bench lease, operator authorization and independent SDR
  evidence.

See also `docs/k6-modulation-workplan-20261124.md`,
`docs/k6-tx-path-combination-lab-20261124.md`, and
`docs/k6-soapyfang-modulation-validation-20261124.md`.
