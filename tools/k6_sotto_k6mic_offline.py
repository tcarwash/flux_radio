#!/usr/bin/env python3
"""Convert a saved *K6 microphone* capture into CRC-valid native-FSK Sotto L packets.

Entirely OFFLINE: no serial port, SDR, RF/PTT, firmware or lock access. Read
already validated K6 micvoice WAV and its independently measured source rate,
resample to Sotto 8000 Hz PCM, call pinned fixed-point Sotto-1200 codec,
pack using existing Flux L/64 profile, then reassemble/CRC/decode for checks.
Audio and packets are written only in the dedicated output bench folder, not
committed to Git or to any other agent's worktree. Real microphone material
still requires subsequent RF transmission and live throughput validation.
"""
from __future__ import annotations
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import pager
from tools.k6_sotto_fsk_offline import simulate

FS=8000
SOTTO_FRAME_S=.04
SAMPLES_PER_FRAME=320
FRAME_BYTES=6
L64_AIRTIME_S=512/4800 + .044375


def sha(path:Path)->str:
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def load_k6_audio(source_wav:Path, meta_json:Path)->tuple[np.ndarray,float,dict]:
    m=json.loads(meta_json.read_text())
    with wave.open(str(source_wav),'rb') as w:
        sample_rate=w.getframerate()
        n=w.getnframes()
        if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getcomptype()!='NONE':
            raise ValueError('K6 microphone source must be uncompressed s16 mono WAV')
        sample_bytes=w.readframes(n)
    audio=np.frombuffer(sample_bytes,dtype='<i2').copy()
    if audio.size!=n or n!=m['samples']:
        raise ValueError('K6 microphone sample count mismatch')
    actual=float(m['measured_sample_rate_hz'])
    if not (7600<actual<8400) or abs(sample_rate-actual)>2:
        raise ValueError('K6 capture sample-rate metadata differs from WAV header')
    if m.get('clipping_events',0)!=0:
        raise ValueError('Captured ADC clipping events; require separate audio review')
    if not (0<len(audio)/actual<=30):
        raise ValueError('Capture duration must be between 0 and 30 seconds')
    return audio,actual,m


def resample_voice(source_pcm:np.ndarray, source_rate:float)->tuple[np.ndarray,int]:
    if source_rate<=0 or source_pcm.size==0:raise ValueError('No microphone data')
    # Pin accurate measured ADC timing rather than the rounded integer WAV rate;
    # keep resampling ratio denominator bounded to a small deterministic filter.
    ratio=Fraction(FS/source_rate).limit_denominator(10000)
    sampled=resample_poly(source_pcm.astype(np.float64),ratio.numerator,ratio.denominator)
    expected_n=round(source_pcm.size*FS/source_rate)
    sampled=sampled[:expected_n]
    if len(sampled)<expected_n:
        sampled=np.pad(sampled,(0,expected_n-len(sampled)))
    if np.max(np.abs(sampled))>32767:
        # No extra AGC: the user's micvoice WAV has already been normalized.
        sampled=sampled*(32767/np.max(np.abs(sampled)))
    pcm=np.rint(np.clip(sampled,-32768,32767)).astype('<i2')
    # Sotto-1200 encodes exactly one six-byte superframe per 320 samples.
    n_frames=(len(pcm)+SAMPLES_PER_FRAME-1)//SAMPLES_PER_FRAME
    padded=np.pad(pcm,(0,n_frames*SAMPLES_PER_FRAME-len(pcm)),mode='constant').astype('<i2')
    return padded,int(len(padded)-len(pcm))


