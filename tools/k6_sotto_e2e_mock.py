#!/usr/bin/env python3
"""Mock installed Sotto voice: real ARM encoder -> virtual FSK -> real native RX -> WAV.

Offline only. This is deliberately NOT a serial/K6 hardware transport and never
keys the physical radio. It exercises the actual experimental Sotto API29 ARM
module and the pinned Sotto 1200 fixed-point decoder with modelled RF.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import prepare,trial,accepted
from tools.k6_sotto_opt_bench import read_pcm,mixed_pcm
from tools.k6_sotto_installed_vfo import (
    on_select_toggle, ptt_allowed, sotto_mode_installed, vfo_label,
)

SAMPLE_RATE=8000
SAMPLES_PER_SUPERFRAME=320
BYTES_PER_SUPERFRAME=6
DECODER_DELAY_SUPERFRAMES=2


def native_decoder(tree: Path, destination: Path):
    """Build pinned, real fixed-point 1200-bit/s C decoder; no mock PCM synthesis."""
    source=tree/'c/fixed'
    if not shutil.which('cc'):
        raise RuntimeError('native C compiler unavailable')
    inputs=[source/name for name in
            ('sotto_fx_dec.c','sotto_fx_lr.c','fx_math.c','fx_tables.c')]
    if not all(p.is_file() for p in inputs):
        raise RuntimeError('missing pinned fixed-point Sotto 1200 decoder source')
    subprocess.run(['cc','-O2','-shared','-fPIC',
                    '-DSOTTO_V36','-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
                    '-I'+str(source),'-I'+str(tree/'c'),
                    *map(str,inputs),'-o',str(destination),'-lm'],
                   check=True,capture_output=True,text=True,timeout=35)
    c=C.CDLL(str(destination))
    c.sotto_lr_dec_create.restype=C.c_void_p
    c.sotto_lr_dec_destroy.argtypes=[C.c_void_p]
    c.sotto_lr_decode.argtypes=[C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.POINTER(C.c_int16)]
    c.sotto_lr_dec_flush.argtypes=[C.c_void_p,C.POINTER(C.c_int16)]
    c.sotto_lr_dec_flush.restype=C.c_int
    return c


def decode_pcm(lib, codec:bytes)->np.ndarray:
    """Decode exactly 6-byte Sotto 1200 superframes, draining 2-frame delay."""
    if not codec or len(codec)%BYTES_PER_SUPERFRAME or len(codec)>10000:
        raise ValueError('Sotto 1200 payload must be a bounded nonempty multiple of 6 bytes')
    state=lib.sotto_lr_dec_create()
    if not state:
        raise RuntimeError('Sotto decoder state allocation failed')
    output=(C.c_int16*SAMPLES_PER_SUPERFRAME)()
    pcm=[]
    try:
        for i in range(0,len(codec),BYTES_PER_SUPERFRAME):
            frame=(C.c_uint8*BYTES_PER_SUPERFRAME).from_buffer_copy(codec[i:i+6])
            lib.sotto_lr_decode(state,frame,1,output)
            pcm.extend(output[:])
        # A terminated 1200 stream has 2 buffered superframes.
        for i in range(DECODER_DELAY_SUPERFRAMES):
            if lib.sotto_lr_dec_flush(state,output)!=1:
                raise RuntimeError('Sotto decoder flushed before its latency tail')
            pcm.extend(output[:])
    finally:
        lib.sotto_lr_dec_destroy(state)
    result=np.array(pcm,dtype='<i2')
    # The first two output windows are decoder priming, not received speech.
    start=DECODER_DELAY_SUPERFRAMES*SAMPLES_PER_SUPERFRAME
    result=result[start:start+(len(codec)//BYTES_PER_SUPERFRAME)*SAMPLES_PER_SUPERFRAME]
    if len(result)!=len(codec)//BYTES_PER_SUPERFRAME*SAMPLES_PER_SUPERFRAME:
        raise RuntimeError('Sotto decoded duration differs from compressed session')
    return result


def write_wav(path, samples):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(samples,dtype='<i2').tobytes())


class MockSottoRadio:
    """Small explicit installed-mode/PTT/RX/playback state machine.

    Uses the production installed-mode predicates, but maps that mock slot to
    the experimental SOTTO TX bench binary *only in this offline harness*.
    Do not mistake it for a resident Aura integration or a KISS provider.
    """
    def __init__(self,artifact,decoder,*,pcm=None):
        self.artifact=artifact
        self.decoder=decoder
        self.mode='FM'
        self.radio_state='FM'  # FM | RX | TX: RF is strictly half-duplex
        self.slots=[]
        self.running=False
        self.ptt=False
        self.pcm=pcm
        self.rx_audio=None
        self.last=None
        self.speaker_cursor=0

    @property
    def installed(self):
        return sotto_mode_installed(self.slots)

    def install(self):
        if self.running or self.ptt:
            raise RuntimeError('cannot install a module during TX')
        if self.installed:
            raise RuntimeError('Sotto already installed')
        self.slots.append(dict(rc=0,flags=1,name='SOTTO TEST'))

    def select(self):
        if self.running or self.ptt:
            raise RuntimeError('cannot change mode during TX')
        # Complete the existing legacy mode-key path, no shortcuts.
        self.mode='FM'
        for _ in range(5):
            self.mode=on_select_toggle(legacy=self.mode,sotto_installed=self.installed,
                                       sotto_selected=self.mode=='SOTTO')
        assert self.mode in ('SOTTO','FM')
        self.radio_state='RX' if self.mode=='SOTTO' else 'FM'
        return self.mode

    def _render_validated_receive(self,payload):
        """Audio playout is only allowed in RX after an independent RF check.

        This does not model a production on-chip decoder; it uses the pinned
        native Sotto decoder and strictly verified virtual FDV1 payload.
        """
        if (self.radio_state!='RX' or self.running or self.ptt
                or not self.installed or self.mode!='SOTTO'):
            raise RuntimeError('Sotto receive/playout unavailable outside RX')
        if not payload or payload!=self.artifact['reference']:
            raise RuntimeError('decoded RF payload differs from independent native encoder')
        audio=decode_pcm(self.decoder,payload)
        reference=decode_pcm(self.decoder,self.artifact['reference'])
        if not np.array_equal(audio,reference):
            raise RuntimeError('received PCM differs from native reference decoder')
        self.rx_audio=audio
        self.speaker_cursor=0

    def transmit(self,*,adc_cycles=4800,spi_us=10,spi_byte_us=0,channel=None):
        if self.ptt or self.running or self.radio_state=='TX':
            raise RuntimeError('PTT already held')
        if not ptt_allowed(sotto_selected=self.mode=='SOTTO',
                           sotto_ready=self.installed and self.mode=='SOTTO',
                           legacy_allowed=True) or self.mode!='SOTTO' or self.radio_state!='RX':
            raise RuntimeError('Sotto not installed and selected for RX')
        # PTT preempts reception and clears speaker/jitter state before TX.
        # No local RX codec processing or playout is allowed while keyed.
        self.rx_audio=None
        self.speaker_cursor=0
        self.radio_state='TX'
        self.ptt=True
        self.running=True
        try:
            result=trial(self.artifact,channel or Channel(),cpi=1.5,adc_cycles=adc_cycles,
                         spi_us=spi_us,spi_byte_us=spi_byte_us,capture_codec=True)
        finally:
            # The actual ARM TX oracle enforces its own RF/PA release; the
            # mock's control state *also* fails closed back to RX on errors.
            self.ptt=False
            self.running=False
            self.radio_state='RX'
        result['accepted']=accepted(result)
        self.last=result
        if not result['accepted']:
            return False
        # The independent virtual receiver has acquired this payload *after*
        # TX completed. Do not decode it as a simultaneous second RF path.
        self._render_validated_receive(result['received_codec_bytes'])
        return True


    def speaker_next(self):
        """Pull one 20-ms PCM block, as a virtual speaker sink would."""
        if self.ptt or self.running or self.radio_state=='TX':
            raise RuntimeError('speaker cannot start while the mock is transmitting')
        if self.rx_audio is None:
            return None
        if self.speaker_cursor >= len(self.rx_audio):
            return None
        block=self.rx_audio[self.speaker_cursor:self.speaker_cursor+160].copy()
        if len(block)!=160:
            raise RuntimeError('nonintegral 20-ms speaker frame')
        self.speaker_cursor+=160
        return block

    def remove(self):
        if self.ptt or self.running:
            raise RuntimeError('cannot remove Sotto while transmitting')
        self.slots=[]
        if self.mode=='SOTTO':
            self.mode='FM'
        self.radio_state='FM'
        self.rx_audio=None
        self.speaker_cursor=0

    def status(self):
        return dict(installed=self.installed,mode=vfo_label(
                    legacy='FM',sotto_installed=self.installed,
                    sotto_selected=self.mode=='SOTTO'),
                    ptt=self.ptt,running=self.running,radio_state=self.radio_state,
                    playback_ready=self.rx_audio is not None)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sotto-tree',type=Path,required=True)
    p.add_argument('--mic-wav',type=Path,help='8 kHz mono PCM16 WAV, 0.1-3s in 10ms blocks')
    p.add_argument('--fixture',choices=('triangle','mixed'),default='mixed')
    p.add_argument('--out-wav',type=Path,required=True,
                   help='decoded receiver audio at 8 kHz; written only on successful RF')
    p.add_argument('--report',type=Path)
    p.add_argument('--adc-cycles',type=int,default=4800)
    p.add_argument('--spi-us',type=float,default=10)
    p.add_argument('--spi-byte-us',type=float,default=0)
    p.add_argument('--cn',type=float,default=25,help='model carrier-to-noise dB')
    p.add_argument('--prefix-every',type=int,default=0)
    a=p.parse_args(argv)
    if not 0<=a.adc_cycles<12000 or not 0<a.spi_us<=1000 or not 0<=a.spi_byte_us<=100:
        p.error('invalid offline timing model')
    if not 0<a.cn<100 or not 0<=a.prefix_every<=100:
        p.error('invalid channel model')
    if not (a.sotto_tree/'c/k6/mod/sotto_live_tx.c').is_file():
        p.error('experimental API29 Sotto development checkout required')
    try:
        pcm=read_pcm(a.mic_wav) if a.mic_wav else mixed_pcm() if a.fixture=='mixed' else None
    except (ValueError, OSError,wave.Error) as e:
        p.error(str(e))
    with tempfile.TemporaryDirectory(prefix='sotto-e2e-mock-') as tmp:
        d=Path(tmp)
        artifact=prepare(a.sotto_tree,d,pcm=pcm)
        decoder=native_decoder(a.sotto_tree,d/'sotto1200.so')
        radio=MockSottoRadio(artifact,decoder,pcm=pcm)
        if radio.select()!='FM' or radio.status()['installed']:
            raise RuntimeError('uninstalled Sotto appeared in mode cycle')
        radio.install()
        if radio.select()!='SOTTO':
            raise RuntimeError('installed Sotto not selectable')
        success=radio.transmit(adc_cycles=a.adc_cycles,spi_us=a.spi_us,
                               spi_byte_us=a.spi_byte_us,
                               channel=Channel(cn_db=a.cn,prefix_every=a.prefix_every))
        result=radio.last
        record=dict(schema=1,offline_only=True,source_revision=artifact['revision'],
                    source_dirty=artifact['dirty'],
                    source='actual compiled Sotto API29 ARM + independent virtual IQ C/Python RX + actual native fixed-point Sotto1200 decoder',
                    mode=radio.status()['mode'],passed=success,
                    mock_install_identity='SOTTO TEST',
                    module_runtime_identity='SOTTO TX',
                    identity_binding='mock-only; current Aura VFO does not launch API29 live TX from this identity',
                    pcm_input_samples=len(pcm) if pcm is not None else 8320,
                    codec_frames=result['codec_frames'],codec_exact=result['codec_exact'],
                    rx_packets=result['received'],rf_session_complete=result['session']['complete'],
                    decoded_pcm_samples=int(len(radio.rx_audio)) if radio.rx_audio is not None else 0,
                    decoded_pcm_sha256=hashlib.sha256(radio.rx_audio.tobytes()).hexdigest() if radio.rx_audio is not None else None,
                    rms=float(np.sqrt(np.mean(radio.rx_audio.astype(np.float64)**2))) if radio.rx_audio is not None else None,
                    adc_cycles=a.adc_cycles,spi_setup_us=a.spi_us,spi_byte_us=a.spi_byte_us,
                    modem_code_bytes=result['module_bytes'],stack_bytes=result['stack_bytes'],
                    resource_reads=result['spi_reads'],rx_clean=result['rx'] is not None and all(result['rx'][k]==0 for k in ('overruns','underflows','gaps')),
                    tx_abort=result['tx']['aborted'],tx_idle=not result['tx']['active'])
        if success:
            audio_blocks=[]
            while (block:=radio.speaker_next()) is not None:
                audio_blocks.append(block)
            emitted=np.concatenate(audio_blocks)
            if not np.array_equal(emitted,radio.rx_audio):
                raise RuntimeError('virtual speaker output differs from RF-decoded samples')
            record['speaker_blocks_20ms']=len(audio_blocks)
            record['speaker_exact']=True
            write_wav(a.out_wav,emitted)
            print('Decoded receiver WAV:',a.out_wav)
        radio.remove()
        record['post_uninstall']=radio.status()
        if a.report:
            a.report.parent.mkdir(parents=True,exist_ok=True)
            a.report.write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(record,indent=2))
        return 0 if success and record['tx_idle'] and not record['post_uninstall']['installed'] else 1


if __name__=='__main__':
    sys.exit(main())
