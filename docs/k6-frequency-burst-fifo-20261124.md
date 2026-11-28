# Frequency modem: blind-start acquisition and FD6818B FIFO receive replay

Compiled multi-burst FIFO clock recovery and parity reconstruction: [`k6-native-fsk-burst-clock-parity-20261124.md`](k6-native-fsk-burst-clock-parity-20261124.md).

**2026-11-24 · Offline SoapyFang/RFIC simulation only.** No physical RF TX,
radio flash, live serial, or new production modem/KISS provider was created.

This follow-up to [`k6-frequency-modem-soapyfang-20261124.md`](k6-frequency-modem-soapyfang-20261124.md)
removes the **known frame start** assumption from the host reference decoder,
then exercises **actual SoapyFang FD6818B native-FSK RX register/IRQ/FIFO
behavior** rather than treating a floating-point FM discriminator as proof of
a packet modem. Two distinct receiver paths are intentionally retained.

## 1. Unknown-start pilot/sync reference acquisition

`sim/k6emu/rf/burst_acquisition.py` searches every integer sample phase and
possible frame start for a normalized correlation with *public pilot symbols*.
It estimates frequency offsets and the 2-/4-level constellation means **only
from that pilot**, validates the fixed **public sync word**, and only then
makes hard decisions on the held-out random payload. It does not use the
payload contents to find the start, tune timing, choose gain or correct
polarity. For 4-FSK, actual two-bit Gray BER is distinguished from symbol
error rate. A **separate strict monotonic-level-order gate** rejects reversed
signed-deviation hardware assumptions even when pilot-relabelled BER equals
zero. The receiver requires the configured symbol rate, pilot/sync, and full
packet size; it is **not** a production unknown-baud demodulator, blind packet
length detector, phase tracker or frequency-drift recovery loop.

`tools/k6_frequency_modem_lab.py --blind-start` inserts an unknown lead interval
(e.g. 17 or 1,289 samples), adds an arbitrary carrier-frequency offset,
passes the entire synthetic IQ recording through SoapyFang SigMF → actual
`RFBackend` → `ChipModel` channel filter, then acquires from the resulting
FM discriminator. SoapyFang's batched RSSI register reads intentionally skip
advances under 1 ms; the runner now explicitly consumes the last partial
recording tail to avoid losing sub-millisecond lead offsets. The legacy
`decode_pilot` fixed-start tool remains available for comparison.

### Validated 30 dB SNR examples

| Mode / assumed bandwidth | Unseen burst start | Carrier offset | Result |
| --- | --- | --- | --- |
| Native 2-FSK, 4,800 baud | 0, 17, 1,289, 2,903, 3,800 samples | 0, +700, −500, +150, +400 Hz | Lock; 0 payload errors |
| Binary subaudio, 300 baud, **modeled** 135-Hz LPF | Same offsets | Same | Lock; 0 payload errors |
| Hypothetical signed-DAC 4-FSK, 300 baud, **unverified** 350-Hz LPF | Same offsets | Same | Lock; 0 payload errors |
| Binary subaudio, 600 baud, modeled 135-Hz LPF | 1,289 samples | +230 Hz | Lock; 0 payload errors |
| Binary subaudio, 1,200 baud, modeled 135-Hz LPF | 1,289 samples | +230 Hz | **Fail**; held-out errors |

**Notable improvement:** the previous *fixed-start pilot decoder* failed
simulated 600-baud subaudio at modeled 135-Hz bandwidth. The new pilot/sync
start-and-sampling-phase acquisition finds a valid receiver timing and
recovers the held-out payload. This is a software receiver improvement, **not**
a demonstration that the K6 subaudio transmit path passes 600 baud. A
separate noise-only recording, deliberately wrong sync, corrupted held-out
symbol, and inverted multilevel frequency mapping are regression negatives.

## 2. Native FIFO register receive (the actual K6 emulator path)

