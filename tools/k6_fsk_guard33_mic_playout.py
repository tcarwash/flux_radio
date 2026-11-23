#!/usr/bin/env python3
"""Strict *offline* replay of K6 microphone Sotto speech recovered over RF.

Consumes the independently CRC-valid FSK-14 SDR result, reassembles the
Sotto-1200 message (26 x 6B), and decodes audio back to a private WAV.
All voice data, raw packet hex, PCM, Sotto compressed bits and IQ remain
inside mode-0700 private directories. Public output is hash/counts only.
No hardware access, PTT, serial, SDR device or firmware writes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import wave
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import pager
from tools.k6_fsk_guard33_mic_bench import frozen_build

DECODER=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_dec_fx')
DECODER_SHA='408b4f4a8c1c5999bf66d20c72f104835a071a969fde8470dee221501121c741'
SOURCE_COMPRESSED=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit')
REFERENCE_WAV=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-received-8k.wav')


def sha_file(p:Path):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def private_write(path:Path,data:bytes):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)


def run(rxfile:Path,out:Path):
    # Fail before output if private permission policy or output slot unsafe.
    if not out.is_dir() or out.stat().st_mode&0o077:
        raise PermissionError('Output must be an existing mode-0700 private folder')
    outputs=[out/x for x in ('fsk14-rf-reassembled-sotto1200.bit',
                             'fsk14-rf-decoded-8k.raw',
                             'fsk14-rf-decoded-8k.wav',
                             'fsk14-rf-audio-replay-report.json')]
    if any(f.exists() for f in outputs):
        raise FileExistsError('Refuse overwriting RF speech recovery evidence')
    if sha_file(DECODER)!=DECODER_SHA:
        raise RuntimeError('Decoder binary not pinned to original host Sotto codec')
    known=frozen_build()
    rx=json.loads(rxfile.read_text())
    if rx['frames_detected']!=4 or rx['crc_valid']!=4 or rx['crc_ambiguous']!=0:
        raise RuntimeError('No strict 4/4 independent SDR RF packet recovery')
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    asm=pager.Assembler(profile)
    for i,f in enumerate(rx['frames']):
        if (not f['valid'] or f['ambiguous'] or f['seq']!=i or
            f['msg_id']!=93 or f['total_codec_frames']!=26 or
            f['offset_bytes'] not in (0,2) or f['sync_errors']!=0 or
            f['selected_baud']!=4800 or
            f['packet_sha256']!=known['expected_packet_hashes'][i]):
            raise RuntimeError('Invalid recovered RF mic speech packet '+str(i))
        packet=bytes.fromhex(f['packet_hex'])
        parsed=profile.unpack(packet)
        if parsed is None or parsed['msg_id']!=93 or parsed['seq']!=i:
            raise RuntimeError('Invalid independent CRC check '+str(i))
        asm.push(packet)
    restored,good=asm.frames(93)
    if len(good)!=26 or not good.all() or len(restored)!=26*6:
        raise RuntimeError('26 original compressed mic frames not completely recovered')
    # This truth comparison is AFTER the independently decoded RF packets.
    if restored!=SOURCE_COMPRESSED.read_bytes():
        raise RuntimeError('RF reassembled Sotto bytes differ from the original mic source')
    private_write(outputs[0],restored)
    try:
        subprocess.run([str(DECODER),'-1200',str(outputs[0]),str(outputs[1])],
                       check=True,capture_output=True,timeout=30)
        outputs[1].chmod(0o600)
        pcm=outputs[1].read_bytes()
        if len(pcm)!=26*320*2:
            raise RuntimeError('Sotto PCM decoder length mismatch')
        # First generate the WAV into memory, then atomically create the
        # 0600 private file; no bytes ever printed or committed.
        from io import BytesIO
        buffer=BytesIO()
        with wave.open(buffer,'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(pcm)
        private_write(outputs[2],buffer.getvalue())
        report={
            'experiment':'FSK-14 RF-recovered real-K6-mic Sotto-1200 independent playout',
            'privacy':'private audio and packet data, hash-only public evidence',
            'source':'4 CRC-valid L64 voice packets demodulated from original RF capture',
            'expected_packets':4,'rf_packets_crc_valid':4,
            'recovered_superframes':int(good.sum()),
            'compressed_voice_bytes':len(restored),
            'recovered_source_sotto_sha256':sha_file(outputs[0]),
            'original_source_sotto_sha256':sha_file(SOURCE_COMPRESSED),
            'compressed_source_bit_exact':True,
            'pcm_samples_8khz':len(pcm)//2,
            'audio_duration_s':round((len(pcm)//2)/8000,3),
            'decoded_pcm_sha256':sha_file(outputs[1]),
            'decoded_wav_sha256':sha_file(outputs[2]),
            'prior_host_decode_wav_sha256':sha_file(REFERENCE_WAV),
            'rf_recovered_wav_equals_original_host_reconstruction':
                outputs[2].read_bytes()==REFERENCE_WAV.read_bytes(),
            'decoder_binary_sha256':DECODER_SHA,
            'hardware_access':False,
            'not_live_radio_dv':True,
        }
        private_write(outputs[3],(json.dumps(report,indent=2)+'\n').encode())
        return report
    except Exception:
        # Never leave unprotected output if decode failed; original raw
        # RF capture and independent received packet JSON are untouched.
        for f in outputs:
            if f.exists():f.unlink()
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rx',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    x=run(a.rx,a.out)
    print('RF K6 mic speech 4/4 packets',x['recovered_superframes'],'/26 Sotto frames',
          x['audio_duration_s'],'sec host decoded; source bit-exact',
          x['compressed_source_bit_exact'],'original host WAV match',
          x['rf_recovered_wav_equals_original_host_reconstruction'])
    print('RF-recovered decoded WAV sha256',x['decoded_wav_sha256'])


if __name__=='__main__':main()
