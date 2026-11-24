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
codec agreement and post-TX restoration.

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
offset, known 2-byte prefix anomaly, ADC workload and SPI per-read latency.
The button executes **one bounded virtual session**, shows packet/codec/
SPI/PCM results and enables the browser's decoded-receiver WAV player
only after strict acceptance.

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
