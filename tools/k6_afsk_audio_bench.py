"""Deterministic host-only Bell 202 / AFSK audio quality benchmark.

This is NOT a production packet modem and makes NO RF performance claims.
It generates repeatable 48 kHz continuous-phase 1200/2200 Hz, 1200-baud PCM
test sequences from a fixed seed and a known bit array, then scores them
with a *known-symbol-boundary reference* two-tone detector (per-symbol
quadrature correlations, i.e. a Goertzel-style energy comparison).

Bit-to-tone mapping used here (test convention only)::

    bit 1 -> MARK  (1200 Hz)
    bit 0 -> SPACE (2200 Hz)

This is a raw-symbol test convention, NOT AX.25 NRZI/HDLC: there is no
bit-stuffing, no CRC, no addressing, and no packet decode of any kind.
There is intentionally no ``decode_packet`` / HDLC / AX.25 API in this
module. BER is measured against the known transmitted bits with known
symbol timing, never by blind decoding.

Deliberately standalone: imports only numpy + stdlib, and does NOT import
or touch ``sim/k6emu/aprs_signal.py``, ``sim/k6emu/aprs_slicer.py``,
``sim/k6if/aprs_mf.py``, GP0/PCM acquisition paths, radio controls, live
serial, firmware, or any shared digital-modem API. That keeps AFSK quality
scoring independent from GP0/PCM captures so future PA11 PCM samples can be
compared against GP0-derived audio on equal footing.

Generated WAV/metadata always defaults outside the git checkout
(``/tmp/k6-afsk-bench``); writing inside a git repo requires the explicit
``--allow-repo-out`` flag.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
BAUD = 1200
F_MARK = 1200.0
F_SPACE = 2200.0
DEFAULT_SEED = 1
DEFAULT_N_SYMBOLS = 1200
DEFAULT_AMPLITUDE = 0.7
DEFAULT_OUT_DIR = "/tmp/k6-afsk-bench"

# Band limits (Hz) for the injected band-limited AWGN noise path.
NOISE_LOW_HZ = 800.0
NOISE_HIGH_HZ = 2800.0


def samples_per_symbol(sample_rate: int = SAMPLE_RATE, baud: int = BAUD) -> int:
    """Integer samples per symbol; only exact divisions are supported."""
    if sample_rate <= 0 or baud <= 0 or sample_rate % baud != 0:
        raise ValueError("sample rate must be a positive integer multiple of baud")
    return sample_rate // baud


def generate_bits(n_symbols: int, seed: int = DEFAULT_SEED) -> list[int]:
    """Deterministic pseudo-random test bits (0/1) from a fixed seed."""
    if n_symbols <= 0:
        raise ValueError("n_symbols must be positive")
    rng = np.random.default_rng(seed)
    return [int(v) for v in rng.integers(0, 2, size=n_symbols)]


def _bandpass_filter(white: np.ndarray, sample_rate: int,
                     low_hz: float, high_hz: float) -> np.ndarray:
    """Brick-wall band-pass via FFT (deterministic, zero-phase)."""
    n = white.size
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    spec[~mask] = 0.0
    return np.fft.irfft(spec, n=n)


def generate_afsk(bits: list[int] | np.ndarray,
                  sample_rate: int = SAMPLE_RATE,
                  baud: int = BAUD,
                  amplitude: float = DEFAULT_AMPLITUDE,
                  clip: float = 1.0,
                  snr_db: float | None = None,
                  noise_seed: int = 0,
                  noise_low_hz: float = NOISE_LOW_HZ,
                  noise_high_hz: float = NOISE_HIGH_HZ) -> np.ndarray:
    """Continuous-phase Bell 202 PCM for ``bits`` (float64, nominally [-1, 1]).

    Phase is accumulated sample-by-sample (``phase += 2*pi*f/fs``), so the
    waveform is continuous across symbol boundaries by construction. ``1``
    selects 1200 Hz, ``0`` selects 2200 Hz (raw-symbol test convention).

    When ``snr_db`` is given, deterministic band-limited AWGN
    (``noise_low_hz``..``noise_high_hz``, seeded by ``noise_seed``) is added
    so that ``10*log10(signal_power / in_band_noise_power) == snr_db``,
    where ``signal_power`` is the nominal sine power ``amplitude**2 / 2``.
    The sum is then hard-clipped to ``[-clip, clip]``.
    """
    bits = [int(b) for b in bits]
    if any(b not in (0, 1) for b in bits):
        raise ValueError("bits must be 0/1")
    if not (0.0 < amplitude <= 1.0):
        raise ValueError("amplitude must be in (0, 1]")
    if clip <= 0:
        raise ValueError("clip must be positive")
    spb = samples_per_symbol(sample_rate, baud)
    freqs = np.array([F_MARK if b else F_SPACE for b in bits], dtype=np.float64)
    phase = np.cumsum(np.repeat(freqs, spb) * (2.0 * math.pi / sample_rate))
    out = amplitude * np.sin(phase)
    if snr_db is not None:
        rng = np.random.default_rng(noise_seed)
        white = rng.standard_normal(out.size)
        shaped = _bandpass_filter(white, sample_rate, noise_low_hz, noise_high_hz)
        noise_power = float(np.mean(shaped ** 2))
        if noise_power <= 0:
            raise ValueError("band-limited noise has zero power")
        signal_power = amplitude ** 2 / 2.0
        target = signal_power / (10.0 ** (snr_db / 10.0))
        out = out + shaped * math.sqrt(target / noise_power)
    return np.clip(out, -clip, clip)


def analyze(samples: np.ndarray,
            bits: list[int] | np.ndarray,
            sample_rate: int = SAMPLE_RATE,
            baud: int = BAUD) -> dict:
    """Score PCM against known bits with a known-boundary two-tone detector.

    Each symbol window (exactly ``sample_rate / baud`` samples, starting at
    sample 0) is correlated against unit-amplitude cos/sin references at
    1200 Hz and 2200 Hz. The tone with the larger quadrature energy wins;
    energy for MARK decides bit 1, energy for SPACE decides bit 0.

    Returns a dict with ``decisions``, per-symbol ``scores`` in [-1, 1]
    (``(E_mark - E_space) / (E_mark + E_space)``), ``errors``, ``ber``
    (pre-FEC: raw symbol decisions vs known bits, no coding gain assumed),
    ``margin_db`` (detector energy-ratio margin, a test-bench proxy -- NOT
    a calibrated channel SNR), ``sample_rate``, ``peak_abs`` and
    ``clipped_fraction`` (fraction of samples at/above 0.99 magnitude).
    """
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    bits = [int(b) for b in bits]
    if any(b not in (0, 1) for b in bits):
        raise ValueError("bits must be 0/1")
    spb = samples_per_symbol(sample_rate, baud)
    n = len(bits)
    if x.size < n * spb:
        raise ValueError(f"need at least {n * spb} samples, got {x.size}")
    frames = x[: n * spb].reshape(n, spb)
    t = np.arange(spb, dtype=np.float64) / sample_rate
    refs = {}
    for f in (F_MARK, F_SPACE):
        refs[f] = (np.cos(2.0 * math.pi * f * t), np.sin(2.0 * math.pi * f * t))
    cm, sm = refs[F_MARK]
    cs, ss = refs[F_SPACE]
    e_mark = frames.dot(cm) ** 2 + frames.dot(sm) ** 2
    e_space = frames.dot(cs) ** 2 + frames.dot(ss) ** 2
    denom = e_mark + e_space
    scores = np.where(denom > 0, (e_mark - e_space) / np.maximum(denom, 1e-30), 0.0)
    decisions = [1 if m >= s else 0 for m, s in zip(e_mark, e_space)]
    errors = sum(d != b for d, b in zip(decisions, bits))
    tot_win = float(np.sum(np.maximum(e_mark, e_space)))
    tot_lose = float(np.sum(np.minimum(e_mark, e_space)))
    margin_db = float(10.0 * math.log10(tot_win / tot_lose)) if tot_lose > 0 else float("inf")
    return {
        "n_symbols": n,
        "errors": errors,
        "ber": errors / n,
        "decisions": decisions,
        "scores": [float(s) for s in scores],
        "margin_db": margin_db,
        "sample_rate": sample_rate,
        "baud": baud,
        "peak_abs": float(np.max(np.abs(x))),
        "clipped_fraction": float(np.mean(np.abs(x) >= 0.99)),
    }


def write_wav(path: str | Path, samples: np.ndarray,
              sample_rate: int = SAMPLE_RATE) -> Path:
    """Write mono 16-bit PCM WAV (float [-1, 1] -> int16)."""
    path = Path(path)
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    if sample_rate <= 0:
        raise ValueError("invalid sample rate")
    pcm = np.clip(x, -1.0, 1.0)
    data = (pcm * 32767.0).round().astype(np.int16).tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return path


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read mono 16-bit PCM WAV -> (float samples in [-1, 1], sample rate)."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("only mono 16-bit PCM WAV is supported")
        frames = w.getnframes()
        raw = w.readframes(frames)
        sample_rate = w.getframerate()
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0, sample_rate


def save_meta(path: str | Path, meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def load_meta(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _repo_root_below(path: Path) -> bool:
    """True if ``path`` sits inside a git checkout (walk up looking for .git)."""
    p = path.resolve()
    for parent in (p, *p.parents):
        if (parent / ".git").exists():
            return True
    return False


def cmd_gen(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    if _repo_root_below(out_dir) and not args.allow_repo_out:
        print(f"refusing to write inside a git checkout: {out_dir}\n"
              "generated data stays outside git; use --allow-repo-out to override",
              file=sys.stderr)
        return 2
    bits = generate_bits(args.n_symbols, args.seed)
    samples = generate_afsk(bits, sample_rate=args.sample_rate,
                            amplitude=args.amplitude, snr_db=args.snr_db,
                            noise_seed=args.seed + 1000)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{args.prefix}.wav"
    meta_path = out_dir / f"{args.prefix}.meta.json"
    write_wav(wav_path, samples, args.sample_rate)
    save_meta(meta_path, {
        "bits": bits,
        "seed": args.seed,
        "n_symbols": args.n_symbols,
        "sample_rate": args.sample_rate,
        "baud": BAUD,
        "f_mark_hz": F_MARK,
        "f_space_hz": F_SPACE,
        "amplitude": args.amplitude,
        "snr_db": args.snr_db,
        "note": "raw-symbol test convention (1=1200Hz, 0=2200Hz); "
                "not AX.25/NRZI/HDLC; no packet decode",
    })
    print(json.dumps({"wav": str(wav_path), "meta": str(meta_path),
                      "n_symbols": args.n_symbols}))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    samples, wav_rate = read_wav(args.wav)
    meta = load_meta(args.meta)
    if wav_rate != int(meta.get("sample_rate", wav_rate)):
        print(f"error: WAV rate {wav_rate} != meta rate {meta.get('sample_rate')}",
              file=sys.stderr)
        return 2
    result = analyze(samples, meta["bits"],
                     sample_rate=wav_rate,
                     baud=int(meta.get("baud", BAUD)))
    summary = {k: v for k, v in result.items() if k not in ("decisions", "scores")}
    summary["mean_score"] = float(np.mean(result["scores"]))
    summary["wav"] = str(args.wav)
    print(json.dumps(summary, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Deterministic Bell 202/AFSK audio quality bench "
                    "(reference detector; NOT a packet modem)")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate test WAV + metadata/expected bits")
    g.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    g.add_argument("--n-symbols", type=int, default=DEFAULT_N_SYMBOLS)
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--sample-rate", type=int, default=SAMPLE_RATE)
    g.add_argument("--amplitude", type=float, default=DEFAULT_AMPLITUDE)
    g.add_argument("--snr-db", type=float, default=None,
                   help="inject band-limited AWGN at this in-band SNR (dB)")
    g.add_argument("--prefix", default="afsk-bench")
    g.add_argument("--allow-repo-out", action="store_true",
                   help="permit writing generated data inside a git checkout")
    g.set_defaults(func=cmd_gen)
    a = sub.add_parser("analyze", help="score a WAV against its metadata bits")
    a.add_argument("--wav", required=True)
    a.add_argument("--meta", required=True)
    a.set_defaults(func=cmd_analyze)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