def encode_packets(pcm:np.ndarray, encoder:Path, decoder:Path, out:Path,
                   *,msg_id=93,depth=1,parity=False)->dict:
    out.mkdir(parents=True,exist_ok=False)
    if not 0<=msg_id<=255:raise ValueError('msg_id out of range')
    profile=pager.profile('L','1200',depth=depth,parity=parity,short_last=True)
    n_frames=len(pcm)//SAMPLES_PER_FRAME
    if n_frames*SAMPLES_PER_FRAME!=len(pcm) or n_frames<=0:
        raise ValueError('Input must be one or more whole 40-ms Sotto frames')
    pcm_path=out/'k6mic-resampled-8k.raw'
    pcm_path.write_bytes(pcm.tobytes())
    compressed_path=out/'k6mic-sotto1200.bit'
    subprocess.run([str(encoder),'-1200',str(pcm_path),str(compressed_path)],check=True,capture_output=True,timeout=40)
    compressed=compressed_path.read_bytes()
    if len(compressed)!=FRAME_BYTES*n_frames:
        raise ValueError(f'Expected {n_frames} Sotto frames, encoder output {len(compressed)} bytes')
    wire=profile.pack(compressed,msg_id=msg_id)
    if not wire or any(len(p)!=64 or profile.unpack(p) is None for p in wire):
        raise ValueError('Flux native L64 packet CRC/protocol failure')
    (out/'k6mic-l64-packets.bin').write_bytes(b''.join(wire))
    # Own packet decoding path, with reassembly and compressed-bit exactness.
    assembler=pager.Assembler(profile)
    for i,p in enumerate(wire):
        parsed=profile.unpack(p)
        if parsed and parsed['parity'] is None and parsed['seq']!=i and not parity:
            raise ValueError('Nonconsecutive packet sequence')
        assembler.push(p)
    restored,good=assembler.frames(msg_id)
    if good.shape[0]!=n_frames or not good.all() or restored!=compressed:
        raise ValueError('Packet unpack/CRC/reassembly was not bit exact')
    (out/'k6mic-sotto-rx-reassembled.bit').write_bytes(restored)
    # Decode compressed source through the *same* fixed-point codec as prior
    # physical Sotto packet validation. This is a lossy reconstruction of mic
    # audio; equality to the original PCM is not an expected codec property.
    decoded_path=out/'k6mic-received-8k.raw'
    subprocess.run([str(decoder),'-1200',str(out/'k6mic-sotto-rx-reassembled.bit'),str(decoded_path)],
                   check=True,capture_output=True,timeout=40)
    decoded=np.fromfile(decoded_path,dtype='<i2')
    if len(decoded)!=n_frames*SAMPLES_PER_FRAME:
        raise ValueError('Sotto decoder output length mismatch')
    wav_path=out/'k6mic-received-8k.wav'
    with wave.open(str(wav_path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(FS)
        w.writeframes(decoded.tobytes())
    waveform_rms=float(np.sqrt(np.mean(decoded.astype(np.float64)**2)))
    model=simulate(compressed,depth=depth,parity=parity,payload='L',
                   playout_buffer_ms=160,jitter_ms=0,pipeline_ms=20,seed=2)
    return dict(audio_rate_hz=FS,voice_samples=len(pcm),
        encoded_superframes=n_frames,voice_seconds=round(n_frames*SOTTO_FRAME_S,3),
        codec='Sotto 1200 fixed-point',compressed_voice_bytes=len(compressed),
        packet_profile='Flux L64',packet_bytes=64,packets=len(wire),
        msg_id=msg_id,depth=depth,parity=parity,packet_seqs=[profile.unpack(p)['seq'] for p in wire if profile.unpack(p)['parity'] is None],
        crc_good_packets=len(wire),reassembled_frames=int(good.sum()),
        decoded_8k_pcm_samples=len(decoded),decoded_peak=int(np.max(np.abs(decoded.astype(np.int32)))),
        decoded_rms=round(waveform_rms,2),
        fixed_frame_rf_airtime_s=round(len(wire)*L64_AIRTIME_S,6),
        synthetic_queue_model=dict(voice_s=model.voice_s,packets=model.packets,
                                   capture_to_playout_ms=model.capture_to_playout_ms,
                                   concealed_frames=model.concealed_frames,
                                   air_fraction=model.air_fraction),
        offline_only=True,actual_radio_tx=False,
        sample_files_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()},
        limitations=['Previously recorded K6 microphone, not a live RF voice call',
                     'Codec is lossy; bit-exact check applies to compressed packet bytes, not original PCM',
                     'Packet model assumes measured L64 restart and ideal receive; no new RF captured'])


def run(wav:Path,metadata:Path,encoder:Path,decoder:Path,out:Path,*,msg_id=93)->dict:
    if not wav.is_file() or not metadata.is_file():raise FileNotFoundError('Missing K6 microphone source')
    src,rate,meta=load_k6_audio(wav,metadata)
    audio,pad=resample_voice(src,rate)
    data=encode_packets(audio,encoder,decoder,out,msg_id=msg_id)
    data.update(source_wav=str(wav),source_wav_sha256=sha(wav),
                source_metadata=str(metadata),source_metadata_sha256=sha(metadata),
                source_sample_rate_hz=rate,original_source_samples=len(src),
                original_source_s=round(len(src)/rate,6),zero_pad_samples_8k=pad,
                source_adc_clipping_events=meta['clipping_events'],
                encoder_binary_sha256=sha(encoder),decoder_binary_sha256=sha(decoder))
    (out/'k6mic-sotto-offline-report.json').write_text(json.dumps(data,indent=2)+'\n')
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True,type=Path)
    p.add_argument('--meta',required=True,type=Path)
    p.add_argument('--encoder',required=True,type=Path)
    p.add_argument('--decoder',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--msg-id',type=int,default=93)
    a=p.parse_args()
    r=run(a.capture,a.meta,a.encoder,a.decoder,a.out,msg_id=a.msg_id)
    print('K6 mic -> Sotto-1200',r['original_source_s'],'s ->',r['voice_seconds'],
          's,',r['encoded_superframes'],'codec frames,',r['packets'],'L64 packets,',
          r['crc_good_packets'],'CRC checked;', 'offline only, no RF')

if __name__=='__main__':main()
