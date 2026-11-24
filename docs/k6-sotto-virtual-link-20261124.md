# Actual live Sotto module over virtual RF — 2026-11-24

## Running

The main-branch virtual link can build the actual experimental API29 live
Sotto module from a separate checkout, without changing that checkout.
The tested development revision was
`a23a604b107aa599fe8bc5ee194231719f7c1bc5` (Sotto submodule pin
`598f1bce87c22ebada2b9d0d1fcfd89107b33a09`). This is not a merge of the
experimental radio firmware. When the module is available on main,
the same command can omit the source-tree argument.

```sh
python tools/k6_sotto_virtual_link.py --sotto-tree /path/to/sotto-checkout \\
  --cpi 1.5 --spi-us 34 --adc-cycles 0 --prefix-every 0,2

python tools/k6_sotto_virtual_link.py --sotto-tree /path/to/sotto-checkout \\
  --cpi 1.25,1.5 --spi-us 10,20,34 --adc-cycles 4800,5745 \\
  --json timing.json

python tools/k6_sotto_virtual_link.py --sotto-tree /path/to/sotto-checkout \\
  --spi-us 34 --adc-cycles 4800,5745 --cache-bytes 0,256,512,1024 \\
  --json cache.json
```

Requires the selected checkout's pinned Sotto submodule, native C compiler,
Clang/LLD/LLVM binutils, numpy, scipy and Unicorn. The tool builds the actual
catalog module via its own build script, independently compiles the native
reference encoder, and builds the actual C DV wire decoder from that same
checkout. Temporary build artifacts stay outside the source checkout.
No serial, SDR, radio, network or physical PTT is used.

```sh
FLUX_SOTTO_TREE=/path/to/sotto-checkout pytest -q tests/test_k6_sotto_virtual_link.py
```

The strict session oracle test always runs; the four actual-module tests
run when the selected checkout contains the live module and toolchain.
CI on main does not fetch a moving experimental branch.

## Pipeline and evidence

1. Synthetic 8 kHz PCM (8320 samples) arrives at the real four-block PCM
   ring on the modeled 125 us sample clock.
2. The actual Cortex-M0 live module executes its encoder, read-only resource
   lookups, FDV1 session scheduler, and async transport calls. The module
   is 10192 bytes; the test uses the existing 14400-byte arena budget
   less module and 24-byte overhead.
3. The modeled API29 service loads the 33-word TX FIFO through register
   transactions. Completion is derived from virtual RF TX-done, not a
   fixed successful callback or expected payload.
4. A separate compiled polling receiver independently acquires the
   generated IQ and reads the modeled RX FIFO. A second compiled module
   invokes the actual C wire decoder; its output is checked against an
   independent Python FDV1 oracle. Neither receives the reference codec
   bytes as input. Reassemble a single complete epoch, then compare with
   the independently compiled native encoder.
5. Reports include module SHA-256/source revision/dirty flag, samples,
   stack, PCM high-water, resource read counts and sizes, modeled TX/RX
   diagnostics, first RF time, pre-release TX count, guard recovery,
   exact codec match and wall time.

Baseline CPI 1.5, SPI 34 us, ADC cost 0, C/N 25 dB, 1 ms RX poll:
8320 samples, 26/26 codec superframes, 6/6 FDV1 packets, 600-byte stack
high-water, PCM high-water 2/4, first RF about 109 ms after capture starts,
4 packets start before release. Injecting a leading 0x00f8 on every
second packet still recovers all 26 superframes, including three shifted
CRC-valid packets. Actual C wire decoder and Python oracle agree.
This is a codec-byte loopback, not decoded speaker audio.

## First optimization sweep

The ADC model charges a specified number of cycles per 8 kHz sample
against foreground work and SPI/FD access. Explicit delays are wall time.
CPI is an assumed average, not measured per-instruction Cortex-M0 timing.
ADC 5745 = prior standalone W80 instruction estimate 5445 + assumed
300 cycles ISR overhead. ADC 6415 corresponds to W90 + overhead.
4800 is a prospective faster ADC target, not a measured result.

