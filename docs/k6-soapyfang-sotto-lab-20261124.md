# SoapyFang Sotto-1200 half-duplex voice lab

This is an **opt-in local workbench feature**, not a radio firmware release.

The SoapyFang/K6 workbench has a receive-side behavioral `RFBackend` which
accepts synthetic IQ, SigMF/NumPy replays or receive-only SoapySDR sources.
The Sotto lab reuses this **same RFBackend and native sampled IQ path** with
the opt-in `VirtualTxBackend` feeding a deterministic in-memory `VirtualAir`.
No SoapySDR transmitter device or actual K6 radio is opened.

The lab compiles and runs the **actual API30 ARMv6-M Sotto transmitter**
in Unicorn against 8 kHz synthetic microphone samples; the virtual
FD6818B preloaded 33-word/4800-baud FSK profile emits IQ. The independent
RF receiver and C/Python FDV1 wire decoders must recover an *exact* stream
before the pinned native C Sotto-1200 decoder produces 20 ms PCM speaker
blocks. The strict pass oracle checks buffers, CRC, session boundaries,
codec agreement and post-TX restoration. The receiver side is not just
an analytic IQ decoder: `tools/k6_fsk_emulator_replay.receive` executes
a **compiled Cortex-M0 polling receiver fixture** against SoapyFang's
behavioral FD6818B `FskRx` FIFO, followed by independent C/Python FDV1
checks and the real *host* fixed-point speech decoder. The chip/register
model remains uncalibrated to physical 4,800-baud silicon.

## Launch with the workbench

Point to this source checkout (not a firmware image):

```sh
cd /storage5/dev/flux-sotto-flashable-duplex-20261124
~/.local/share/flux-rf-venv/bin/python -m sim.k6ide \
  --image /path/to/valid/current/flux-fd6818b.bin \
  --host 127.0.0.1 --port 5056 \
  --sotto-lab-tree /storage5/dev/flux-sotto-flashable-duplex-20261124
```

Then open `http://127.0.0.1:5056/` and use **Sotto-1200 half-duplex
voice**. You can choose two 8-kHz PCM fixtures, finite RF C/N, frequency
offset, known 2-byte prefix anomaly, ADC workload, SPI per-read latency,
and the **actual modeled 33-word FSK RX FIFO threshold/polling interval**.
The button executes **one bounded virtual session**, shows packet/codec/
SPI/PCM results and enables the browser's decoded-receiver WAV player
only after strict acceptance.

### Faster repeated tuning

Module code, native codec reference and decoder are cached by the content
SHA-256 of their compilation inputs. A changed encoder/ABI source forces a
new build, even in a dirty worktree; changing only C/N, frequency offset,
prefix fault, ADC/SPI model parameters reuses the same compiled binaries.
The cache is never shared across server sessions and is cleared at shutdown.

In one local 1.2-second fixture comparison, compilation/setup improved
**2.107 s -> 0.002 s** and complete end-to-end execution
**4.706 s -> 2.380 s** on the second identical trial (both passed
exactly). These are local development-host wall times, not RF or silicon
latency and not guaranteed across machines.

The UI's normal **Board PTT** switch still controls the emulated Aura
GPIO. The separate Sotto lab button does **not** connect to the running
firmware's module launcher or physical radio. Its state machine models
RX -> PTT TX -> RX sequentially; speaker output is forbidden while keyed
and stale PCM is discarded on failure.

HTTP API (enabled only with `--sotto-lab-tree`):

- `GET /api/sotto/state`: opt-in state and last result
- `POST /api/sotto/run`: bounded model parameters, one synchronous run
- `GET /api/sotto/audio.wav`: previous strictly accepted receiver audio

The API does not accept source paths, USB/Soapy transmitter devices, arbitrary
code, IQ uploads, or physical RF operations. It accepts one experiment
at a time. To automate, run `python -m pytest -q
tests/test_k6_soapyfang_sotto_lab.py` with the pinned Sotto submodule
initialized. A no-microphone baseline captures 9,600 samples, sends
six virtual FSK packets, verifies 30 exact 6-byte Sotto codeframes, and
produces 60 x 20 ms receiver PCM blocks. Under deliberately high ADC/SPI
load, it returns an explicit MODEL LIMIT and deletes stale playback.

