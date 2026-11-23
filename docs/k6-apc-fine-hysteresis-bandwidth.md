# Flux TX-20 — APC hysteresis and high-side envelope bandwidth

Bench: 2026-11-22 local, 2026-11-23 UTC evidence. Branch: research/native-fsk-pdm.

## Summary

Four new short, dummy-loaded RF experiments (two each of threshold and
fast-envelope tests) establish two important limits for the FD6818B K6:

1. **Low-end APC control exhibits reproducible hysteresis.** On the way down,
   72% stays free of deep carrier fades, but reaching 70% initiates
   ~8-ms-spaced dropouts that persist while rising through 72% and 74%,
   partially recover by 76%, and disappear by 80%.
2. **The 80–100% amplitude envelope works well at 24–48 Hz, but loses
   substantial contrast around 91 Hz.** Two captures yield ~94% of the
   slow-step amplitude swing at 47.6 Hz, ~39.5% at 91.1 Hz.

This is real RF carrier amplitude control, not merely register writes,
but **not** a full arbitrary-IQ synthesizer. Independent phase control,
command timing, EVM, RF phase continuity and occupied spectrum are
still open questions. Tests use fixed-gain, air-coupled RTL-SDR
reception and are *not* calibrated conducted RF power measurements.

## Physical threshold experiment: 13 steps × 75 ms

Firmware API27 was compiled explicitly with K6_APC_FINE=1. Only
percentages 70,72,74,76,78,80,85,90,100 are allowed in that
experimental variant. The prior four-value default APC API whitelist
is unchanged. Firmware enforces exact B-chip ID, fixed 433.500 MHz
lab frequency, low-power PA calibration, independently armed TIM14
TX watchdog, and exact REG_36 readback; lower PA-driver bits remain
0xA2. The measured calibrated APC target byte is 180 (0xB4).

Sequence:

    100 85 80 78 76 74 72 70 72 74 76 80 100

Both live modules returned result 13 and independently verified
restoration of the original module library slot and VFOs.

| Target | Direction | Run 1 RF amplitude | Run 2 | Deep-fade 1-ms bins (R1/R2) |
|---|---|---:|---:|---:|
| 100% | start | 100.34% | 100.30% | 0/0 |
| 85% | down | 85.57% | 85.66% | 0/0 |
| 80% | down | 79.35% | 79.23% | 0/0 |
| 78% | down | 76.33% | 76.22% | 0/0 |
| 76% | down | 73.62% | 73.70% | 0/0 |
| 74% | down | 70.21% | 70.29% | 0/0 |
| 72% | down | 67.58% | 67.55% | 0/0 |
| **70%** | bottom | 73.99%* | 71.84%* | **13/15** |
| **72%** | up | 75.87%* | 75.35%* | **12/13** |
| **74%** | up | 76.50%* | 75.38%* | **9/9** |
| 76% | up | 73.79%* | 73.54%* | 2/2 |
| 80% | up | 79.16% | 79.15% | 0/0 |
| 100% | end | 99.66% | 99.70% | 0/0 |

Asterisk: relative amplitude median influenced by fades/recovery,
not a linear watts measurement.

Each fade means received carrier amplitude less than 36% of the
full-level reference in a coherent 1-ms time bin. All 13 stages
were checked identically. The state-dependent fade pattern
repeated almost exactly across two independent radio transmissions.
An RF power meter/attenuated coupler would help distinguish
PA/APC physical oscillation from receiver-dependent behavior.

**Conservative operating bound:** use 80–100% for further
envelope experiments. A descending 72% setting alone does NOT
establish safe stable operation after arbitrary transitions.

Offline checksum-verifying analyzer:
tools/k6_apc_fine_analyze.py.

## Physical fast-envelope experiment: high side only

The second module commands no lower than 80% APC. Waveform:

    100% 80 ms
    8 cycles: 80%/100%, each state 20 ms (nominal 25 Hz cycle)
    8 cycles: 80%/100%, each state 10 ms (nominal 50 Hz cycle)
    12 cycles: 80%/100%, each state 5 ms (nominal 100 Hz cycle)
    100% 80 ms

