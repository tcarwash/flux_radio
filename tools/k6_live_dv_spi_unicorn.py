#!/usr/bin/env python3
"""Full combined Cortex-M0 Unicorn execution of incremental Sotto+SPI+guard queue.

Executes REAL linked ARMv6-M Thumb instructions for the 10ms codec,
SPI-backed IVF and shape-codebook access, and the guarded 64+2-byte
packet scheduler. K6 codebook/deck reads are modeled as ordinary
memory-mapped bytes in emulator address space. There is NO actual
SPI1 peripheral timing, GPIO/ADC, PA, serial, RF, firmware flash or
hardware UART. The modeled ELF is UNSAFE/NOT FLASHABLE.

The input real microphone PCM and full emulated output stay PRIVATE.
A separate Python Flux pager and prior host encode provide bit-exact
truth only after the ARM code runs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.k6_live_dv_guard_unicorn import make_emu,signed32
from tools.k6_live_dv_spi_deck import build_deck,SLIM_CELLS
from tools.k6_live_dv_spi_m0_size_probe import build as build_arm

SOURCE_PCM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw')
REFERENCE_SLIM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit')
REFERENCE_GUARD_SHA='923e1956c18ecfd75cdb5e8778ea6dbff93b55970a95f330054aa4cdf504f537'
REFERENCE_CODEC_SHA='8c20829a11ce6c3f22162b6dccfbdf967a6eb30b0bd4646ba5'
EMBEDDED=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/slim.elf')
CB_REGIONS=(10240,10240,5120,5120)
CB_SYMBOLS=('fx_cb0','fx_cb1','fx_cb2','fx_cb3')
CB_BASE=0x00100020
DECK_BASE=0x00148010
ARM_CTX=0x20003c00


def run(elf:Path,pcm:bytes):
    if not pcm or len(pcm)%160:raise ValueError('Input must be 10ms s16/8kHz PCM blocks')
    cm0,m=make_emu(elf)
    # Codebook words from the independent ROM-resident Cortex-M0 ELF,
    # never from the known output packets. This is public codec data.
    _,lib=make_emu(EMBEDDED)
    codebook=b''.join(bytes(lib.mu.mem_read(lib.sym[n],size))
                      for n,size in zip(CB_SYMBOLS,CB_REGIONS))
    if len(codebook)!=30720:raise ValueError('Incorrect public shape codebook size')
    deck=build_deck(SLIM_CELLS.read_text())
    mu=m.mu
    mu.mem_map(0x00100000,0x00100000)
    mu.mem_write(CB_BASE,codebook)
    mu.mem_write(DECK_BASE,deck)

    r,_=m.call('dv_flash_configure',m.sym['dv_emulated_spi_read']|1,0,DECK_BASE)
    if signed32(r)!=0 or m.call('dv_flash_fault')[0]!=0:
        raise RuntimeError('Real ARM K6SC deck verification failed')
    # Nonfatal fault injection must be sticky and reject a corrupt deck,
    # *before* any codec state/output is generated.
    corrupted=bytearray(deck);corrupted[-5]^=0x20
    mu.mem_write(DECK_BASE,bytes(corrupted))
    r,_=m.call('dv_flash_configure',m.sym['dv_emulated_spi_read']|1,0,DECK_BASE)
    if signed32(r)!=-1 or m.call('dv_flash_fault')[0]!=1:
        raise RuntimeError('Corrupted SPI flash deck was accepted by real ARM verification code')
    mu.mem_write(DECK_BASE,deck)
    r,_=m.call('dv_flash_configure',m.sym['dv_emulated_spi_read']|1,0,DECK_BASE)
    if signed32(r)!=0 or m.call('dv_flash_fault')[0]!=0:
        raise RuntimeError('Correct SPI deck not accepted after fault reset')

    codec_ctx=cm0.cm4.IO+0x10000
    scheduler_ctx=cm0.cm4.IO+0x20000
    audio_in=cm0.cm4.IO
    frame_out=cm0.cm4.IO+0x1000
    phy_out=cm0.cm4.IO+0x2000
    state_bytes,_=m.call('slim_enc_bytes')
    queue_bytes,_=m.call('dv_arm_state_bytes')
    if state_bytes!=3148 or queue_bytes!=204:
        raise RuntimeError('Cortex-M0 codec/queue ABI state length mismatch')
    m.call('slim_enc_init',codec_ctx)
    m.call('dv_arm_begin',scheduler_ctx,112)
    m.paint_stack()
    bits=bytearray();frames=[];cyc=[]

    def enqueue_codec(voice_frame:bytes):
        mu.mem_write(frame_out,voice_frame)
        status,c=m.call('dv_arm_push',scheduler_ctx,frame_out)
        cyc.append(c)
        status=signed32(status)
        if status not in (1,2):
            raise RuntimeError('Actual ARM queue rejected valid 6-byte codec frame')
        if status==1:drain()

    def drain():
        status,c=m.call('dv_arm_peek',scheduler_ctx,phy_out)
        cyc.append(c)
        if signed32(status)!=0:
            raise RuntimeError('Actual ARM FIFO peek failed')
        frames.append(bytes(mu.mem_read(phy_out,66)))
        status,c=m.call('dv_arm_confirm',scheduler_ctx,1)
        cyc.append(c)
        if signed32(status)!=0:raise RuntimeError('Actual ARM TX completion ACK failed')

    enc_cycles=[]
    for i in range(len(pcm)//160):
        mu.mem_write(audio_in,pcm[i*160:(i+1)*160])
        emitted,c=m.call('slim_encode10',codec_ctx,audio_in,frame_out)
        enc_cycles.append(c)
        if emitted not in (0,1):raise RuntimeError('Invalid emulated slim_encode10 result')
        if emitted:
            frame=bytes(mu.mem_read(frame_out,6))
            bits.extend(frame)
            enqueue_codec(frame)
        if m.call('dv_flash_fault')[0]:
            raise RuntimeError('Real ARM SPI vector fetch failed during codec call')
    flushes=0
    while True:
        emitted,c=m.call('slim_flush',codec_ctx,frame_out)
        if emitted==0:break
        if emitted!=1 or flushes>=3:raise RuntimeError('Invalid emulated slim codec flush')
        flushes+=1
        frame=bytes(mu.mem_read(frame_out,6))
        bits.extend(frame);enqueue_codec(frame)
        if m.call('dv_flash_fault')[0]:
            raise RuntimeError('Real ARM SPI fault during flush')
    status,_=m.call('dv_arm_finish',scheduler_ctx)
    if signed32(status)==1:drain()
    elif signed32(status)!=0:raise RuntimeError('ARM packet queue PTT end failed')
    if m.call('dv_arm_queue_depth',scheduler_ctx)[0]!=0:
        raise RuntimeError('Unsent ARM voice packets remain in scheduler')
    if len(bits)!=156 or len(frames)!=4:raise RuntimeError('Wrong real mic voice/guard count')
    if bytes(bits)!=REFERENCE_SLIM.read_bytes():
        raise RuntimeError('Arm Sotto SPI encoder differs from independently compiled codec baseline')
    phy=b''.join(frames)
    digest=hashlib.sha256(phy).hexdigest()
    if digest!=REFERENCE_GUARD_SHA:
        raise RuntimeError('Arm Cortex-M0 guarded packets not bit-identical to host and Python Flux L64')
    return dict(
        result='Cortex-M0 FULL SOTTO SPI + GUARDED PACKETIZER EMULATOR PASS',
        actual_arm_instructions_executed=True,
        source_pcm_sha256=hashlib.sha256(pcm).hexdigest(),
        codec_superframes=26,
        codec_sha256=hashlib.sha256(bits).hexdigest(),
        original_guard_frames=4,
        guard_bytes_sha256=digest,
        guard_bytes_total=len(phy),
        codec_10ms_calls=len(pcm)//160,
        codec_flush_superframes=flushes,
        linked_elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
        emulated_packetizer_max_cycles=max(cyc),
        emulated_codec_max_10ms_cycles=max(enc_cycles),
        modeled_stack_highwater_bytes=m.stack_used(),
        k6sc_deck_corruption_refused=True,
        codebook_from_independent_ARM_elf=True,
        no_hardware_access=True,unsafe_to_deploy=True,
        limitations=[
            'Actual ARMv6-M instructions, but SPI reads modeled as ordinary memory reads, not SPI1 peripheral transfer time.',
            'No PA11 GPIO/ADC interrupt or true 8kHz concurrent acquisition.',
            'No native-FSK radio TX/RX, speaker, PTT hardware watchdog or signed library slot authentication.',
            'This ELF has an emulator-only context binding and MUST NOT be flashed.',
        ])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--pcm',type=Path,default=SOURCE_PCM)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('refuse overwrite')
    result=run(a.elf,a.pcm.read_bytes())
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(result['result'],result['codec_superframes'],'superframes,',
          result['original_guard_frames'],'guarded L64 frames, NO RADIO')


if __name__=='__main__':
    main()
