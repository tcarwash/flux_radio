# K6 AFSK audio bench (host-only Bell 202 quality reference)

Deterministic, host-only benchmark for comparing upcoming PA11 PCM samples
against existing GP0-derived audio. Generates repeatable 48 kHz
continuous-phase Bell 202 (1200/2200 Hz, 1200 baud) PCM from a fixed seed
and known bit array, then scores WAV/PCM with a documented
**known-symbol-boundary reference** two-tone detector.

**This is NOT a production packet modem and makes no RF performance
claims.** There is no NRZI, no HDLC, no CRC, no addressing, and no packet
decode of any kind. BER is measured against the known transmitted bits
with known symbol timing — never by blind decoding. Do not quote its
numbers as packet-delivery or over-the-air sensitivity figures.

## Files (new; nothing existing was modified)

- `tools/k6_afsk_audio_bench.py` — standalone bench (numpy + stdlib only).
- `tests/test_k6_afsk_audio_bench.py` — determinism, spectral identity,
  phase continuity, clean BER=0, noisy non-ideal behavior, CLI roundtrip,
  no-packet-decode-claims.
- `docs/k6-afsk-audio-bench.md` — this file.

## Design constraints (deliberate)

- Standalone: imports only numpy + stdlib. It does **not** import
  `sim/k6emu/aprs_signal.py`, `sim/k6emu/aprs_slicer.py`,
  `sim/k6if/aprs_mf.py`, and does not touch shared digital-modem APIs,
  CW, GP0, firmware, radio controls, live serial, `/dev/ttyUSB*`,
  or the network. AFSK scoring and GP0/PCM acquisition stay independent.
- No assumption that FD6818B PA11 works; future PA11 PCM is scored by the
  same reference detector from a WAV file, which is exactly the Flux
  comparison this bench exists for.
- Generated data defaults to `/tmp/k6-afsk-bench` (outside git).
  `gen` refuses an output dir inside a git checkout unless passed
  `--allow-repo-out`. (`*.wav` is git-ignored regardless.)
- Bit→tone mapping is a raw-symbol test convention: `1` → 1200 Hz MARK,
  `0` → 2200 Hz SPACE. This is **not** AX.25 NRZI (where tones encode
  bit *transitions*), so these sequences are not packets.

## Method

**Synthesis** (`generate_afsk`): per-symbol tone from the known bits,
phase accumulated sample-by-sample (`phase += 2πf/fs`) so the waveform is
continuous across symbol boundaries by construction. Optional injected
noise is deterministic band-limited AWGN (800–2800 Hz brick-wall via FFT,
seeded PRNG) scaled so `10·log10(signal/noise) == --snr-db`, with the sine
power `A²/2` as the signal reference. Output is hard-clipped to
`[-clip, clip]` (default ±1.0).

**Reference detector** (`analyze`): for each symbol window (exactly 40
samples at 48 kHz/1200 baud, starting at sample 0), correlate against
unit cos/sin references at 1200 and 2200 Hz (Goertzel-style quadrature
energies). Larger MARK energy decides `1`, larger SPACE energy decides
`0`. Reported per symbol: `scores[k] = (E_mark − E_space)/(E_mark +
E_space)` in [−1, 1]. Aggregate: `errors`, pre-FEC `ber` (raw decisions
vs known bits; no coding gain assumed), `margin_db` (10·log10 of
winner/loser energy sums — a bench margin proxy, **not** a calibrated
channel SNR), `sample_rate`, `peak_abs`, `clipped_fraction`.

## Usage

Generate a test WAV plus metadata/expected bits (defaults shown):

```sh
python3 tools/k6_afsk_audio_bench.py gen \
  --out-dir /tmp/k6-afsk-bench --n-symbols 1200 --seed 1 \
  --sample-rate 48000 --amplitude 0.7
# writes /tmp/k6-afsk-bench/afsk-bench.wav + afsk-bench.meta.json
```

Score it against its known bits:

```sh
python3 tools/k6_afsk_audio_bench.py analyze \
  --wav /tmp/k6-afsk-bench/afsk-bench.wav \
  --meta /tmp/k6-afsk-bench/afsk-bench.meta.json
```

Expected clean output: `"errors": 0, "ber": 0.0`, `margin_db ≈ 14`,
`peak_abs ≈ 0.7`, `clipped_fraction: 0.0`.

Generate a degraded case (0 dB in-band SNR; the detector must err):

```sh
python3 tools/k6_afsk_audio_bench.py gen \
  --out-dir /tmp/k6-afsk-bench --prefix afsk-noisy --snr-db 0 --seed 1
python3 tools/k6_afsk_audio_bench.py analyze \
  --wav /tmp/k6-afsk-bench/afsk-noisy.wav \
  --meta /tmp/k6-afsk-bench/afsk-noisy.meta.json
# expect ber ≈ 0.17, margin_db well below the clean run
```

Score an externally captured WAV (e.g. future PA11 PCM, resampled to
48 kHz mono 16-bit): pair it with a `.meta.json` holding the known
transmitted `bits` (+ `sample_rate`, `baud`) and run `analyze`. The WAV
must start on a symbol boundary — the detector does no clock recovery,
by design.

## Checks

```sh
python3 -m pytest tests/test_k6_afsk_audio_bench.py -q
# 11 passed
```

## Limits

- Fixed 1200 baud, 1200/2200 Hz, integer samples/symbol only.
- No clock recovery, no frequency-offset tracking, no multipath/fading
  model; WAV must start on a symbol boundary.
- `margin_db` is a detector-energy ratio, not a calibrated SNR meter.
- Numbers say nothing about packets, RF, or on-air sensitivity.

## Next step

First validate the PA11 receive path and its **actual** sampling rate on FD6818B.
Capture PCM at that measured rate (upstream 0x4819 reported roughly 17.8 kHz),
then resample with timing accounted for to a symbol-aligned 48 kHz mono WAV.
Write the matching `.meta.json` and run `analyze` beside a similarly
resampled GP0-derived capture. The current 32-sample PA11 level probe does
**not** export PCM or support BER measurements yet.
