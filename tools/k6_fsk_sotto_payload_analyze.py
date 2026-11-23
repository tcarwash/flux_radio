#!/usr/bin/env python3
"""Offline RF I/Q decoder + real CRC validation for the 2 fixed FSK-11 Sotto packets.

Uses first 256 known training bits per packet to acquire. Recovered bytes are
based on 512 frequency symbol decisions, NOT copied from expected manifests.
The existing Flux L packet CRC/whitening parser validates the recovered bytes.
This is pilot-aided reception, not blind over-the-air packet acquisition.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from scipy.signal import butter,sosfiltfilt
from scipy.ndimage import uniform_filter1d
from modem import pager
from k6_fsk_lab_vector import build_vector
from k6_fsk_lab_decode import decode_samples
from k6_fsk_iq_edges import read_iq, FS

MANIFEST=Path(__file__).resolve().parents[1]/'tests/data/k6/fsk11_sotto_l_payload_manifest.json'


def expected_packets():
    m=json.loads(MANIFEST.read_text())
    payloads=[bytes.fromhex(x) for x in m['packet_hex']]
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    if len(payloads)!=2 or any(len(x)!=64 or not p.unpack(x) for x in payloads):
        raise RuntimeError('Frozen packet manifest invalid')
    return payloads,p,m


def rf_recover_bits(iq,fit,*,fs=FS,nbits=512):
    if not fit['locked'] or fit['training_bits']!=256:
        raise ValueError('Missing training-only RF acquisition')
    filt=sosfiltfilt(butter(3,8000,fs=fs,output='sos'),np.asarray(iq))
    discr=np.angle(filt[1:]*np.conj(filt[:-1]))*fs/(2*np.pi)
    discr=uniform_filter1d(discr,size=max(3,int(round(.16*fs/float(fit['candidate_baud_estimate'])))))
    bit_times_s=float(fit['detected_first_payload_bit_center_s'])+np.arange(nbits)/float(fit['candidate_baud_estimate'])
    sample_times=bit_times_s*fs
    if sample_times[0]<0 or sample_times[-1]>=len(discr)-1:
        raise ValueError('RF fit outside known I/Q capture')
    symbols=np.interp(sample_times,np.arange(len(discr)),discr)
    bit0,bit1=fit['median_training_frequency_bit0_hz_relative'],fit['median_training_frequency_bit1_hz_relative']
    polarity=1 if bit1>bit0 else -1
    return ((symbols-(bit0+bit1)/2)*polarity>0).astype(np.uint8)


def analyze(path:Path):
    payloads,p,m=expected_packets()
    iq=read_iq(path,duration=1.8,offset=280500.)
    fits=[];recovered=[];parsed=[];bit_errors=[];crc_ok=[]
    for i,known in enumerate(payloads):
        bits=''.join(f'{b:08b}' for b in known)
        _,vec=build_vector(word_count=32,baud=4800,gain=21,data_bits=bits)
        fit=decode_samples(iq,vec,train_bits=256,lowpass_hz=8000.)
        fits.append(fit)
        if not fit.get('locked'):
            recovered.append(None);parsed.append(None);bit_errors.append(None);crc_ok.append(False)
            continue
        observed=rf_recover_bits(iq,fit)
        decoded=np.packbits(observed).tobytes()
        known_bits=np.unpackbits(np.frombuffer(known,dtype=np.uint8))
        errors=int(np.count_nonzero(observed!=known_bits))
        recovered.append(decoded)
        packet=p.unpack(decoded)
        parsed.append(None if packet is None else {'seq':packet['seq'],'msg_id':packet['msg_id'],
            'frames':len(packet['frames'])})
        bit_errors.append(errors)
        crc_ok.append(packet is not None and packet['seq']==i and packet['msg_id']==33)
    rx=pager.Assembler(p)
    for pkt,good in zip(recovered,crc_ok):
        if pkt is not None and good:rx.push(pkt)
    assembled=rx.frames(33) if 33 in rx.messages else None
    frame_count=int(assembled[1].sum()) if assembled else 0
    start=[f.get('detected_first_payload_bit_center_s') for f in fits]
    diff=(start[1]-start[0]) if all(x is not None for x in start) else None
    complete=(all(crc_ok) and all(x==0 for x in bit_errors) and frame_count==16)
    return dict(experiment='FSK-11 actual Sotto L payload over native FSK',
        radio_transmitted_by_analyzer=False,source_recording=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        codec_source_sha256=m['source_sha256'],
        packet_sha256s=m['packet_sha256'],
        packet_bits_decoded=512,packet_bits_training=256,packet_bits_heldout=256,
        frame_fits=fits,received_packets_hex=[x.hex() if x else None for x in recovered],
        payload_total_errors=bit_errors,crc_ok=crc_ok,parsed_headers=parsed,
        codec_frames_crc_valid=frame_count,
        payload_start_separation_ms=round(diff*1000,3) if diff else None,
        nonpayload_rearm_ms=round((diff-512/4800)*1000,3) if diff else None,
        result='PASS' if complete else 'INCONCLUSIVE_OR_FAIL',
        limitations=['Known 256-bit per-frame training was used for RF acquisition (NOT blind sync).',
            'No physical microphone input, decoder PCM playback or demonstrated continuous streaming.',
            'No calibrated RF emissions/range measurements or long-run BER.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('refuse to overwrite analysis')
    r=analyze(args.iq)
    args.out.write_text(json.dumps(r,indent=2)+'\n')
    print('FSK-11',r['result'],'CRC',r['crc_ok'],'Sotto frames',r['codec_frames_crc_valid'],
          'payload bit errors',r['payload_total_errors'],'spacing_ms',r['payload_start_separation_ms'])


if __name__=='__main__':main()
