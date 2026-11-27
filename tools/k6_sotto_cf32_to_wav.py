#!/usr/bin/env python3
"""Offline RTL-SDR complex64 recording -> independent FDV1/1200 audio WAV.

Does not access USB, radio, network, PTT, or SPI. Uses the already field-tested
Sotto Monitor blind packet decoder and pinned native Sotto 1200 decoder.
Raw capture must be contiguous 1024000 Hz complex64 with center 125kHz below
443.5MHz; its JSON sidecar must say complete=true. Keep IQ even on no decode.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import wave

import numpy as np
from scipy.signal import resample_poly

MONITOR=Path(os.environ.get('SOTTO_MONITOR_ROOT','/storage5/dev/sotto-monitor'))
RATE=1_024_000
FS=128_000
OFFSET=125_000
FREQUENCY=443_500_000


def run(iq_path:Path,out_base:Path,*,tune_offset_hz:int=0):
    if not -55000<=tune_offset_hz<=55000:
        raise ValueError("Decoder tune offset must be within ±55 kHz")
    iq_path=iq_path.resolve();out_base=out_base.resolve()
    meta_path=iq_path.with_suffix('.json')
    meta=json.loads(meta_path.read_text())
    if (not meta.get('complete') or meta.get('sample_rate')!=RATE or
        meta.get('actual_center_hz')!=FREQUENCY-OFFSET or
        meta.get('format')!='little-endian complex64'):
        raise ValueError('Unqualified recording settings; refusing to infer RF frequency or sample continuity')
    if (meta.get('samples',0)<RATE//2 or meta.get('file_bytes')!=iq_path.stat().st_size or
        iq_path.stat().st_size!=meta['samples']*8):
        raise ValueError('Truncated or incomplete RTL-SDR recording')
    if hashlib.sha256(iq_path.read_bytes()).hexdigest()!=meta['sha256']:
        raise ValueError('RTL-SDR recording SHA256 differs from capture metadata')
    if MONITOR.as_posix() not in sys.path:sys.path.insert(0,str(MONITOR))
    import app as sotto
    assert sotto.RATE==RATE and sotto.FS==FS and sotto.OFFSET==OFFSET
    s=sotto.Monitor();s.freq=FREQUENCY/1e6;s.mode='offline';s.tune_hz=tune_offset_hz
    tail=np.empty(0,np.complex64);n=0
    samples=np.memmap(iq_path,dtype='<c8',mode='r')
    for start in range(0,len(samples),131072):
        a=np.asarray(samples[start:start+131072]); t=np.arange(len(a),dtype=np.float64)+start
        z=resample_poly(a*np.exp(-2j*np.pi*OFFSET*t/RATE),1,8).astype(np.complex64)
        block=np.concatenate((tail,z)); origin=n-len(tail)
        n+=len(z);s.samples=n;s.process(block,origin)
        tail=block[-int(FS*.23):]
    # Remove the base64 PCM payloads, but keep CRC/clock/offset diagnostics.
    events=list(s.events)
    pcm=bytes(s.pcm)
    audio_rms=0.
    if pcm:
        audio_rms=float(np.sqrt(np.mean(np.frombuffer(pcm,dtype='<i2').astype(np.float64)**2)))
    result=dict(source=str(iq_path),source_sha256=meta['sha256'],source_meta=meta,
                decoder_tune_offset_hz=tune_offset_hz,
                decoded_good_packets=s.good,decoded_bad_packets=s.bad,
                codec_frames=s.frames,audio_pcm_samples=len(pcm)//2,
                audio_rms=round(audio_rms,3),events=events,
                independent_rf_crc_validated=s.good>0,
                full_non_silent_microphone_decode=bool(s.good>0 and s.frames>0 and audio_rms>100))
    path=out_base.with_suffix('.json');wav=out_base.with_suffix('.wav')
    if any(p.exists() for p in (path,wav)):
        raise ValueError('Refusing to overwrite previous decoded evidence')
    path.write_text(json.dumps(result,indent=2)+'\n')
    with wave.open(str(wav),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(pcm)
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_meta','events')},indent=2))
    print('WAV',wav,'JSON',path)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--tune-offset-hz',type=int,default=0,help='Sotto Monitor click-to-tune offset; e.g. -30500 Hz')
    args=p.parse_args()
    run(args.iq,args.out,tune_offset_hz=args.tune_offset_hz)
