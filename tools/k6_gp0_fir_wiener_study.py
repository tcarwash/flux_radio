#!/usr/bin/env python3
"""Screen compact fixed-point FIR approximations of the GP0 Wiener mask.

The response is trained from simulated GP0 source/noise spectra, then evaluated
before and after the real Sotto v3.8 codec on held-out speech. This is an
offline design gate, not an MCU or physical-radio qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import firwin2, lfilter, welch

ROOT = Path(__file__).resolve().parents[1]
RFSIM = Path('/storage5/dev/rfsim-k6-gp0-20261127')
sys.path[:0] = [str(ROOT), str(RFSIM)]
import types
pkg = sys.modules.get('tools')
if pkg is None or str(ROOT / 'tools') not in getattr(pkg, '__path__', []):
    pkg = types.ModuleType('tools')
    pkg.__path__ = [str(ROOT / 'tools')]
    sys.modules['tools'] = pkg

from tools.k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from tools.k6_gp0_noise_sotto_v38_study import (
    FS, INPUT_VPP, SAMPLE_ROOT, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

VPP = 0.1
TARGET_SNR_DB = -1.0
NOISE_SCALE = 2.5
MASK_FLOOR = 0.03
NFFT = 512
TAPS = (15, 31, 63)
SEEDS = (17, 83, 211)
TRAIN = tuple(VOICE_IDS[:4])
TEST = tuple(VOICE_IDS[4:])


def source_pcm(voice: str) -> tuple[np.ndarray, np.ndarray]:
    path = SAMPLE_ROOT / f'librispeech_{voice}__original16.flac'
    ref = resample_poly(read_flac(path, 16000), 1, 2)
    norm = ref / max(float(np.max(np.abs(ref))), 1e-12)
    clean = k6_gp0_pcm(norm, input_vpp=VPP, noise_rms_fs=0,
                        response='measured', clip=True)
    return ref, clean


def average_psd(arrays: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    rows = [welch(x, fs=FS, nperseg=NFFT, noverlap=NFFT//2,
                  detrend=False, scaling='spectrum')[1] for x in arrays]
    freq = np.fft.rfftfreq(NFFT, 1 / FS)
    return freq, np.mean(rows, axis=0)


def design_fir() -> dict:
    train_pcm = [source_pcm(v)[1] for v in TRAIN]
    freq, ps = average_psd(train_pcm)
    noise = generate_gp0_noise(FS * 120, seed=90817, model='banded', rms_fs=1.0)
    _, pn = welch(noise, fs=FS, nperseg=NFFT, noverlap=NFFT//2,
                  detrend=False, scaling='spectrum')
    mean_speech_power = float(np.mean([np.mean(x*x) for x in train_pcm]))
    noise_rms = math.sqrt(mean_speech_power) / (10 ** (TARGET_SNR_DB / 20))
    pn *= noise_rms * noise_rms / float(np.mean(noise * noise))
    pmix = ps + pn
    gain = np.maximum((pmix - NOISE_SCALE * pn) / np.maximum(pmix, 1e-20), MASK_FLOOR)
    gain = np.clip(gain, MASK_FLOOR, 1.0)
    result = {'freq_hz': freq, 'gain': gain, 'ps': ps, 'pn': pn,
              'train_speech_rms': math.sqrt(mean_speech_power),
              'design_noise_rms': noise_rms}
    for n in TAPS:
        h = firwin2(n, freq / (FS / 2), gain, window=('kaiser', 5.0), fs=2)
        q = np.clip(np.rint(h * 32768), -32768, 32767).astype(np.int16)
        result[f'h{n}'] = q.astype(np.float64) / 32768.0
    return result


def align_causal(y: np.ndarray, delay: int) -> np.ndarray:
    if delay == 0:
        return y
    if len(y) <= delay:
        return np.zeros_like(y)
    return np.r_[y[delay:], np.zeros(delay, dtype=y.dtype)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out-dir', type=Path, required=True)
    a = p.parse_args()
    out = a.out_dir.resolve()
    if out.exists():
        p.error(f'refusing to overwrite existing output directory: {out}')
    out.mkdir(parents=True)
    d = design_fir()
    report = {
        'schema': 1, 'offline_only': True,
        'experiment': 'fixed linear-phase FIR approximation to static Wiener mask, before real Sotto v3.8 codec',
        'source_model': 'RFSim measured-response GP0 plus banded statistical noise',
        'input_vpp': VPP, 'target_global_snr_db': TARGET_SNR_DB,
        'wiener_noise_scale': NOISE_SCALE, 'mask_floor': MASK_FLOOR,
        'sample_rate_hz': FS, 'nfft_design': NFFT, 'tap_counts': list(TAPS),
        'group_delay_ms': {str(n): round((n-1)/2*1000/FS, 3) for n in TAPS},
        'train_voices': list(TRAIN), 'heldout_voices': list(TEST), 'seeds': list(SEEDS),
        'design': {'train_clean_rms': d['train_speech_rms'], 'matched_noise_rms': d['design_noise_rms']},
        'limitations': [
            'This FIR is trained on the current statistical noise model, not a long physical GP0 silence capture.',
            'Speech Vpp is a laboratory source convention, not calibrated acoustic SPL.',
            'Metrics are proxies; no physical K6 timing, listening, or RF test is performed.',
        ],
        'conditions': {},
    }
    with tempfile.TemporaryDirectory(prefix='gp0-fir-wiener-v38-') as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            ref, clean = source_pcm(voice)
            clean_dec = encode_decode(encoder, decoder, clean, temp, f'{voice}-clean')
            target_rms = float(np.std(clean_dec))
            rows = []
            for seed in SEEDS:
                noise = generate_gp0_noise(len(clean), seed=seed, model='banded', rms_fs=OFF_PCM_RMS_FS)
                noise *= float(np.std(clean)) / (10 ** (TARGET_SNR_DB / 20) * float(np.std(noise)))
                mixture = clean + noise
                np.clip(mixture, -1.0, 1.0, out=mixture)
                preamble = generate_gp0_noise(FS * 2, seed=seed + 1_000_003,
                                               model='banded', rms_fs=float(np.std(noise)))
                samples = {'noisy': mixture}
                samples['wiener_stft'] = causal_denoise(
                    mixture, preamble, 'wiener', noise_scale=NOISE_SCALE,
                    mask_floor=MASK_FLOOR)
                for n in TAPS:
                    h = d[f'h{n}']
                    filtered = lfilter(h, [1.0], mixture)
                    filtered = align_causal(filtered, (n-1)//2)
                    samples[f'fir_{n}tap'] = filtered
                trial = {'seed': seed, 'treatments': {}}
                for name, pcm in samples.items():
                    dec = encode_decode(encoder, decoder, pcm, temp, f'{voice}-{seed}-{name}')
                    trial['treatments'][name] = score(ref, dec, target_rms)
                rows.append(trial)
            report['conditions'][voice] = {'heldout': voice in TEST, 'trials': rows}
            print(f'{voice}: {len(rows)} paired seeds complete', flush=True)

    report['summary'] = {}
    for name in ('noisy', 'wiener_stft', *(f'fir_{n}tap' for n in TAPS)):
        all_rows = [(v, t['treatments'][name]) for v, c in report['conditions'].items() for t in c['trials']]
        held = [(v, r) for v, r in all_rows if v in TEST]
        report['summary'][name] = {
            'all_mean_estoi': round(float(np.mean([r['estoi'] for _, r in all_rows])), 5),
            'all_mean_pesq_nb': round(float(np.mean([r['pesq_nb'] for _, r in all_rows])), 4),
            'heldout_mean_estoi': round(float(np.mean([r['estoi'] for _, r in held])), 5),
            'heldout_mean_pesq_nb': round(float(np.mean([r['pesq_nb'] for _, r in held])), 4),
            'heldout_voices': list(TEST),
            'heldout_case_count': len(held),
        }

    # Persist Q15 coefficients for target-port review, including exact hash.
    header = ['/* Generated by k6_gp0_fir_wiener_study.py; offline candidate only. */',
              '#pragma once', '#include <stdint.h>']
    coeff_meta = {}
    for n in TAPS:
        q = np.clip(np.rint(d[f'h{n}'] * 32768), -32768, 32767).astype(np.int16)
        name = f'gp0_wiener_q15_{n}'
        header.append(f'static const int16_t {name}[{n}] = {{')
        header.append('  ' + ', '.join(str(int(x)) for x in q))
        header.append('};')
        coeff_meta[str(n)] = {'sha256': hashlib.sha256(q.astype('<i2').tobytes()).hexdigest(),
                              'dc_gain_q15': int(np.sum(q)), 'group_delay_samples': (n-1)//2}
    (out/'gp0_wiener_fir_q15.h').write_text('\n'.join(header)+'\n')
    report['coefficients'] = coeff_meta
    (out/'report.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
