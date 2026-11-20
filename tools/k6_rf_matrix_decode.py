#!/usr/bin/env python3
"""Known-payload-assisted offline FD6818B FSK lab analysis, not blind BER."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
from scipy.ndimage import uniform_filter1d


def payload(nbytes):
    x = 0xace1
    words = []
    for _ in range(nbytes // 2):
        words.append(x)
        x = (x >> 1) ^ (0xb400 if x & 1 else 0)
    return np.unpackbits(np.frombuffer(b''.join(w.to_bytes(2, 'little') for w in words), dtype=np.uint8))


def decode(path, offset=280375):
    meta = json.loads(path.read_text())
    raw = np.load(path.with_suffix('.npy'))
    z = (raw[::2].astype('f') - 127.5) + 1j * (raw[1::2].astype('f') - 127.5)
    # Only the first 2.5 s are needed for this bounded lab protocol.
    z = z[:2560000]
    y = resample_poly(z * np.exp(2j * np.pi * offset * np.arange(len(z)) / 1024000), 1, 8)
    fs = 128000
    d = np.angle(y[1:] * y[:-1].conj()) * fs / (2 * np.pi)
    bits = payload(meta['nbytes'])
    target = bits.astype(float) * 2 - 1
    target -= target.mean()
    baud = meta['baud']
    best = None
    for width in [max(2, int(fs / baud * f)) for f in (.3, .6, .9)]:
        a = uniform_filter1d(d, width)
        for rate in baud * np.linspace(.99, 1.01, 9):
            ix = np.arange(len(bits)) * fs / rate
            start = np.arange(int(1.04 * fs), int(1.24 * fs), max(1, int(fs / baud / 16)))
            inds = (start[:, None] + ix[None, :]).astype(int)
            s = a[inds]
            s -= s.mean(axis=1, keepdims=True)
            corr = (s @ target) / (np.linalg.norm(s, axis=1) * np.linalg.norm(target) + 1e-9)
            k = np.argmax(abs(corr))
            c = corr[k]
            recovered = (s[k] * np.sign(c) > 0).astype(np.uint8)
            err = np.count_nonzero(recovered != bits)
            if best is None or abs(c) > best['correlation']:
                best = dict(correlation=float(abs(c)), errors=int(err), bits=len(bits), start_s=float(start[k]/fs), rate=float(rate), filter_samples=width, polarity=int(np.sign(c)), recovered_hex=np.packbits(recovered).tobytes().hex())
    return dict(**meta, **best, capture=path.stem, carrier_offset_hz=offset)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--offset', type=float, default=280375)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    rows = []
    for path in sorted(args.directory.glob('apc*.json')):
        row = decode(path, args.offset)
        rows.append(row)
        print(f"{path.stem}: {row['errors']}/{row['bits']} errors; correlation {row['correlation']:.3f}; clock {row['rate']:.1f}", flush=True)
    if args.output:
        args.output.write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
