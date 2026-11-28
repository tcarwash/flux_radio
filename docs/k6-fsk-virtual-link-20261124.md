# Virtual native-FSK link and parameter sweeps — 2026-11-24

## Workflow

Two independently compiled Cortex-M0 fixtures execute through the module
ABI. The sender writes a finite preloaded TX FIFO and waits for the modeled
TX-done interrupt. Its register transactions create timestamped 2FSK bursts.
The receiver runs on the same virtual timeline in a second emulator, with
an IQ source that renders those bursts plus seeded complex noise. It acquires
preamble/sync and reads its eight-word RX FIFO. It is not given the sender's
payload, expected frame count, CRC, or start times.

The two runs are sequential (one-way timestamped replay), not a concurrently
scheduled interactive duplex network. The backend has no physical TX sink,
does not open a serial port or SDR, and is not installed as a radio module.
Ordinary RFBackend remains receive-only. The compiled fixtures use actual
module register calls, but are not the production Sotto/KISS firmware.

Run from the repository root with numpy, unicorn and the existing clang/lld/
llvm-objcopy/llvm-nm module build toolchain. For example:

```sh
python tools/k6_fsk_virtual_link.py --packets 4 \\
  --deviation 900,1800,2700 --offset 0,1500,3000 --cn 0,8,16,25 \\
  --json rf-sweep.json --csv rf-sweep.csv

python tools/k6_fsk_virtual_link.py --packets 4 \\
  --threshold 2,4,6 --poll-us 1000,5000,10000,20000 --gap-us 0,12000 \\
  --json fifo-sweep.json --csv fifo-sweep.csv

python tools/k6_fsk_virtual_link.py --packets 4 \\
  --threshold 4,6 --poll-us 1000,10000 --lead-us 30000,32750,36500 \\
  --seed 1,2 --gap-us 12000 --summary-json timing-ranked.json

python tools/k6_fsk_virtual_link.py --packets 4 --prefix-every 0,2 --guard both
```

Each comma-separated argument is a Cartesian sweep axis. Other axes include
`--clock-ppm`, `--signal-dbm`, `--rate` (one sample rate per run), and
`--prefix-every`. The default cap is 256 trials; raise `--max-trials` to
at most 4096 for a deliberate larger experiment. Build the fixtures once
per process. JSON and CSV include settings, exact delivered packets, CRC
valid count, raw exact frames, positional bit errors (null when counts
differ), guard recovery offsets, FIFO overruns/underflows, TX completion,
stack use and wall/virtual time. The optional ranked summary groups by
configuration across noise seeds and start phases, sorting by worst
delivery, then mean delivery and FIFO errors. Compare configurations at
the same channel conditions; do not interpret a higher C/N as a firmware
improvement.

C/N is carrier power divided by noise power in a reference 25 kHz
bandwidth. It is not a measured radio sensitivity or Eb/N0. Deviation is
the explicit waveform frequency shift, not a calibrated mapping from
REG70 gain code. Absolute signal power scales both carrier and noise
together at fixed C/N. Clock ppm changes the sender's symbol rate, while
the RX model retains its fixed nominal 4800-baud clock. Lead time varies
the relative FIFO polling phase. Gap is an additional delay after the
sender sees completion; register writes and polling add further delay.

The sender uses real L64 framing, whitening and CRC around deterministic
synthetic six-byte codec slots, not valid speech. Score exact delivered
original packets by multiset intersection after independent CRC checking.
The 66-byte guarded profile tests only CRC-valid windows at offset 0 or
2, and only accepts an unambiguous result. Raw frame differences remain
visible; the demodulator does not repair them.

## Initial evidence

84 sweep trials (36 RF, 24 FIFO, 24 phase/seed) plus four guard controls,
each sending four packets. These are finite modeled experiments, not
statistical confidence bounds. Most trials took 0.1–0.5 seconds locally,
with the initial compilation adding overhead.

