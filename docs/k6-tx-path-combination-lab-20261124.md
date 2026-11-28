# K6 B-chip transmit-path combination laboratory (offline Goal)

**2026-11-24 | Host simulation only | Physical RF and flash disabled**

Follow-up causal register-write CW replay and SoapyFang results: [k6-cw-register-trace-replay-20261124.md](k6-cw-register-trace-replay-20261124.md).

This expands the existing [five-mode modulation workplan](k6-modulation-workplan-20261124.md) and [SoapyFang RX replay harness](k6-soapyfang-modulation-validation-20261124.md) by separating *frequency/phase* actuation from *RF envelope* actuation, enumerating feasible and conflicting path combinations, and replaying representative synthetic results through the **existing receive-only** SoapyFang `SigmfSource → RFBackend → ChipModel` filtering chain. The host reference decoders use the observed **filtered IQ**, with known reference timing and complex gain; K6 firmware does **not** decode or transmit these new modes. No hardware access is performed.

## Evidence ledger / path identifiers

Frequency/phase path (11 candidates):

| Actuator | Evidence classification | Reason |
| --- | --- | --- |
| `carrier` | Observed individual path | Stable unmodulated RF when keyed; no phase control |
| `native_fsk` | Observed individual path | B-chip binary FSK/FIFO TX tested; synthetic FIFO 4800 baud |
| `tone` | Observed individual path | REG70/71 audio FM tone, not arbitrary RF IQ |
| `dual_tone` | Hypothesis | Second DTMF tone via REG72 is unqualified on B-chip; see queue #16 |
| `subaudio` | Observed individual, modeled bandwidth | REG51/DCS/CTCSS signed binary FM, relatively slow |
| `dcs_bits` | Observed individual, modeled symbol clock | DCS bitstream / subaudio FM, not arbitrary PSK |
| `ctcss_phase` | Hypothesis | REG52 shifts *CTCSS oscillator phase*, not RF QPSK; #25 |
| `micin` | Observed individual AF-over-FM path | MICIN audio FM with assumed 300–2700 Hz transfer; external PA11-to-MIC loopback is a **separate**, unverified physical cable route |
| `gp0` | Hypothesis | GP0/PB1 external CDCSS *transmit* mux unverified, queue #23 |
| `signed_dac` | Hypothesis | Combined signed REG51 subaudio DAC with dynamic gain, queue #20 |
| `reg40` | Hypothetical control-only | Native FSK deviation scaling; not an independent modulation source, queue #26 |

Envelope path (10 choices): `ideal` (reference only), `constant_pa`, `hard_gate`, `measured_gate`, `apc_slow`, `apc_knee`, `driver6`, `bias4`, `driver6_bias4`, `driver6_gate`.

- `measured_gate` approximates the prior B-chip VHF-low unshaped CW **5.25 ms attack / 0.765 ms release**. The actual response is board-, band-, power- and firmware-specific.
- `apc_slow` is a schematic 65-Hz first-order response informed by physical 48–91-Hz APC measurements. `apc_knee` adds schematic 93-Hz oscillation *without claiming a quantitative match* to prior unstable APC IQ captures. Avoid promoting any derived success as a real CW solution.
- `driver6`, `bias4`, `driver6_bias4`, `driver6_gate` use hypothetical fast open-loop 6/4-bit controlled envelopes; no B-chip physical transfer curve has been established. Their idealized performance is an **upper-bound hypothesis**.
- Real RF carrier phase from DTMF/CTCSS/DCSS is not equivalent to directly controlling the complex RF oscillator. `ctcss_phase` deliberately perturbs only a 123-Hz CTCSS FM tone.

Register update timing is modeled too. The default `--spi-write-us 117`
approximates the standard software-driven 3-wire register-write time;
`--spi-write-us 5` is a **hypothetical fast-SPI scenario**, not a proven
board-safe clock. Dynamic REG51, gain, bias and audio-tone target values are
sample-held at the resulting update rate; the joint driver-plus-bias envelope
charges two writes. These assumptions do not establish MCU scheduling,
peripheral settling time, PA linearity or a usable high-speed TX API.

The numerical models are **intentionally coarse** and conservative about evidence. They project the *desired* complex-IQ phase trajectory into a finite set of optimistic actuator behaviors. For example, native-FSK chooses discrete ±1500-Hz symbols at a 4800-baud clock, `subaudio` low-passes at 135 Hz, `micin` uses AC coupling and band limits, and the signed-DAC hypothesis uses an assumed finite-rate / 8-bit transfer. They are *not* firmware register replay, verified mux timing, calibrated PLL dynamics, driver impedance, or a physical 8-bit RF DAC. Model code is in `sim/modulation/tx_paths.py`.

## Compatibility and combinations

`tools/k6_tx_path_lab.py` enumerates **all combinations of one, two, or three frequency-source/control names** crossed with each of the 10 envelope options. This is a bounded search of plausible hardware resource use, not a claim that four or more sources cannot ever coexist. The enumerator includes blocked combinations rather than dropping them. Safety constraints include:

