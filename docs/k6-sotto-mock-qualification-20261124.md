# Sotto no-hardware mock qualification

Use this when the physical K6, microphone/serial cable, bench RF gear, or
hardware lease is unavailable. **This workflow never opens the radio, a serial
port or an SDR, installs a library, keys the transmitter, accesses the bench
lock, or flashes the device.** It is an offline diagnostic tool, not a
substitute for a final hardware proof.

## The two independent mock layers

- **KISS fake device**: `tests/test_k6_kiss_hil_fake_device.py` exercises the
  actual KISS host client, a compiled TNC image, and mocked serial/Cable/
  LiveLink/device storage, including LIST/GET/SET/STATUS/STOP and rollback.
  Mode-registry and dispatcher tests enforce that Sotto voice providers have
  zero KISS packet TX payload capability. The fake device **does not encode,
  transmit or decode Sotto voice**.
- **Actual Sotto ARM module in Unicorn**: `tools/k6_sotto_virtual_link.py`
  builds the pinned API29 live TX research module, feeds mock 8-kHz microphone
  samples into its actual executable Thumb instructions, simulates FD6818B
  4800-baud FSK and noisy IQ, and independently decodes/reassembles the wire
  session with C and Python. Its data/codebook resource reads, simulated SPI
  latency, ADC workload and stack/memory/PCM constraints are observable.
  This is the useful layer for actual codec/performance optimization.

We deliberately do **not** pretend those two independent layers form a
working Sotto KISS packet provider. The current Sotto module is voice-only;
API27 loopback packet dispatch and the experimental API29 Sotto voice service
are different codepaths and build ABIs.

## One-command qualification

From a clean Flux checkout, use the isolated Python environment with Unicorn,
NumPy and pytest installed. `--sotto-tree` must explicitly name the **actual
API29 live Sotto development checkout**, since `main` alone does not contain
`c/k6/mod/sotto_live_tx.c`:

```sh
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_mock_qualify.py \
  --sotto-tree /path/to/experimental-sotto-live \
  --json /tmp/sotto-mock-qualification.json
```

The runner executes the fake-device, installed-mode, API27 safety, and actual
Sotto virtual-link tests. Then it compiles the real Sotto ARM module plus
independent native codec, runs two deterministic PCM fixtures (triangle and
mixed-frequency), and reports nominal, slower SPI, and high-load runs plus a
2-byte-shifted RF alignment run. Only the **high-load** run is diagnostic: it
may fail with a modeled buffer overflow while the overall mock qualification
passes, provided all required runs pass, the resource oracles agree, and TX
returns with cleanup. Every scenario is recorded individually; the final
success summary does not hide diagnostic failures.

Use `--quick` to exercise only the triangle fixture; use `--skip-mock-tests`
to skip the separate fake KISS/registry pytest suite. `--spi-byte-us 0.5`
adds transfer-length-sensitive modeled SPI time instead of assuming every
transaction costs the same. This is not a measured bus clock. Generated JSON
contains per-fixture success, samples, exact codec results, packet recovery,
stack and SPI cost counters plus the source Git revision and dirty state.

For comparative optimization of two actual Sotto module trees, use
`tools/k6_sotto_opt_bench.py`; it runs both on exactly the same PCM and timing
matrix. Always compare both codec output and strict acceptance, not just
latency or instruction counts.

## Evidence limits

This can catch real module regressions (wrong codec bytes, corrupted FDV1
framing, lost PCM buffers, resource misuse, mis-sized module, unintended TX
state, mock interface regressions), but cannot certify KD32F328 interrupt
scheduling, BK/FD6818B RF silicon behavior, analog microphone/PA behavior,
frequency deviation, real SPI performance or intelligibility over the air.
If the physical bench is unavailable, keep new optimizations in research or
verified-offline branches until suitable device measurements are possible.