### RF sweep (one seed, 4 packets per cell)

Each cell is the total exact packets over offsets 0, +1500, +3000 Hz
(12 sent). Other settings: 240 kS/s, -70 dBm, threshold 4, poll 1 ms,
gap 12 ms, no guard or injected fault.

| Deviation | C/N 0 dB | 8 dB | 16 dB | 25 dB |
|---|---:|---:|---:|---:|
| 900 Hz | 0/12 | 0/12 | 0/12 | 12/12 |
| 1800 Hz | 0/12 | 0/12 | 12/12 | 12/12 |
| 2700 Hz | 0/12 | 0/12 | 12/12 | 12/12 |

This suggests testing 1800–2700 Hz deviation first in this model, not
changing the real radio's gain without a calibrated RF capture.

### FIFO sweep (C/N 25 dB, deviation 1800 Hz, one seed)

Each cell is exact delivered packets, gap 0 ms / 12 ms.

| RX threshold | Poll 1 ms | Poll 5 ms | Poll 10 ms | Poll 20 ms |
|---|---|---|---|---|
| 2 | 4/4 / 4/4 | 2/4 / 4/4 | 0/4 / 0/4 | 0/4 / 0/4 |
| 4 | 4/4 / 4/4 | 3/4 / 4/4 | 3/4 / 4/4 | 0/4 / 0/4 |
| 6 | 4/4 / 4/4 | 3/4 / 4/4 | 4/4 / 4/4 | 0/4 / 1/4 |

The 24 additional phase/seed trials varied threshold 4/6, poll 1/10 ms,
three lead times and two noise seeds, with 12 ms gap. Threshold 4 at both
poll intervals and threshold 6 at 1 ms delivered 24/24 per setting.
Threshold 6 at 10 ms delivered 23/24 with one FIFO overrun. This is why
the initial single-phase 10 ms result must not be treated as a guarantee.
A conservative initial modeled candidate is threshold 4, 1 ms polling,
12 ms additional gap, followed by phase/seed/clock-error sweeps and
physical validation. Lower threshold is not automatically safer if
software only drains a fixed number of words per IRQ.

### Guard controls

| Explicit fault cadence | Guard | Exact original packets | Raw exact frames | Valid offset 2 |
|---|---|---:|---:|---:|
| None | Off | 4/4 | 4/4 | 0 |
| None | On | 4/4 | 4/4 | 0 |
| Every second frame | Off | 2/4 | 2/4 | 0 |
| Every second frame | On | 4/4 | 2/4 | 2 |

The optional fault replaces the final transmitted word with an inserted
leading 0x00f8 word (wire bytes f8 00), preserving frame length. It models
the observed corruption explicitly; it does not explain its cause.
The guard result is consistent with the earlier FSK-13/14 physical
evidence, not new hardware evidence.

## Validation and boundaries

- 50 focused regressions passed, including TX completion/IRQ timing,
  short FIFO rejection, overflow rejection, abort, chunk-independent IQ,
  compiled loopback, guard corruption, slow polling and large clock error.
- Existing FSK-10B and FSK-12 archive replay remains available with
  configurable frame length, FIFO threshold and polling interval.
- TX supports only the finite preloaded direct 4800 profile; 64-word TX
  capacity is a model assumption. No refill, PA gating, analog gain
  calibration, oscillator recovery, collision protocol or hardware CRC.
- No physical hardware accessed; no bench lease required.
- Next: compare model predictions with a small frozen hardware parameter
  matrix; calibrate deviation and FIFO timing, then connect actual
  production modules and implement interleaved bidirectional scheduling.

## Actual live Sotto module

The same virtual RF path now accepts the actual API29 live Sotto TX
module from a selected development checkout, with modeled microphone
capture, resource reads and asynchronous FSK, and the actual C DV wire
decoder on the receive side. See [integrated codec/transport timing
sweeps](k6-sotto-virtual-link-20261124.md).
