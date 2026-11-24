# K6 CW register-write replay → SoapyFang (offline)

**2026-11-24. Research-only, no physical TX or installable CW firmware change.**

This builds on `sim/k6emu/rf/tx_trace.py` and the five-mode SoapyFang
modulation laboratory. The missing piece was a **causal bridge between the
emulator's timestamped FD6818B three-wire write requests and predicted
analog CW envelope**, without pretending that firmware register write intent
is equivalent to actual RF output.

## What it does

1. `K6Board(..., capture_tx_events=True)` can record **actual virtual three-wire
   bus writes** (cycle, register, value) made to the K6 emulator. Its previous
   `fd_writes` behavior remains unchanged. The production firmware is not
   modified or keyed, and the default trace capture remains disabled.
2. `sim/k6emu/rf/tx_trace_replay.py` converts the register writes into a
   *hypothetical* RF envelope, applying each write **at or after its recorded
   time**, never before. REG36[5:0] is normalized as driver gain; REG19[3:0]
   can be modeled as bias; the user must choose which gain model to test.
   Unknown initial gain and gate states are treated as **off**. Writes after
   the end of the capture have no influence on previous samples.
3. The gate is **only a declared hypothetical REG30 bit mask**, not a claim
   that REG30[3] actually gates the physical K6 PA. In particular, Aura's
   known TX-on register values do not establish an independent RF PA gate
   through that bit. External traces require an explicit `--gate-mask`; if
   absent, the CLI refuses to interpret an RF envelope. Real RF PA GPIO
   gating and APC have not been calibrated into this model.
4. `tools/k6_cw_register_replay.py` generates a test-only register sequence
   at 5, 117, 250 or 1000 µs per write, serializing to **one command per
   complete transaction**. It prepares nonzero gain before simulated key-down,
   prioritizes key-up before gain cleanup, discards stale intermediate ramp
   values, and never assumes simultaneous gain/gate writes. It replays the
   resulting carrier IQ through **SoapyFang's actual RX channel filtering**
   and checks an independent host envelope decoder and FFT leakage against
   the target 20-WPM “CQ” 5-ms raised-cosine shape.

The script reports sample count, source register commands, key decision
accuracy, maximum amplitude deviation, 50-Hz-outside FFT leakage ratio,
and receiver result in JSON. The default strict result requires the actual
SoapyFang IQ to recover the keyed signal, >=99.5% key samples correct,
<=0.10 maximum normalized envelope error, and negligible final off-state
energy. All are **model targets**, not RF emissions or on-device key-up
certification.

## How to reproduce

```sh
python tools/k6_cw_register_replay.py --write-us 117 \
  --json /tmp/flux-cw-regs-117.json

# Hypothetical fast 3-wire transfer (not a validated clock)
python tools/k6_cw_register_replay.py --write-us 5 \
  --json /tmp/flux-cw-regs-fast.json

# Expected strict-envelope failure: too few commands for the ramp
python tools/k6_cw_register_replay.py --write-us 1000 \
  --json /tmp/flux-cw-regs-slow.json

# Independently hypothesized PA analog rise/fall, **not** measured on this path
python tools/k6_cw_register_replay.py --write-us 117 \
  --attack-ms 5.25 --release-ms 0.765 \
  --json /tmp/flux-cw-analog-sensitivity.json

# With a real *emulated K6Board* bus trace from a test or firmware run
python tools/k6_cw_register_replay.py \
  --trace /path/to/virtual-trace.json --gate-mask 0x8 \
  --json /tmp/flux-cw-bus-replay.json

python -m pytest -q tests/test_k6_cw_register_replay.py
```

Trace file shape: `{ "clock_hz": 96000000, "writes": [[0,48,0],
[24000,54,63], [48000,48,8], [96000,48,0]] }`. The external trace is
*inert input data* only; the bridge does not issue it to a real radio.

## Results on host model, 30-dB virtual receiver SNR

| Synthetic register-write completion interval | Writes | Max normalized envelope error | Modeled strict gate |
| ---: | ---: | ---: | --- |
| 5 µs | 1025 | 0.0084 | Pass |
| 117 µs | 561 | 0.0383 | Pass |
| 250 µs | 297 | 0.0741 | Pass |
| 1000 µs | 89 | 0.5568 | **Fail** |

The 117-µs model had 99.9606% key decision accuracy; 1000-µs still had a
high key classification accuracy (99.8981%), **but did not preserve the
shaped transition**. This is why key decisions alone cannot qualify good
sounding CW. Out-of-band leakage above 50 Hz was ~1.007 times the *ideal
shaped* signal at 117 µs, increasing to ~1.172 times at 1000 µs. These
FFT values are not calibrated occupied-bandwidth or spectral-mask results.

A schematic analog driver response using 5.25-ms attack and 0.765-ms release
at the 117-µs register rate **fails**, with key decision accuracy ~98.38% and
max envelope error ~0.682. These two time constants came from older RF
keying observations at another operating configuration; applying them to a
software-controlled gain register is **hypothetical**, not a fitted physical
model. Successful fast register updates do not imply a linear/monotonic PA.

A regression exercises the complete **test-generated register words → actual
K6Board virtual three-wire bus decoder → timestamped captured writes →
causal RF envelope → SigMF → SoapyFang receiver → host envelope checks**.
No generated trace is treated as a real hardware capture. Tests also cover
unknown gate/gain state, equal-time writes, late commands, simulated PA tail,
invalid register masks, and failure reporting. For the other five waveform
families and their mixed FSK/tone/subaudio/MIC and hypothetical polar routes,
see [`k6-tx-path-combination-lab-20261124.md`](k6-tx-path-combination-lab-20261124.md).

## Engineering next steps

- Compare register update-write budgets with actual Aura instruction timing,
  not the ideal slot scheduler. Prefer fewer writes, monotonic gain tables,
  and reliable immediate key-up to uncontrolled fast APC target changes.
- Add physical PA-gating GPIO trace and an experimentally **measured**
  amplitude transfer curve. Until then, REG30 mask and normalized gain are
  only abstract assumptions. No PA/RF silicon is represented here.
- Keep native FSK FIFO modulation and real microphone FM separate from
  register envelope replay: register writes alone are **insufficient to
  reconstruct FIFO symbol payload, or a microphone waveform**. Do not claim
  the RF modem is exercising SSB, QPSK or OFDM via these controls.
- Existing queued TX-32 (#35) and TX-33 (#36) provide a controlled path for
  later actual hardware evidence under an exclusive lease, with no automatic
  authorization to transmit, flash, or change hardware configuration.
