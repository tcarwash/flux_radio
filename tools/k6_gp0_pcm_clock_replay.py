#!/usr/bin/env python3
"""RF-verified, OFFLINE-only GP0 PCM clock correction and listening A/B.

Reads saved 8k-header GP0 PCM AIR WAV+JSON, or post-tuned 128kS/s C64 IQ.
The latter can yield a *labeled* consecutive-prefix preview on --allow-partial;
missing samples are never zero-filled or guessed. Source files are read-only.
The true clock is inferred from 1600 PCM samples / measured 100us ticks,
not a hardcoded value. A fractional polyphase resampler creates *new* 8k PCM
playback for local listening and comparing optional low-pass presentation.
It does not correct radio firmware, capture high-rate PDM, key RF, or send data.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

# Direct CLI invocation needs the repo root for `tools.*` imports.
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, resample_poly, sosfiltfilt

from tools.k6_gp0_pcm_air import PACKETS, N_SAMPLES, FS, parse_iq_complex64


@dataclass(frozen=True)
class PcmCapture:
    pcm: bytes
    ticks_100us: int
    token: int
    complete: bool
    accepted_packets: int
    source: str


def collect_packets(packets,*,allow_partial=False,source='RF IQ'):
    accepted=[p for p in packets if p is not None]
    if not accepted:raise ValueError('no CRC-verified GP0 PCM AIR packets')
    first=accepted[0]
    signature=(first['token'],first['samples'],first['fs'],first['capture_100us'])
    ticks=first['capture_100us']
    if not first['token'] or first['fs']!=FS or first['samples']!=N_SAMPLES or not 1500<=ticks<=2800:
        raise ValueError('invalid capture header, sample count or clock ticks')
    by_seq={}
    for p in accepted:
        if (p['token'],p['samples'],p['fs'],p['capture_100us'])!=signature:
            raise ValueError('mixed GP0 PCM sessions')
        s=p['seq']
        if not 0<=s<PACKETS:raise ValueError('out-of-range sequence')
        if s in by_seq and by_seq[s]!=p['payload']:
            raise ValueError('conflicting CRC-valid duplicate packet')
        by_seq[s]=p['payload']
    complete=len(by_seq)==PACKETS
    if not complete and not allow_partial:raise ValueError(f'incomplete RF capture: {len(by_seq)}/{PACKETS} packets; use --allow-partial for a labeled prefix')
    if sorted(by_seq)!=list(range(len(by_seq))):
        raise ValueError('partial data has gaps or missing initial sequence: cannot form contiguous audio')
    pcm=b''.join(by_seq[i] for i in range(len(by_seq)))
    expected_size=N_SAMPLES*2 if complete else len(by_seq)*24*2
    if len(pcm)!=expected_size:raise ValueError('incorrect or inconsistent PCM packet payload lengths')
    return PcmCapture(pcm,ticks,first['token'],complete,len(by_seq),source)


def read_verified_wav(wav_path,metadata_path):
    meta=json.loads(metadata_path.read_text())
    fs,pcm=wavfile.read(wav_path)
    if fs!=FS or pcm.dtype!=np.int16 or pcm.ndim!=1 or len(pcm)!=N_SAMPLES:
        raise ValueError('expected complete 1600 sample signed16 mono WAV with 8k header')
    b=pcm.astype('<i2',copy=False).tobytes()
    if not meta.get('complete_pcm') or meta.get('unique_packets')!=PACKETS or meta.get('crc_accepted')!=PACKETS:
        raise ValueError('WAV session lacks full 67-packet CRC verification')
    if meta.get('pcm_sha256')!=hashlib.sha256(b).hexdigest():
        raise ValueError('PCM hash disagrees with original RF-derived manifest')
    ticks=meta.get('capture_duration_ticks_100us')
    if not isinstance(ticks,int) or not 1500<=ticks<=2800 or meta.get('sample_rate_hz')!=FS or meta.get('sample_count')!=N_SAMPLES:
        raise ValueError('invalid capture rate or duration')
    return PcmCapture(b,ticks,int(meta['token']),True,PACKETS,str(wav_path))


def source_rate(capture):
    if len(capture.pcm)%2:raise ValueError('odd PCM byte count')
    if not 1500<=capture.ticks_100us<=2800:raise ValueError('invalid timebase')
    # Radio reports the *full* fixed 1600-sample capture time in all chunks,
    # even when the RF receiver has recovered only a contiguous prefix.
    return N_SAMPLES*10_000/capture.ticks_100us


def clock_correct(samples,ticks_100us):
    x=np.asarray(samples)
    if x.ndim!=1 or x.dtype!=np.int16 or not x.size or not 1500<=ticks_100us<=2800:
        raise ValueError('signed16 audio and credible full-capture tick count required')
    ratio=Fraction(ticks_100us,2000) # 8k / (1600*10000/ticks)
    result=resample_poly(x.astype(np.float64),ratio.numerator,ratio.denominator)
    return np.clip(np.rint(result),-32768,32767).astype(np.int16)


def describe(x,fs=FS,expected_tone_hz=None):
    v=np.asarray(x,dtype=np.float64)/32768.0
    if v.ndim!=1 or len(v)<96:raise ValueError('insufficient PCM for spectral report')
    centered=v-v.mean()
    result={'samples':int(len(v)),'duration_ms_at_8k':round(1000*len(v)/fs,3),
            'rms_ratio':round(float(np.std(v)),7),
            'max_abs_ratio':round(float(np.max(abs(v))),7),
            'clipped_samples':int(np.count_nonzero(abs(v)>=.999))}
    if expected_tone_hz is not None:
        f=float(expected_tone_hz)
        if not 150<=f<=3500:raise ValueError('expected tone must be in voice passband')
        t=np.arange(len(v))/fs
        grid=np.fft.rfftfreq(131072,1/fs)
        spec=np.abs(np.fft.rfft(centered*np.hanning(len(v)),n=131072))
        within=(grid>=.87*f)&(grid<=1.13*f)
        hz=float(grid[within][np.argmax(spec[within])])
        basis=np.column_stack((np.sin(2*np.pi*hz*t),np.cos(2*np.pi*hz*t),np.ones(len(v))))
        a,b,c=np.linalg.lstsq(basis,v,rcond=None)[0]
        rms=float(np.std(v-basis@[a,b,c]))
        result.update(peak_hz=round(hz,3),tone_amp_peak_ratio=round(float(math.hypot(a,b)),7),
                      sine_residual_rms_ratio=round(rms,7))
    return result


def write_wav_new(path,pcm):
    with path.open('xb') as out:
        with wave.open(out,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(FS)
            w.writeframes(pcm.astype('<i2',copy=False).tobytes())


def replay(capture,*,expected_tone_hz=None,lowpass_hz=None):
    if lowpass_hz is not None and (not math.isfinite(lowpass_hz) or not 700<=lowpass_hz<=3200):
        raise ValueError('lowpass must be 700..3200Hz')
    raw=np.frombuffer(capture.pcm,dtype='<i2').copy()
    out=clock_correct(raw,capture.ticks_100us)
    record={'schema':1,'offline_only':True,'rf_verified':True,
            'source':capture.source,'source_sha256':hashlib.sha256(capture.pcm).hexdigest(),
            'session_token':capture.token,'original_packets':capture.accepted_packets,
            'required_packets':PACKETS,'complete':capture.complete,
            'partial_prefix_only':not capture.complete,'fabricated_missing_samples':False,
            'source_samples':len(raw),'original_pcm_rate_declared_hz':FS,
            'observed_source_pcm_rate_hz':round(source_rate(capture),5),
            'duration_ticks_100us':capture.ticks_100us,'output_pcm_rate_hz':FS,
            'source_nominal_report':describe(raw,expected_tone_hz=(None if expected_tone_hz is None else expected_tone_hz*FS/source_rate(capture))),
            'corrected':describe(out,expected_tone_hz=expected_tone_hz),
            'calibration_limitation':'Short MCU-clock timing, not direct PDM clock measurement; already aliased noise cannot be recovered',
            'status':'offline playback only; no firmware/radio change'}
    filtered=None
    if lowpass_hz is not None:
        # Noncausal zero-phase 3rd-order IIR is a listening / feasibility
        # upper-bound only; not the exact real-time ISR filter implementation.
        sos=butter(3,lowpass_hz,fs=FS,output='sos')
        f=sosfiltfilt(sos,out.astype(np.float64))
        filtered=np.clip(np.rint(f),-32768,32767).astype(np.int16)
        record.update(lowpass_hz=lowpass_hz,
                      filtered=describe(filtered,expected_tone_hz=expected_tone_hz),
                      filter_qualifier='offline zero-phase output-side filter, NOT high-rate anti-alias filter')
    return record,out,filtered


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    inp=ap.add_mutually_exclusive_group(required=True)
    inp.add_argument('--iq-c64',type=Path,help='RF pre-tuned 128kS/s C64 IQ; CRC decoded again')
    inp.add_argument('--wav',type=Path,help='complete RF-verified 8k-header PCM WAV')
    ap.add_argument('--metadata-json',type=Path,help='required with --wav')
    ap.add_argument('--allow-partial',action='store_true',help='for IQ: allow only contiguous verified seq0..N prefix')
    ap.add_argument('--expected-tone-hz',type=float)
    ap.add_argument('--lowpass-hz',type=float,help='optional offline voice lowpass A/B; not ISR filter')
    ap.add_argument('--output-prefix',type=Path,required=True)
    a=ap.parse_args()
    if (a.wav is not None)!=(a.metadata_json is not None):ap.error('use --wav with --metadata-json')
    if a.wav and a.allow_partial:ap.error('--allow-partial applies only to RF IQ')
    stem=a.output_prefix
    wav_out=stem.with_name(stem.name+'-clock-8k').with_suffix('.wav')
    filtered_out=stem.with_name(stem.name+'-lowpass-8k').with_suffix('.wav')
    report_out=stem.with_suffix('.json')
    paths=(wav_out,report_out)+((filtered_out,) if a.lowpass_hz is not None else ())
    for p in paths:
        if p.exists():ap.error(f'will not overwrite evidence: {p}')
        if not p.parent.is_dir():ap.error(f'output directory absent: {p.parent}')
    if a.wav:
        capture=read_verified_wav(a.wav,a.metadata_json)
    else:
        packets,_=parse_iq_complex64(a.iq_c64)
        capture=collect_packets(packets,allow_partial=a.allow_partial,source=str(a.iq_c64))
    report,corrected,filtered=replay(capture,expected_tone_hz=a.expected_tone_hz,lowpass_hz=a.lowpass_hz)
    write_wav_new(wav_out,corrected)
    if filtered is not None:write_wav_new(filtered_out,filtered)
    report['corrected_wav']=str(wav_out)
    if filtered is not None:report['filtered_wav']=str(filtered_out)
    with report_out.open('x') as f:f.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':main()
