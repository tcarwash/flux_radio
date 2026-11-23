#!/usr/bin/env python3
"""Use the K6-oriented *slim* Sotto 1200 encoder on saved real K6 microphone audio.

This is entirely offline; consumes only previously saved 8-kHz PCM and
produces private .bit/.bin/.raw/.wav records and a hash-only JSON manifest.
Unlike the full encoder, the slim binary consumes IN.raw OUT.bit and is
low-memory MCU-oriented, but this host run DOES NOT prove MCU clock or SPI
throughput. The same Flux L64 pager format and CRC/whitening are used.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import pager
from tools.k6_sotto_fsk_offline import simulate


def hash_file(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(pcm_file:Path,slim_encoder:Path,decoder:Path,out:Path,*,msg_id=94):
    if not 0<=msg_id<=255:raise ValueError('invalid message ID')
    pcm=pcm_file.read_bytes()
    if not pcm or len(pcm)%640:raise ValueError('8-kHz PCM must contain full 40ms superframes')
    source_frames=len(pcm)//640
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    compressed_file=out/'slim-sotto1200.bit'
    subprocess.run([str(slim_encoder),str(pcm_file),str(compressed_file)],
                   check=True,capture_output=True,timeout=45)
    stream=compressed_file.read_bytes()
    if len(stream)!=source_frames*6:
        raise ValueError(f'Slim stream expected {source_frames} superframes; got {len(stream)//6}')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=p.pack(stream,msg_id=msg_id)
    if not packets or any(len(x)!=64 for x in packets):raise ValueError('Bad slim packet count/shape')
    (out/'slim-l64-packets.bin').write_bytes(b''.join(packets))
    assembler=pager.Assembler(p)
    for k,packet in enumerate(packets):
        info=p.unpack(packet)
        if info is None or info['seq']!=k or info['msg_id']!=msg_id:
            raise ValueError('Slim compressed packet CRC/sequence failed')
        assembler.push(packet)
    recv,quality=assembler.frames(msg_id)
    if not quality.all() or recv!=stream:raise ValueError('Compressed Sotto payload not exactly reconstructed')
    (out/'slim-rx-reassembled.bit').write_bytes(recv)
    decoded=out/'slim-recovered-8k.raw'
    subprocess.run([str(decoder),'-1200',str(out/'slim-rx-reassembled.bit'),str(decoded)],
                   check=True,capture_output=True,timeout=40)
    d=np.fromfile(decoded,dtype='<i2')
    if len(d)!=source_frames*320:raise ValueError('Host decoder sample count mismatch')
    wav=out/'slim-recovered-8k.wav'
    with wave.open(str(wav),'wb') as wf:
        wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(8000);wf.writeframes(d.tobytes())
    sim=simulate(stream,depth=1,parity=False,payload='L',
                 playout_buffer_ms=160,jitter_ms=0,pipeline_ms=20)
    r=dict(experiment='Saved K6 mic -> slim Sotto 1200 -> Flux L64 -> CRC/assembler -> host PCM',
           offline_only=True,actual_radio_tx=False,actual_cortex_m0_timing_measured=False,
           encoder='Sotto-1200 slim host build; C port designed for Cortex-M0 and SPI codebooks',
           encoder_binary_sha256=hash_file(slim_encoder),
           decoder_binary_sha256=hash_file(decoder),
           source_pcm_sha256=hash_file(pcm_file),
           source_superframes=source_frames,
           compressed_bytes=len(stream),compressed_sha256=hash_file(compressed_file),
           voice_s=source_frames*.04,packet_profile='L64/CRC16/PN9',
           message_id=msg_id,packet_count=len(packets),
           crc_accepted_packets=len(packets),
           reconstructed_superframes=int(quality.sum()),
           compressed_bits_roundtrip_exact=recv==stream,
           decoded_pcm_samples=len(d),decoded_pcm_sha256=hash_file(decoded),
           decoded_peak=int(np.max(np.abs(d.astype(np.int32)))),
           modeled_capture_to_playout_ms=sim.capture_to_playout_ms,
           local_private_wav=str(wav),
           artifact_sha256={p.name:hash_file(p) for p in out.iterdir() if p.is_file()},
           limitations=['Slim encoder codec differs from full encoder; bitstream equality NOT expected',
                        'Host PCM output is lossy relative to original mic PCM',
                        'No on-radio CPU/SPI/stack proof, no new RF transmitter/SDR capture'])
    (out/'slim-mic-packet-report.json').write_text(json.dumps(r,indent=2)+'\n')
    return r


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pcm',required=True,type=Path)
    ap.add_argument('--encoder',required=True,type=Path)
    ap.add_argument('--decoder',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--msg-id',type=int,default=94)
    a=ap.parse_args()
    r=run(a.pcm,a.encoder,a.decoder,a.out,msg_id=a.msg_id)
    print('Offline slim Sotto:',r['source_superframes'],'superframes',r['packet_count'],
          'L64 packets CRC',r['crc_accepted_packets'],'voice seconds',r['voice_s'],
          'NO radio activity')
if __name__=='__main__':main()