Each cell below is the result for the same 1.04-second synthetic clip,
no cache, no injected RF fault. Pass = all 8320 samples and all 26 codec
frames exact; fail = PCM ring overrun, module aborts.

| CPI | ADC cycles/sample | SPI 10 us | SPI 20 us | SPI 34 us |
|---|---:|---|---|---|
| 1.25 | 4800 | Pass | Pass | Fail |
| 1.25 | 5745 | Pass | Fail | Fail |
| 1.5 | 4800 | Pass | Fail | Fail |
| 1.5 | 5745 | Fail | Fail | Fail |

At CPI 1.5 and 34 us SPI, 5745 cycles/sample overran after 1040
samples (130 ms), before any completed RF packet. With no ADC CPU cost,
even 60 us SPI passed. The integrated test exposes a real design risk
that a codec-only or RF-only emulator cannot see.

A passing run performs 6244 resource reads, 5970 while keyed:
4857 x 20-byte codebook reads, 414 x 128-byte centroid reads,
387 x 4-byte reads and other small requests. A hypothetical exact-range
LRU cache with 12 bytes of metadata per entry, 2 us hits and 256/512/1024
byte budgets did not rescue the 34 us SPI failures. At ADC 4800 the
1024-byte cache hit only 26 of 800 requests before overflow. At ADC
5745 it delayed overflow from 1040 to 1280 samples. Cache is a modeled
service cost, not an implemented firmware change or an allocated RAM
budget. Prioritize reducing per-read overhead and ADC cost, and measure
whether batching/prefetch can help before allocating scarce RAM.

## Limits and next steps

The actual compiled encoder, live scheduler and C wire decoder execute;
the API29 resident Rust implementation, PA11 analog behavior, actual ISR
instructions, SPI bus arbitration, RFIC register timing and physical
watchdog are modeled, not executed or calibrated. ADC cycles are a
conservative workload charge, not proof of analog sample quality.
The RF receiver remains a compiled polling fixture, not a full
production RX/playout mode. One-way timestamped replay, no interactive
duplex or KISS integration. The fixed synthetic input is a regression
fixture, not a representative speech corpus. No hardware tests were run.

Next: measure actual ADC and SPI transaction cost, test shorter sampling
loops for fidelity, optimize encoder and read transactions, and repeat
with longer/multiple speech fixtures. Freeze a small hardware matrix
only after the model candidates pass with meaningful headroom.

## First compiled optimization candidate

[Candidate a21765a7](https://github.com/tcarwash/flux_radio/commit/a21765a786f5c1e79f77b4ef933b710a1914ab28)
on research/sotto-dct-fast-20261124 replaces the compact DCT phase
reduction's repeated subtraction with exact reciprocal arithmetic.
The expression m - ((m*205)>>14)*80 equals m modulo 80 for every
integer 0..741, the complete 20x20 input domain. The native C test
compares all 380 non-DC coefficients actually used by the encoder to
the upstream table. The unused DC row has a different normalization
in the original compact implementation; it is deliberately excluded.
Module size increases 10192 -> 10200 bytes, still within the 10240-byte
object budget including 24-byte overhead (16 bytes spare).

At CPI 1.5, compare the actual baseline and candidate binaries:

| ADC cycles/sample | SPI read cost | Baseline | Candidate |
|---|---:|---|---|
| 0 | 10 us | Pass | Pass |
| 4800 | 10 us | Pass | Pass |
| 5745 | 10 us | Fail at 3280 samples | Pass, exact 26/26 |
| 0 | 34 us | Pass | Pass |
| 4800 | 34 us | Fail at 1600 samples | Pass, exact 26/26 |
| 5745 | 34 us | Fail at 1040 samples | Fail at 2560 samples |

The candidate's successful trials preserve all codec bytes and complete
FDV1 delivery. The DCT-specific test passed after restricting the table
comparison to the actual non-DC domain; 64 other Sotto regressions
passed, two skipped in that run. This is a real source optimization,
not merely a changed model parameter. It is on a separate branch
based on the experimental live Sotto module, ready for the Sotto
integration owner to cherry-pick after review. No radio flash or
hardware validation. The remaining W80-like 34 us SPI case still
overflows; do not treat the candidate as a real-time qualification.
