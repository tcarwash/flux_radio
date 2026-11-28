"""Offline pure-NumPy CW/keying envelope reference (simulation only).

Reference model for a clean CW envelope: deterministic Morse dot/dash keying
shaped with a raised-cosine rise/fall, multiplied onto a continuous carrier
phasor to give baseband IQ. Pure NumPy, no firmware, no radio hardware, no
serial/RF TX, no external commands.

Background: ``docs/k6-rf-expansion-plan.md`` ("Clean CW work") targets a
4-8 ms raised-cosine rise/fall as an initial CW envelope, with the existing
``cwkey`` module using binary PA gating. This file models only the envelope
shape; it says nothing about APC linearity, PA overshoot, PLL chirp, or
off-state isolation. Any spectral comparison below is simulated guidance for
reviewing the shape, not proof of hardware (APC) behavior. No "no spurs" or
"no clicks on air" claim is made here.

Public API:
    dit_seconds, text_to_schedule, schedule_from_durations,
    raised_cosine_envelope, rectangular_envelope, cw_iq,
    max_edge_step, leakage_ratio, compare_rectangular
"""

from __future__ import annotations

import numpy as np

# Nominal CW rise/fall target from the expansion plan (4-8 ms), in seconds.
# Configurable per call via ``ramp_s``; this constant is just the default.
NOMINAL_RAMP_S = 0.005

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--",
    "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.",
    "/": "-..-.", "?": "..--..",
}

__all__ = [
    "MORSE",
    "NOMINAL_RAMP_S",
    "dit_seconds",
    "text_to_schedule",
    "schedule_from_durations",
    "schedule_seconds",
    "raised_cosine_envelope",
    "rectangular_envelope",
    "cw_iq",
    "max_edge_step",
    "leakage_ratio",
    "compare_rectangular",
]


def dit_seconds(wpm: float) -> float:
    """Seconds per dot (one Morse unit) at ``wpm``. Raises ValueError if not finite/positive."""
    wpm = float(wpm)
    if not np.isfinite(wpm) or wpm <= 0:
        raise ValueError(f"wpm must be finite and positive, got {wpm!r}")
    return 1.2 / wpm


def text_to_schedule(text: str, wpm: float) -> list:
    """Morse keying schedule for ``text`` at ``wpm``.

    Returns a list of ``(key_on, seconds)`` segments: dot = 1 unit on,
    dash = 3 units on, 1-unit gap after every element, 3-unit gap between
    letters and 7-unit gap between words (built from the trailing element
    gap plus 2 / 6 extra units). Unknown characters raise ValueError;
    ``""`` returns ``[]``.
    """
    dit = dit_seconds(wpm)
    sched: list = []
    for ch in text.upper():
        if ch == " ":
            sched.append((False, 6 * dit))
            continue
        code = MORSE.get(ch)
        if code is None:
            raise ValueError(f"unsupported Morse character {ch!r}")
        for sym in code:
            sched.append((True, dit if sym == "." else 3 * dit))
            sched.append((False, dit))
        sched.append((False, 2 * dit))  # pad element gap up to a letter gap
    return sched


def schedule_from_durations(on_off: list) -> list:
    """Explicit schedule from ``[(on_s, gap_s), ...]`` pairs.

    Each pair is one key-down pulse of ``on_s`` seconds followed by a
    key-up gap of ``gap_s`` seconds. All durations must be finite and
    non-negative; ``[]`` returns ``[]``.
    """
    sched: list = []
    for pair in on_off:
        on_s, gap_s = float(pair[0]), float(pair[1])
        if not np.isfinite(on_s) or not np.isfinite(gap_s):
            raise ValueError(f"durations must be finite, got {(on_s, gap_s)!r}")
        if on_s < 0 or gap_s < 0:
            raise ValueError(f"durations must be non-negative, got {(on_s, gap_s)!r}")
        if on_s > 0:
            sched.append((True, on_s))
        if gap_s > 0:
            sched.append((False, gap_s))
    return sched


def schedule_seconds(sched: list) -> float:
    """Total duration of a schedule in seconds."""
    return float(sum(sec for _, sec in sched))


def _check_clock(fs: float, ramp_s: float) -> tuple:
    fs = float(fs)
    ramp_s = float(ramp_s)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"fs must be finite and positive, got {fs!r}")
    if not np.isfinite(ramp_s) or ramp_s < 0:
        raise ValueError(f"ramp_s must be finite and non-negative, got {ramp_s!r}")
    return fs, ramp_s