- REG72 is shared between native-FSK baud and the second DTMF oscillator; the two cannot be presumed concurrent.
- REG70 native-FSK deviation and the audio-tone oscillator are conservatively flagged as shared-register mode contention until explicitly disproved.
- REG51 multiplexes internal subaudio/DCSS, external GP0 and signed-DAC encodings. Pairwise conflicting modes are rejected.
- `reg40` has no standalone data stream: only combine with `native_fsk`, and do not infer arbitrary-IQ modulation from deviation scaling.
- Multiple paths with no known register conflict are **still unverified combinations**; synthetic additive FM is optimistic. No path has `hardware_qualified=true`.

The 11-source enumeration contains **2,310** bounded candidate
configurations (one-to-three sources crossed with ten envelopes), of which
**1,010 are modeled** and **1,300 are blocked** by deliberately conservative
mode/register conflicts. Across six waveform fixtures, there are **13,860**
rows: 6,060 simulated and 7,800 marked conflicting. These counts are
compatibility-model bookkeeping, **not physical feasibility test passes**.

The full matrix runs across all six waveform fixtures—CW, AM, USB, LSB, QPSK and OFDM—and yields one JSON row per candidate configuration per mode. For each compatible candidate it measures complex-IQ waveform error (gain-normalized, **not** decoder EVM), envelope error, PAPR and sample count. For representative selected combinations it additionally runs the actual SoapyFang RX processing and independent host decoder to record AM depth, SSB suppression, CW envelope, or BER/EVM. The ideal IQ path is kept as an explicit **non-hardware baseline**.

## Reproduce

From the project root with NumPy, pytest and SoapyFang dependencies installed:

```sh
# Representative SoapyFang receive+reference-decode checks, all modes:
python tools/k6_tx_path_lab.py \
  --snr-db 30 --json /tmp/flux-tx-paths-soapy.json

# Every 1/2/3-actuator combination × ten envelopes × six modes:
python tools/k6_tx_path_lab.py \
  --full-matrix --json /tmp/flux-tx-paths-full.json

# Faster model-only compatibility and distortion inventory:
python tools/k6_tx_path_lab.py \
  --full-matrix --skip-soapy --max-actors 2 \
  --json /tmp/flux-tx-paths-model.json

python -m pytest -q tests/test_k6_tx_path_lab.py \
  tests/test_k6_soapyfang_modlab.py tests/test_modlab_*.py
```

To attach a *real emulated firmware request trace* to the synthetic matrix,
`K6Board(..., capture_tx_events=True)` now records opt-in timestamped
FD6818B 3-wire writes. `sim/k6emu/rf/tx_trace.py` independently classifies
write **intent**, time, ambiguous mode selectors and shared-register caveats;
it does not synthesize transmitted RF from unobserved register writes.
A JSON file `{ "clock_hz": 96000000, "writes": [[96000,54,6306], ...] }`
can be passed using `--regtrace path.json`, keeping source provenance separate
from hypothetical models. The original un-timestamped `fd_writes` behavior
remains unchanged and this trace is off by default. There is no GPIO keying
or hardware SPI access in this path.

The output preserves failures and conflicts as data, not unqualified SUCCESS. This experiment cannot validate that REG36/REG19 are safe to drive at sample rate, that the RFIC has a signed instantaneous-frequency DAC, or that native FSK plus any of the auxiliary paths actually sums before the PA. No TX hardware interfaces or permission-bearing APIs are imported.

## Hardware experiment queue

The user authorized **adding** hardware-dependent experiments to the existing shared GitHub queue, *not performing* unleased RF tests. Existing solo-path issues #16, #20, #21, #22, #23, #25 and #26 remain authoritative for individual actuator qualifications. Two non-duplicative composite investigations were added:

- [#35 — TX-32 polar frequency + envelope combination](https://github.com/tcarwash/flux_radio/issues/35) — first establish independently safe frequency and driver envelope axes, then (with an explicitly separately authorized exclusive bench lease and dummy-load/SDR fixture) measure simultaneous AM-AM, AM-PM, hysteresis, timing and key-up isolation. No actual SSB/OFDM until both axes prove controllable.
- [#36 — TX-33 FSK/tone/subaudio/MIC source coexistence](https://github.com/tcarwash/flux_radio/issues/36) — resolve register mux and simultaneous FM summation under bounded known-data bursts, with source readback and negative controls.

Queue issues are coordination only, not permission to transmit, reflash, alter hardware SPI, or acquire the bench lock. When hardware is available, use the repo's current trusted-owner queue author gate, bench-lock workflow, explicit operator approval, restore/abort procedures and independent SDR evidence. Preserve negative outcomes.

## Next phase

1. Add a simulator-only FD6818B TX register-trace observer (from an **existing simulated firmware state**, not hardware) to replace hand-selected host actuator combinations with actual *requested register changes*. Capture conflicts and timing without keying.
2. Fit simplified APC, driver gain and signed-frequency transfer functions against existing **held-out, raw conducted SigMF** if captured under known chip/firmware conditions, never against a speculation in research prose. Keep unverified curves explicitly synthetic.
3. Only after independent TX path measurements promote physical capabilities, and keep AM/SSB/OFDM/QPSK as host-only until the RF evidence passes.