## First receiver scheduling sweep (modeled)

At 1.2 seconds of mixed PCM with ADC 4,800 cycles/sample and
10 µs SPI overhead, the actual ARM polling receive fixture, SoapyFang
`FskRx` FIFO and independent FDV1 receiver reported:

| RX threshold | Poll interval | FDV1 received | FIFO overrun/underflow | Full session |
|---:|---:|---:|---:|---|
| 4 words | 1 ms | 6/6 | 0 / 0 | Pass |
| 4 words | 3 ms | 6/6 | 0 / 0 | Pass |
| 4 words | 5 ms | 6/6 | 0 / 0 | Pass |
| 6 words | 1 ms | 6/6 | 0 / 0 | Pass |
| 6 words | 3 ms | 6/6 | 0 / 0 | Pass |
| 6 words | 5 ms | 5/6 | 0 / 0 | Fail |
| 6 words | 12 ms | 5/6 | 6 / 6 | Fail |
| 6 words | 25 ms | 5/6 | 28 / 28 | Fail |

The 5-ms/threshold-6 miss **is not an overrun** by this model's
status counters; it is likely frame completion/re-arm scheduling
sensitivity. Do not falsely count it as FIFO overflow or assume that
6 words is safer than 4. These are deterministic, incomplete sweep
data, **not physical B-chip measurements** and not a proven global
threshold optimum. Keep conservative 4-word/1-ms polling as the
default until hardware-derived timings and other noise/phase cases
are validated.

## Incremental receive audio and bounded playout

The SoapyFang/Unicorn voice test uses the exact pinned native C
Sotto-1200 fixed-point decoder, taking six encoded bytes per 40-ms
superframe and emitting 160-sample/20-ms speaker blocks. The codec
requires two initial 40-ms frames of priming and two flush frames
only after verified FDV1 END. The receiver rejects malformed input,
ring overflow, and stale PCM on PTT/cancel. The host test compares
every produced PCM sample **bit-for-bit** to the original independent
batch decoder; it never substitutes ideal received source data for
IQ-derived packet bytes.

The first bounded implementation decoded each entire eight-frame
packet immediately into a **16-block PCM ring (5,120 bytes)** and
drained it before the next packet. The new **paced** receiver,
`SottoRxPaced`, queues only encoded six-byte frames at packet arrival,
then calls the native decoder on speaker demand. Its resource caps are:

| Receiver ring | First design | Paced design |
| --- | ---: | ---: |
| 20-ms PCM queued | 16 blocks / 5,120 B | 4 blocks / 1,280 B |
| 40-ms encoded frames queued | 0 | 16 frames / 96 B |
| Total explicit queued data | 5,120 B | 1,376 B |

This is a **3,744-byte reduction (73.1%) in modeled queue storage**.
The separate 320-sample/640-byte decoder temporary, native decoder
working state, Python data-structure overhead and platform scheduling
are not included in these figures. Up to 144 eight-frame segments
(1,152 codec frames) can be drained with bounded memory. A missing END
never causes a fabricated flush. **The host still drains speaker
calls as a test harness, not with the K6's 20-ms timer.** Physical
speaker underruns, decode WCET, required prebuffer delay, and combined
radio SRAM fit are therefore unproven. No silence or concealment is
silently invented as proof of uninterrupted playback.

## Limits and next optimization gates

This is not yet a **flashable two-way on-K6 Sotto** image. API30 host
services, native 4,800-baud RX FIFO/decoder, real speaker and VFO/PTT
binding still need to be integrated and fitted under the application
flash/stack/arena budget. Codebook SPI clocks and ADC timing are models
rather than hardware measurements.

**Once this SoapyFang path works, optimize measured bottlenecks** in
order: nominal and stressed PCM loss; SPI read setup/byte time; ARM
instruction budget; code size; receiver jitter/playout and speaker
latency. Keep codec bit-exact and FDV1 checks fixed while comparing
each candidate, and check any good change against the production
signed image before claiming it is flashable.
