"""Actual C compile and functional validation of the bounded live packet queue.

No radio, no SPI/serial/PTT, no firmware flashing. Expected wire bytes
are constructed using the independent Flux modem.pager production path.
"""
from __future__ import annotations
import ctypes
from pathlib import Path
import random
import subprocess

import pytest
from modem import pager

ROOT=Path(__file__).resolve().parents[1]
HEAD=ROOT/'c/k6/mod'
PRIVATE_K6_SOTTO=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit')


@pytest.fixture(scope='module')
def queue(tmp_path_factory):
    temp=tmp_path_factory.mktemp('dvstream')
    source=temp/'stream_abi.c'
    source.write_text('''
#include <stdint.h>
#include "dv_guard33_stream.h"
static dv_guard33_stream_t stream;
int start(unsigned msg_id) { return dv_stream_begin(&stream,msg_id); }
int push(const uint8_t *frame) { return dv_stream_push(&stream,frame); }
int finish(void) { return dv_stream_finish(&stream); }
int peek(uint16_t *words) {
  const uint16_t *p=dv_stream_peek(&stream);
  unsigned i; if (!p) return 0;
  for (i=0; i<33; ++i) words[i]=p[i];
  return 33;
}
int ack(void) { return dv_stream_ack(&stream); }
unsigned allocated(void) { return sizeof(stream); }
unsigned count(void) { return stream.filled; }
unsigned partial(void) { return stream.frames; }
unsigned next_id(void) { return stream.next_msg_id; }
unsigned committed(void) { return stream.segments; }
''')
    so=temp/'queue.so'
    cc=['clang','-O2','-std=c11','-Wall','-Wextra','-Werror','-I'+str(HEAD)]
    subprocess.run(cc+['-shared','-fPIC',str(source),'-o',str(so)],
                   capture_output=True,check=True)
    cortex=temp/'queue_cm0.o'
    subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0',
                    '-mthumb','-mfloat-abi=soft','-Oz','-ffreestanding',
                    '-fno-builtin','-std=c11','-Wall','-Wextra','-Werror',
                    '-I'+str(HEAD),'-c',str(source),'-o',str(cortex)],
                   capture_output=True,check=True)
    assert cortex.stat().st_size>0
    lib=ctypes.CDLL(str(so))
    lib.start.argtypes=[ctypes.c_uint]
    lib.push.argtypes=[ctypes.POINTER(ctypes.c_uint8)]
    lib.peek.argtypes=[ctypes.POINTER(ctypes.c_uint16)]
    for name in ('start','push','finish','peek','ack'):
        getattr(lib,name).restype=ctypes.c_int
    for name in ('allocated','count','partial','next_id','committed'):
        getattr(lib,name).restype=ctypes.c_uint
    return lib


def send(queue,frame:bytes):
    assert len(frame)==6
    f=(ctypes.c_uint8*6).from_buffer_copy(frame)
    return queue.push(f)


def front(queue):
    w=(ctypes.c_uint16*33)()
    if queue.peek(w)!=33:return None
    return b''.join(x.to_bytes(2,'little') for x in w)


def wire(voice:bytes,id:int):
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    return p.pack(voice,msg_id=id)[0]+b'\x5a\xa5'


def test_state_size_under_192_bytes(queue):
    assert queue.allocated()<=192