Nominal 760-ms program. The SDR detected 788 ms bursts in both
runs. Module execution and register communication overhead
probably account for at least some of the extra 28 ms.
The C module returned result 24 in each physical test,
with no module fault, and was restored and verified after each.

The offline tool tools/k6_apc_fast_analyze.py validates the
saved IQ checksums and hardware restore evidence; uses 0.5-ms
coherent carrier estimates and windowed least-squares
searches for the actual repetition rate. Empirical timing
windows account for cumulative callback overhead.

| Commanded half-step | Nominal cycle Hz | Observed cycle Hz (both) | RF carrier amplitude swing R1 | R2 |
|---|---:|---:|---:|---:|
| 20 ms | 25 | **24.28** | 0.8622 | 0.8577 |
| 10 ms | 50 | **47.59** | 0.8124 | 0.8119 |
| 5 ms | 100 | **91.05** | 0.3394 | 0.3410 |

Amplitudes are in relative ADC units and calculated from the
fundamental of the approximately 50%-duty 80/100% AM envelope.
They are not absolute RF power or FCC spectral compliance metrics.

Relative to the 20-ms swing:

| Half-step | Run 1 retained | Run 2 retained |
|---|---:|---:|
| 20 ms | 100.00% | 100.00% |
| 10 ms | **94.22%** | **94.66%** |
| 5 ms | **39.36%** | **39.76%** |

An earlier provisional result used uncorrected nominal windows
and estimated the final group at 88 Hz/~30% contrast.
Refined windowing, applied independently to both IQ captures,
gives **91.05 Hz/~39.5%**. A synthetic-IQ test exercises
the same estimator with an ideal immediate-response envelope
and guards against inferring attenuation from timing alone.

The result is a first, **not calibrated**, APC frequency
response. It cannot separate firmware scheduling, SPI register
write pacing, PA APC loop response and receive-system effects.

## Artifacts and recovery

Experimental firmware build ID: ef53ce1d2ca74667 (API27,
KUL2 packed catalog, cable only). Image:

    ~/flux-bench/tx-injection-20261122/apc-lab/build-fine/flux-apc-b-fine.bin
    SHA256 b709a53b26ca948b54f62b546fc8db552708cba57a38c9ca2dcb76d5685f6ebc

All four captures, metadata and saved analysis reports:

    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fine-run01.npy/.json/.analysis.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fine-run02.npy/.json/.analysis.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run01.npy/.json/.analysis.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fast-run02.npy/.json/.analysis.json

After the experiments, the original MCU firmware was re-flashed
and its identity verified through live cable queries:

    Production firmware ID: 95d6b260d4122287
    Module API: 26
    Library entries: 31
    Original slot-30 executable SHA256:
      6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522
    Both VFOs: 144.390 MHz
    TX off, standby idle.

Physical verification snapshot:

    ~/flux-bench/tx-injection-20261122/apc-lab/original-restored-after-fine.json

The earlier full 2-MiB SPI backup is still at
~/flux-bench/tx-injection-20261122/apc-lab/spi-pre-apc.bin,
SHA256 4533e49c7cc20dc37c83704d057613b38af39378af44da863435ab1a8d54d54b.

## Discriminating next experiments (arbitrary RF objective)

1. Add firmware-side timestamp telemetry for each actual APC
   REG_36 write, correlate with SDR IQ to separate callback
   overhead from the physical regulator amplitude response.
2. Measure controlled single-step 10-90% rise/fall and any
   RF phase transients at 80↔100%, ideally with calibrated
   attenuated conducted measurements. Check spectral sidebands.
3. Try true synchronized phase/frequency actuation via
   native FSK along with bounded APC amplitude.
4. Fit a model with required envelope bandwidth and compare
   measured complex RF against a polar-IQ target (EVM).
5. Preserve all independent TX avenues: MICIN, tone oscillator,
   synthesizer/TCXO, subaudio, GPIO/I2S digital baseband,
   PA/CW gating, external I/Q mixer and envelope modulator.
   See docs/k6-tx-next-experiments.md.

Neither ~90-Hz AM nor the 72%-descending plateau proves
a usable arbitrary RF signal path at audio/baseband
sample rates. The 80–100% low-rate amplitude control
is nevertheless a significant second actuator alongside
the already measured FSK/frequency work.
