# Flux TX-21B — independently specified polar-IQ target and holdout RF verifier

Date: 2026-11-22 (local), 2026-11-23 UTC
Branch: `research/native-fsk-pdm`

## Status and boundaries

**Completed: OFFLINE SOFTWARE, NOT A PHYSICAL COMBINED TRANSMISSION.**

This checkpoint adds two host-only tools and synthetic test coverage. It makes
a future simultaneous native-FSK/APC conducted experiment **measurable**
without claiming that simultaneous control has yet succeeded.

- `tools/k6_polar_known_target.py` converts the existing checksum-verified
  64-word / 1024-bit FSK lab FIFO manifest into a separately prescribed ideal
  sample-rate-tagged complex target. Its binary ±1.2 kHz *assumption* is NOT
  calibrated hardware deviation, and its independent 80%/100% amplitude
  control is a mathematical requirement, NOT a validated TX scheduler.
- `tools/k6_iq_holdout_verify.py` accepts the target and an independently
  supplied, pre-downconverted complex RF observation; searches a bounded
  nonnegative capture offset, estimates a SINGLE carrier offset and complex
  gain on the pilot segment only, freezes all three, and reports held-out
  complex EVM, amplitude residual, phase residual and optional 80%/100%
  amplitude-conditioned phase diagnostics.

The verifier is deliberately unable to attest capture provenance. Its report
sets `combined_polar_tx_demonstrated=false` and
`physical_hardware_iq_evm_verified=false` even on excellent synthetic data.
These flags may only be changed by independent physical experimental
evidence, never inferred from a numerical EVM.

## Reproduction

From `/storage5/dev/flux-native-fsk-pdm`:

```sh
python3 tools/k6_polar_known_target.py \
  --manifest docs/k6-fsk-lab-vector.json \
  --sample-rate 96000 \
  --deviation-hz 1200 \
  --apc-halfstep-ms 20.6 \
  --out-prefix /tmp/flux-polar-independent-target

python3 -m pytest -q \
  tests/test_k6_iq_holdout_verify.py \
  tests/test_k6_polar_known_target.py \
  tests/test_k6_polar_surrogate.py \
  tests/test_k6_rf_iq_polar_workbench.py
```

The target is a `complex64` `.npy` with 20,480 samples at 96 kHz,
nominally 213.333 ms. The accompanying JSON records FIFO checksum,
bit ordering, exact assumptions and target-file SHA256.

For an appropriately prepared, **independently obtained** complex baseband
observation with the *same* sample rate (and enough pre-roll), the interface is:

```sh
python3 tools/k6_iq_holdout_verify.py \
  --target /tmp/flux-polar-independent-target.npy \
  --observed /path/to/independently-prepared-96k-complex-iq.npy \
  --sample-rate 96000 \
  --pilot-ms 50 \
  --max-lag-ms 10 \
  --max-cfo-hz 300 \
  --out-prefix /tmp/flux-polar-heldout-score
```

This is NOT a transmitter or SDR capture command. The verifier does not
accept raw interleaved RTL-SDR U8 samples or automatically recognize the
FD6818B framing: an independently documented frequency shift, decimation,
burst selection and nominal pilot boundaries are still necessary to prepare
actual SDR IQ.

## Known limits

1. The desired target assumes mathematically instantaneous ±frequency
   switching. The physical B chip has modulation settling and phase
   behavior. The original FIFO frame may add preamble and sync not included
   in the lab payload. This initial target is a **reference** and may need an
   independently measured B-chip impulse response before absolute EVM is
   interpretable.
2. Integer sample offset only; it does not optimize a fractional delay, clock
   skew, FM frequency response, phase drift or any held-out samples.
3. Using the measured FSK phase to create the ideal target, as in the prior
   counterfactual surrogate, would make full-IQ EVM invalid. This test
   explicitly requires source-independent waveform definition.
4. Optional level-conditioned phase-residual differences can quantify
   correlation, but do not alone assign cause to PA pulling, TX modulation,
   or SDR artifacts.
5. Synthetic test EVM is a software regression result, not a radio result.
   The original transmitter is restored to production and no flash,
   serial or physical TX operations were performed for this checkpoint.
6. Neither a passed complex IQ error test nor a narrow plotted spectrum
   establishes calibrated RF power, spectral-mask compliance or ham
   interoperability.

## Next physical go/no-go

The previous [TX-21 offline experiment](k6-polar-combined-iq-offline-20261122.md)
found observed APC-only transition-related phase offsets and documented a
safety/tooling gate that blocked staging a combined transmitter module.
**That gate remains in force and was not bypassed.** Before a genuine
joint-control test, independently validate a bounded, watchdog-protected
native-FSK/APC actuation path and its full restore behavior without relying
on an unverified user module.

Once legitimate hardware preconditions permit, take **one short** conducted
capture with a known independent FSK payload and APC 80/100% sequence,
record register-write timing and RF IQ, preprocess it under a documented
fixed timebase, and compare training-only calibration with genuinely held-out
samples. Review adjacent RF emissions and restore the original SPI module
and radio firmware after the transaction.

Other TX-10..TX-23 entry points remain tracked in
[k6-tx-next-experiments.md](k6-tx-next-experiments.md). Do not mistake this
polar measurement tool for narrowing Flux to FSK.