`tools/k6_frequency_fifo_replay.py` generates a **complete synthetic direct
4800-baud native-FSK frame**: 16 bytes of alternating preamble (`0x55`), 4
public sync bytes (`076B3E36`), and a 64-byte seeded payload. It adds a
sample-accurate arbitrary leading gap, bounded frequency offset, noise,
selectable inverted RF mark/space polarity and a final receiver tail. The
receiver **is not told** the burst start, symbol bits, or payload. It sees
only IQ through the actual SoapyFang `SigmfSource`, `RFBackend`, channel FIR,
and `FskRx` state machine.

The runner writes the existing modeled FD6818B RX registers (`REG58`,
`REG72`, `REG5A/5B` sync, `REG5C/5D/5E` length/threshold, `REG59` arm and
`REG3F` interrupts), then polls `REG0C`, clears/latches `REG02`, and drains
`REG5F` **two-byte FIFO words** on `FULL` and `DONE` interrupts. It compares
the resulting bytes to the known transmitted payload **only after** all
register reads. Its pass criterion also requires exactly one completed frame,
at least one sync and done interrupt, and **zero FIFO overruns, underflows or
sampling gaps**. This is a radio-chip **behavioral emulator**, not physical
RFIC silicon or a compiled on-device modem validation.

### Native-FSK replay results (modeled)

- Leads 0, 13, 1,459, 5,231, 9,777 and 22,000 samples, both RF polarities,
  offsets up to ±1,300 Hz: successful sync and exact 64-byte payload recovery
  at 25 dB modeled input SNR, with no FIFO errors.
- Polling 1, 5 and 15 ms: exact bytes with no modeled FIFO overrun in the
  tested fixture. **25 ms polling fails**, with 16 model FIFO overruns;
  an apparent completed frame is **not** counted as a packet success.
- Missing RF carrier, intentionally incorrect 4-byte sync, and one injected
  payload-bit corruption: all correctly **fail exact-payload qualification**.
  Bit corruption may still deliver a full FIFO frame since hardware CRC is
  disabled; the independent payload check catches it.
- The transmitter half of this test is host-generated FM waveform math, not
  the compiled K6 native-FSK FIFO TX state machine. For compiled TX/RX virtual
  links, keep running `tests/test_k6_virtual_link.py` separately.

## Run

```bash
# Native 4800-baud receive registers, IRQ and FIFO with uncertain burst start
python tools/k6_frequency_fifo_replay.py --lead-samples 9777 \
  --polarity -1 --offset-hz 700 \
  --json /tmp/flux-native-fifo-burst.json

# Reproduce a poll-overrun failure (returns nonzero, writes diagnostic JSON)
python tools/k6_frequency_fifo_replay.py --poll-ms 25 \
  --json /tmp/flux-fifo-overrun.json

# New unknown-start reference acquisition; no payload oracle in demodulation
python tools/k6_frequency_modem_lab.py --path subaudio --baud 600 \
  --bandwidth-hz 135 --blind-start --lead-samples 1289 \
  --offset-hz 230 --json /tmp/flux-subaudio-blind.json

# Hypothetical signed 4-FSK, not a validated radio mode
python tools/k6_frequency_modem_lab.py --path signed_dac --baud 300 \
  --bandwidth-hz 350 --blind-start \
  --json /tmp/flux-4fsk-blind.json

python -m pytest -q tests/test_k6_frequency_fifo_replay.py \
  tests/test_k6_frequency_blind_acquisition.py
```

## Practical engineering decisions

- Native-FSK FIFO and short, guarded RX polling remain the best evidenced
  route for real Sotto/KISS data, *separate from the still-unproven QPSK or
  signed-DAC multi-FSK hardware trajectories*. The SoapyFang behavioral FIFO
  cannot prove physical modulation, emissions or transmitter timing.
- Better pilot/sync timing can recover symbols that a fixed sampling phase
  loses. That is worth carrying into a future *small* C decoder module, but
  MCU cycle count, memory budget, ARM execution and register ABI must first
  be qualified. The new NumPy search is not flashable as-is.
- Next offline gates: blind re-acquisition after adjacent bursts, byte-stream
  framing/FEC/interleaving under dropped words, clock-drift tolerance, and
  end-to-end compiled modem integration. Physical phase/frequency transient
  evidence is queued at [TX-34 #37](https://github.com/tcarwash/flux_radio/issues/37)
  and requires a separate exclusive bench lease and explicit approval.