def raised_cosine_envelope(sched: list, fs: float, ramp_s: float = NOMINAL_RAMP_S) -> np.ndarray:
    """Raised-cosine shaped keying envelope in [0, 1].

    Every key-down segment ramps 0 -> 1 over ``ramp_s`` seconds with
    ``0.5 * (1 - cos(pi * t / ramp))`` and ramps back 1 -> 0 over the same
    duration at its end; key-up segments are exactly zero. Ramp and segment
    lengths are quantized to the sample clock via ``round(s * fs)``. A ramp
    longer than a pulse is clamped to half that pulse so the peak still
    reaches 1.0 without overshoot; ``ramp_s=0`` gives the rectangular shape.
    Output starts and ends at zero (zero after key-up). Empty schedule
    returns an empty array.
    """
    fs, ramp_s = _check_clock(fs, ramp_s)
    counts = [(on, int(round(sec * fs))) for on, sec in sched]
    total = sum(n for _, n in counts)
    env = np.zeros(total, dtype=np.float64)
    i = 0
    for on, n in counts:
        if on and n > 0:
            r = int(round(ramp_s * fs))
            r = min(r, n // 2)  # clamp: ramp never exceeds half the pulse
            if r > 0:
                # Include true zero at the first/last keyed sample to avoid
                # discontinuity relative to adjoining key-up samples.
                t = np.linspace(0.0, 1.0, r) if r > 1 else np.zeros(1)
                env[i:i + r] = 0.5 * (1.0 - np.cos(np.pi * t))
                env[i + n - r:i + n] = 0.5 * (1.0 + np.cos(np.pi * t))
                if n > 2 * r:
                    env[i + r:i + n - r] = 1.0
            else:
                env[i:i + n] = 1.0
        i += n
    np.clip(env, 0.0, 1.0, out=env)  # defensive: no over/undershoot by contract
    return env


def rectangular_envelope(sched: list, fs: float) -> np.ndarray:
    """Unshaped reference envelope: 1.0 while keyed, 0.0 otherwise."""
    return raised_cosine_envelope(sched, fs, ramp_s=0.0)


def cw_iq(env: np.ndarray, fs: float, freq_hz: float = 0.0,
          phase0: float = 0.0) -> np.ndarray:
    """Continuous carrier phasor multiplied by the envelope (baseband IQ).

    ``iq[n] = env[n] * exp(j * (2*pi*freq_hz*n/fs + phase0))``. Phase is
    continuous by construction (no reset at element edges). ``|freq_hz|``
    above ``fs/2`` raises ValueError. Empty envelope returns empty IQ.
    """
    fs, _ = _check_clock(fs, 0.0)
    freq_hz = float(freq_hz)
    if not np.isfinite(freq_hz) or abs(freq_hz) > fs / 2:
        raise ValueError(f"freq_hz must be within +/-fs/2, got {freq_hz!r} at fs={fs!r}")
    env = np.asarray(env, dtype=np.float64)
    n = np.arange(env.size, dtype=np.float64)
    return env * np.exp(1j * (2.0 * np.pi * freq_hz * n / fs + float(phase0)))


def max_edge_step(env: np.ndarray) -> float:
    """Largest |sample[n+1] - sample[n]|; a numerical edge-discontinuity proxy.

    A rectangular envelope steps by 1.0 at each edge; shaping must reduce it.
    This measures model sample steps only, not audible clicks on air.
    """
    env = np.asarray(env, dtype=np.float64)
    if env.size < 2:
        return 0.0
    return float(np.max(np.abs(np.diff(env))))


def leakage_ratio(x: np.ndarray, fs: float, cutoff_hz: float) -> float:
    """Share of total energy outside +/-``cutoff_hz`` (unwindowed FFT).

    Deliberately no window: keying-edge broadening must stay visible. The
    result is a reproducible simulation metric for comparing shapes, not a
    transmitter emission measurement. Empty input returns 0.0.
    """
    fs = float(fs)
    cutoff_hz = float(cutoff_hz)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"fs must be finite and positive, got {fs!r}")
    if not np.isfinite(cutoff_hz) or cutoff_hz < 0:
        raise ValueError(f"cutoff_hz must be finite and non-negative, got {cutoff_hz!r}")
    x = np.asarray(x, dtype=np.complex128)
    if x.size == 0:
        return 0.0
    spec = np.fft.fft(x)
    freqs = np.fft.fftfreq(x.size, 1.0 / fs)
    power = np.abs(spec) ** 2
    total = float(power.sum())
    if total == 0.0:
        return 0.0
    return float(power[np.abs(freqs) > cutoff_hz].sum() / total)


def _default_cutoff(sched: list) -> float:
    """Cutoff tied to keying rate: 3x the dot rate of the shortest pulse."""
    ons = [sec for on, sec in sched if on and sec > 0]
    if not ons:
        return 50.0
    return max(50.0, 3.0 / min(ons))


def compare_rectangular(sched: list, fs: float, ramp_s: float = NOMINAL_RAMP_S,
                        cutoff_hz: float | None = None, freq_hz: float = 0.0) -> dict:
    """Side-by-side shaped vs rectangular comparison (simulation guidance only).

    Returns envelope/IQ pairs, ``max_edge_step`` for each shape, and the
    out-of-band ``leakage_ratio`` for each IQ signal at ``cutoff_hz``
    (default: 3x the dot rate of the shortest pulse, at least 50 Hz).
    A smaller shaped leakage shows the ramp narrows the simulated spectrum;
    it proves nothing about APC behavior or hardware spurs.
    """
    if cutoff_hz is None:
        cutoff_hz = _default_cutoff(sched)
    shaped = raised_cosine_envelope(sched, fs, ramp_s)
    rect = rectangular_envelope(sched, fs)
    iq_shaped = cw_iq(shaped, fs, freq_hz)
    iq_rect = cw_iq(rect, fs, freq_hz)
    step_shaped = max_edge_step(shaped)
    step_rect = max_edge_step(rect)
    leak_shaped = leakage_ratio(iq_shaped, fs, cutoff_hz)
    leak_rect = leakage_ratio(iq_rect, fs, cutoff_hz)
    return {
        "fs": float(fs),
        "ramp_s": float(ramp_s),
        "cutoff_hz": float(cutoff_hz),
        "envelope_shaped": shaped,
        "envelope_rect": rect,
        "iq_shaped": iq_shaped,
        "iq_rect": iq_rect,
        "step_shaped": step_shaped,
        "step_rect": step_rect,
        "leakage_shaped": leak_shaped,
        "leakage_rect": leak_rect,
        "leakage_ratio": (leak_shaped / leak_rect) if leak_rect > 0 else 0.0,
    }
