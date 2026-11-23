# Flux TX-20: measured FD6818B APC amplitude staircase

Updated: 2026-11-22 local bench (November 23 UTC)

**Result:** Two independently triggered 700-ms hardware RF sequences
confirmed that a firmware-owned calibrated FD6818B APC target controls
actual carrier amplitude. An unstable low limit at 70% is reproducible.
The production firmware and the exact original slot-30 executable have
been restored and verified after testing.

## Experimental procedure

With the operator-confirmed 50-ohm dummy load and air-coupled RTL-SDR,
the radio ran the guarded API-27 APC test module at 433.500 MHz.
Both runs emitted seven consecutive 100-ms constant-APC plateaus:

    100% 90% 80% 70% 80% 90% 100%
    0xB4A2 0xA2A2 0x90A2 0x7EA2 0x90A2 0xA2A2 0xB4A2

REG_36[7:0] stayed at the calibrated UHF PA driver 0xA2;
only the upper-byte APC setpoint was altered. A firmware-owned
callback enforces exact chip ID, TX state, 433.500 MHz channel,
low-power calibrated ceiling, 70/80/90/100% allowed values,
readback, and independent TX watchdog. The experimental module
returns 7 if all seven verified writes and 100-ms holds succeed.

Both real runs returned 7, then the original module library slot
was restored with monotonic serial and executable readback;
both VFOs were returned to 144.390 MHz and TX was off.

SDR: RTL-SDR R820T, sample rate 1.024 MS/s, center 433.750 MHz,
fixed manual gain 0 dB, interleaved uint8 IQ, 5.12 seconds,
5,242,880 complex samples each, no ADC extreme/clipping.
RF onset ~0.980 s in both captures; off ~1.692 s.

## Measured relative coherent carrier amplitude

The companion offline-only, SHA-checking tool
tools/k6_apc_rf_analyze.py uses 4096-sample FFT burst detection
and 1-ms coherent carrier estimates, discarding 25 ms at
each plateau boundary. Values are normalized to the mean
of the first and last 100% stages in each recording.

| APC setpoint | Run 1 amplitude | Run 2 amplitude |
|---|---:|---:|
| 180 / 100%, start | 100.08% | 100.28% |
| 162 / 90%, descending | 90.92% | 91.00% |
| 144 / 80%, descending | 79.38% | 79.48% |
| 126 / 70% | 71.22% (intermittent) | 73.12% (intermittent) |
| 144 / 80%, ascending | 79.49% | 79.46% |
| 162 / 90%, ascending | 90.91% | 91.14% |
| 180 / 100%, end | 99.92% | 99.72% |

**The 70% stage exhibits repeatable ~8-ms-spaced deep RF fades.**
Within the central 50 ms of the 70% plateau, each of the two
independently captured runs had 14 1-ms fade bins in seven
contiguous events below 36% of the full-scale carrier.
Every 80%, 90%, and 100% plateau had zero bins below the
same threshold. Actual received raw-IQ RMS falls to
approximately 0.76-0.77 ADC magnitude on those 70%-stage
fades, approaching the receiver noise floor (roughly 0.7);
this is not merely phase cancellation in the coherent demod.

These repeats favor a real low-setpoint APC, PA or modulation
instability, but the air-coupled RTL receiver cannot isolate
its physical cause. Calibrated conducted RF monitoring would
help rule out receiver effects.

The 80-100% stages reproduce well across ascending and
descending commands, both recordings and independent
triggers. This proves amplitude controllability at
**100-ms command cadence** only. The usable amplitude
modulation bandwidth, RF attack/release time, phase continuity,
harmonic emissions, output power in watts and frequency
accuracy remain unmeasured. The ~30.5-kHz apparent
frequency offset from nominal is an *uncalibrated SDR*
measurement and must not be attributed to radio error.

The original recorder's manifest fields named
capture_only / rf_transmission_triggered reflect recorder
implementation; the embedded hardware_lab_transaction
explicitly records the radio's verified physical TX and
successful restoration. The offline analysis refuses files
without that verified transmitter transaction and SHA match.

## Archived evidence (on enrolled development host)

    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run01.npy
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run01.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run01.analysis.json

    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run02.npy
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run02.json
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-staircase-run02.analysis.json

    ~/flux-bench/tx-injection-20261122/apc-lab/flash-packed-apc.log
    ~/flux-bench/tx-injection-20261122/apc-lab/flash-restore-original.log
    ~/flux-bench/tx-injection-20261122/apc-lab/restored-original-post-apc.json

Original full 2-MiB pre-test SPI backup with factory calibration:
    ~/flux-bench/tx-injection-20261122/apc-lab/spi-pre-apc.bin
    SHA256 4533e49c7cc20dc37c83704d057613b38af39378af44da863435ab1a8d54d54b

## Firmware gates and complete rollback

The early API-27 firmware did not support the running radio's
packed KUL2 module library; compiled-image simulation caught
that blocker. The rebuilt packed firmware included
K6_USER_STORE=1 and passed compiled-image tests for API-27
callback refusal when idle, all 31 catalog entries, and
byte-identical startup with the actual 2-MiB SPI backup.

Temporarily flashed experimental firmware:
    5b80f53e0f177bbb
    ~/flux-bench/tx-injection-20261122/apc-lab/build-packed/flux-apc-b-packed.bin
    SHA256 718a01241a9fbef4d45d593f8273b221ffe5d450155ff6e47e08aabea26c6a66

**After both trials the original production firmware was
reflashed and independently verified**, not left experimental:

    Firmware build ID: 95d6b260d4122287
    Module API maximum: 26
    Catalog logical entries: 31
    Original slot-30 executable SHA256:
      6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522
    Both VFOs: 144.390 MHz
    Transmitter: OFF
    Standby: IDLE

This is a reversible lab proof of a **slow amplitude actuator**,
not a full arbitrary-I/Q synthesizer or certification of RF purity.

## Next experiments — do not lose alternate TX injection paths

1. Use a calibrated, attenuated conducted sampler and independent
   power meter to characterize and reproduce the ~8 ms 70%-step
   fade, ruling out SDR artifacts.
2. Add tightly bounded, safe **75% and 85%** amplitude plateaus
   in an opt-in lab image to locate the dropout boundary.
   Preserve the calibrated low-power target ceiling.
3. Measure amplitude attack/release response, settling, phase
   disturbance and occupied spectrum at shorter step durations,
   only after the physical setup and emissions are confirmed.
4. Combine native-FSK phase/frequency actuation with APC
   amplitude control and validate simultaneous synchronization.
5. Continue MICIN/ADC, tone oscillator, subaudio, LO/synthesizer,
   PWM/I2S, CW/PA gating and external IQ/mixer routes in
   docs/k6-tx-next-experiments.md. Do not assume APC eliminates
   the need for an external RF envelope actuator.
