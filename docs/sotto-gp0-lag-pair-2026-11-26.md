# Flux K6 Sotto: paired pitch lag correlation (offline)

**Date:** November 26, 2026 (Pacific). **Branch:** `optimize/sotto-lag-pair-20261126`.
**Status:** isolated Cortex-M0 compilation and virtual RF qualification; **NOT signed, installed, or tested on the physical radio**.

## Why this path

Previous read-only on-radio diagnostics saw the four-block GP0 PCM queue fill, with a 14.8 ms synchronous FSK preload and 506 table reads occupying 32.7 ms in a roughly 160 ms capture. The instruction-level profile attributes ~10.03 MHz to `slim_analyse10`, ~6.24 MHz to `decide`/envelope, and ~1.82 MHz to codebook vector scoring. The earlier exact divider, aligned PCM history copies, and paired codebook-score loop improved progress but still overflowed in the deliberately harsh CPI=3.0 / 4,000-ADC-cycle model.

In the pinned Sotto encoder, the 4 kHz `lag_search` cross-correlation uses a fixed 120-sample window sampled with `SUB2=2`, i.e. 60 multiply/shift/accumulate operations for each of 75 candidate lags. The **new opt-in `fast_lag_pair` option** emits two consecutive operations in each loop iteration instead of one. Every product retains its original right shift, and every accumulation stays in the same order. Compile-time checks require the pinned `SUB2=2` and an even number of iterations. These changes are confined to the generated K6-specific C code: upstream Sotto and the normal/default build are unchanged.

## Module budget

| Build | Bytes | Required scratch | Scratch margin |
|---|---:|---:|---:|
| Fielded baseline, no optimization | 9,784 | 4,080 B | 48 B |
| Three earlier optimizations (`fast_div`+`fast_history_copy`+`fast_cb_pair`) | 9,756 | 4,080 B | 76 B |
| Same three + paired pitch correlation | **9,784** | 4,080 B | **48 B** |

The new four-way candidate is 28 bytes larger than the three-way candidate but still fits the 14,976-byte arena with the original 48-byte minimum scratch margin, same entry offset (16), and no change to codebook/IVF deck resources. The builder now rejects stand-alone paired-lag configurations that shrink the scratch margin below 48 bytes. No extra PCM buffers, ISR work or new module state are required.

The offline CLI built the unsigned candidate into `/tmp/sotto-lag-all4-20261126/` with all four experimental flags enabled. `module.bin` SHA256: `9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0`; 4,182-byte deck SHA256: `5d05fa9fb0194eee04356c3cb4b15471c780d0a915ee67063ed91a54e64ce9f6`. These are **not** a signed/installable firmware release.

## Virtual RF result, measured-SPI fit

These values come from the compiled actual Cortex-M0 module in the Unicorn K6/GP0 emulator, with the existing independent native codec oracle, packet decoder and virtual RF link. The SPI cost is 6.1921296 µs setup plus 0.9403935 µs/byte; FD operations are modelled at 60 µs. **CPI and ADC-cycle settings are stress assumptions, not measured MCU throughput.** Sample counts are completed microphone samples *up to overflow* on failure cases, not audio delivered successfully to the decoder.

| Fixture / model | Three-way | Four-way |
|---|---:|---:|
| 1.04 s tone; CPI 3.0, ADC 4,000 | 2,880 samples, overflow | **5,440 samples, overflow** |
| 1.20 s mixed; CPI 2.5, ADC 4,000 | 9,600 samples, pass | 9,600 samples, pass |
| 1.20 s mixed; CPI 3.0, ADC 4,000 | 3,840 samples, overflow | **4,800 samples, overflow** |
| 3.00 s mixed; CPI 2.5, ADC 4,000 | 24,000 samples, pass | **24,000 samples, pass** |
| 3.00 s mixed; CPI 3.0, ADC 4,000 | 6,080 samples, overflow | **7,680 samples, overflow** |

The 3-second passing four-way run returns **75 exact codec frames**, **12 valid RF packets**, zero invalid, agreement with independent wire decoding, and PCM high-water **2/4** (vs 3/4 for the earlier candidate). TX ends inactive; one GP0 start/stop occurs. No sample was lost. In all the harsh-model failures, the PCM queue eventually still fills (4/4); the engine returns `-12`, stops GP0 capture and dekeys. This failure handling is essential and is **not** a continuous audio pass.

Raw results: [`docs/evidence/sotto-gp0-lag-pair-sweep-20261126.json`](evidence/sotto-gp0-lag-pair-sweep-20261126.json). Focused regression: `tests/test_k6_sotto_lag_pair.py`.

**Validation:** the focused paired-lag suite passed 4/4 tests; the complete 11-file Sotto/GP0 regression selection passed **75/75** (including real compiled ARM modules, native reference bitstream checks, virtual RF links, cancellation/dekey and packaging safety gates). All runs were offline.

## FSK synchronous preload finding (research only)

`firmware/k6-aura/sotto-api29-async-fsk.rs.inc` currently performs the entire FSK start (`op=0`) in one synchronous API call. That includes a one-time nine-register snapshot, PA enable, mode transitions and 33 FIFO word writes, with validity/lease checks at each stage. It **already** makes the *subsequent* 20 ms FIFO settling delay nonblocking, but the initial preload itself does not yield. This matches the physical 14.8 ms foreground stall.

A future *separate, host-level* experiment can stage the preload across repeated `op=1` polls in short word batches, retain its existing TX lease and register snapshot, then start RF only after all 33 words were acknowledged written and the usual 20 ms settling interval elapsed. It must also explicitly abort and restore the saved registers on PTT release, power loss, timeouts, RFIC write failure or unexpected operation. The module's poll throttle is 5 ms, so splitting writes changes start latency; deadlines and FIFO ownership need requalification. **No such API/host change is implemented in this branch.**

This batch strategy attacks the contiguous stall; it does not reduce the total number of SPI operations or prove sustainable encoder throughput. Further pitch/envelope profiling and physical-free simulator tests remain necessary before any radio flash or RF validation.