def test_26_real_k6_mic_compressed_frames_packet_queue_matches_python(queue):
    if not PRIVATE_K6_SOTTO.is_file():
        pytest.skip('private K6 mic Sotto clip unavailable')
    bits=PRIVATE_K6_SOTTO.read_bytes()
    assert len(bits)==156
    assert queue.start(112)==0
    for segment in range(4):
        chunk=bits[segment*48:segment*48+48]
        n=len(chunk)//6
        for frame in range(n):
            if queue.count()==2 and queue.partial()==7:
                # Drain one completed frame before accepting the eighth.
                assert front(queue) is not None
                assert queue.ack()==0
            result=send(queue,chunk[frame*6:frame*6+6])
            assert result==(1 if frame==7 else 0)
        if segment==0:assert queue.count()==1
        if segment==1:assert queue.count()==2
    # Two completed packets are still queued. Finishing cannot silently
    # displace either one, and must wait for a confirmed radio drain.
    assert queue.finish()==-2
    assert queue.partial()==2
    assert front(queue)==wire(bits[48:96],113)
    assert queue.ack()==0
    assert queue.finish()==0
    assert queue.committed()==4
    assert queue.next_id()==116
    # We drained segments zero and one. Segments two and three remain.
    expected=[wire(bits[96:144],114),wire(bits[144:],115)]
    for w in expected:
        assert front(queue)==w
        assert queue.ack()==0
    assert front(queue) is None and queue.count()==0
    assert send(queue,b'123456')==-4


def test_full_queue_refuses_eighth_without_consuming_it(queue):
    assert queue.start(73)==0
    seg=[bytes([i]*6) for i in range(25)]
    for f in seg[:16]:send(queue,f)
    assert queue.count()==2 and queue.partial()==0
    for f in seg[16:23]:assert send(queue,f)==0
    assert queue.partial()==7
    assert send(queue,seg[23])==-2
    assert queue.partial()==7
    first=front(queue)
    assert first==wire(b''.join(seg[:8]),73)
    assert queue.ack()==0
    assert send(queue,seg[23])==1
    assert queue.partial()==0
    assert queue.count()==2
    assert front(queue)==wire(b''.join(seg[8:16]),74)
    assert queue.ack()==0
    assert front(queue)==wire(b''.join(seg[16:24]),75)
    assert queue.ack()==0
    assert queue.finish()==0
    assert queue.next_id()==76
    assert queue.committed()==3


def test_final_partial_segment_is_not_lost_when_queue_full(queue):
    assert queue.start(100)==0
    for i in range(16):
        send(queue,bytes([i]*6))
    assert queue.count()==2
    f=b'abcdef'
    assert send(queue,f)==0
    assert queue.finish()==-2
    assert queue.partial()==1
    assert queue.ack()==0
    assert queue.finish()==0
    assert queue.next_id()==103
    assert queue.count()==2
    assert front(queue)==wire(b'\x08'*6+b'\x09'*6+b'\x0a'*6+b'\x0b'*6+
                              b'\x0c'*6+b'\x0d'*6+b'\x0e'*6+b'\x0f'*6,101)
    assert queue.ack()==0
    assert front(queue)==wire(f,102)
    assert queue.ack()==0


def test_message_id_never_wraps_even_for_full_final_segment(queue):
    assert queue.start(255)==0
    frames=[bytes([i]*6) for i in range(8)]
    for i,f in enumerate(frames):
        assert send(queue,f)==(1 if i==7 else 0)
    assert queue.next_id()==256
    assert front(queue)==wire(b''.join(frames),255)
    assert queue.ack()==0
    assert send(queue,frames[0])==-3
    assert queue.finish()==0
    assert queue.start(0)==-1
    assert queue.start(256)==-1


def test_empty_end_and_errors(queue):
    assert queue.start(1)==0
    assert queue.finish()==0
    assert queue.count()==0
    assert queue.committed()==0
    assert queue.finish()==-4
    assert queue.ack()==-1


@pytest.mark.parametrize('seed',[0,4,17])
def test_random_nonprivate_voice_is_byte_exact(queue,seed):
    r=random.Random(seed)
    assert queue.start(112)==0
    chunks=[]
    for i in range(3):
        v=r.randbytes(48)
        chunks.append(v)
        for j in range(0,48,6):send(queue,v[j:j+6])
        if queue.count()==2:
            assert front(queue)==wire(chunks[i-1],112+i-1)
            assert queue.ack()==0
    assert queue.finish()==0
    # The first two packets were already consumed in the loop.
    assert front(queue)==wire(chunks[-1],114)
    assert queue.ack()==0
    assert front(queue) is None
