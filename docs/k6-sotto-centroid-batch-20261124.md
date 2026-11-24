# Sotto centroid batching — experimental offline comparison

Experimental source branch, based on the DCT optimization plus elimination of
known-zero FFT imaginary swaps. This branch **does not authorize radio TX or a
firmware flash** and is not included in `main`.

The pinned upstream Sotto source remains unchanged. The temporary generated
codec uses `IVF_BLK=32` instead of 16. `sotto_coder_work_t` places the
256-byte centroid block in its existing 1,024-byte union workspace, checked
by `_Static_assert`. The Sotto resource adapter now fetches 256 bytes per
centroid block, without changing the order of centroid comparisons.

## Offline results

Compared with the 128-byte FFT/DCT baseline:

| Metric, 1.04 s triangular PCM, unconstrained timing | Before | 256-byte batch |
|---|---:|---:|
| Resource read transactions while keyed | 5,970 | 5,763 |
| Bytes requested while keyed | 159,434 | 159,434 |
| Module code bytes | 10,192 | 10,196 |
| Code plus 24-byte envelope | 10,216 | 10,220 |
| Available under 10,240-byte limit | 24 | 20 |
| Stack high-water | 600 B | 600 B |
| Exact codec / received session | Pass | Pass |

Mixed-frequency fixture: 5,663 -> 5,424 resource transactions, again with
identical 157,016 bytes transferred in the successful zero-ADC workload.

With modeled CPI=1.5, SPI setup/read cost 10 or 34 µs, and added 0.5 µs per
returned byte, six trial pairs across triangular and mixed-frequency input
had **two pass -> pass, four fail -> fail, no regressions and no recovered
passes**. At ADC 4,800 cycles and 34 µs setup, the candidate survived 3,200
vs 2,560 triangle samples and 6,720 vs 6,080 mixed samples, but *neither*
finished the session. All passing sessions were bit-exact across native codec
and independent C/Python RF decoders. The C/module tests passed (22/22).

Run again with the optional SPI-byte model:

```sh
python3 tools/k6_sotto_opt_bench.py \
  --baseline /path/to/live-dct-fft \
  --candidate /path/to/live-dct-fft-batched \
  --include-mixed --spi-byte-us 0.5 \
  --case 0:10 --case 4800:34 --case 5745:34 --strict \
  --json /tmp/centroid-comparison.json
```

**Do not merge into main as a physical optimization yet.** The actual SPI
transfer clock/overhead and max service read length need verification on the
FD6818B and host resource API; a 256-byte read in Unicorn is not a hardware
qualification. `--spi-byte-us=0` preserves the old constant-per-read model
and can overstate the advantage of larger transfers.
