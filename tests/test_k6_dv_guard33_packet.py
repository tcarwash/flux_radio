"""Cross-validate Cortex-M0 compatible 33-word guarded live Sotto packetizer.

All expected packet bytes are independently constructed with the production
Flux pager Python profile. Tests perform no SDR, RF, PTT or serial operations.
"""
from __future__ import annotations
import ctypes
from pathlib import Path
import random
import subprocess

import pytest
from modem import pager

ROOT=Path(__file__).resolve().parents[1]
HEADER=ROOT/'c/k6/mod/dv_guard33_packet.h'
PRIVATE_K6_AUDIO=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit')

@pytest.fixture(scope='module')
def mcu_build(tmp_path_factory):
    base=tmp_path_factory.mktemp('dvguard')
    src=base/'test_dv_guard.c'
    src.write_text(
        '#include <stdint.h>\n'
        '#include "dv_guard33_packet.h"\n'
        'unsigned encode(uint16_t *dst,const uint8_t *src,unsigned n,uint8_t id) {\n'
        'return dv_guard33_build_words(dst,src,n,id); }\n')
    cc=['clang','-O2','-std=c11','-Wall','-Wextra','-Werror',
        '-I'+str(HEADER.parent)]
    lib=base/'libdvguard.so'
    subprocess.run(cc+['-shared','-fPIC',str(src),'-o',str(lib)],check=True,capture_output=True)
    obj=base/'dvguard_cm0.o'
    cortex=['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0',
            '-mthumb','-mfloat-abi=soft','-Oz','-ffreestanding',
            '-fno-builtin','-std=c11','-Wall','-Wextra','-Werror',
            '-I'+str(HEADER.parent),'-c',str(src),'-o',str(obj)]
    subprocess.run(cortex,check=True,capture_output=True)
    assert obj.stat().st_size>0
    elf=ctypes.CDLL(str(lib))
    elf.encode.argtypes=[ctypes.POINTER(ctypes.c_uint16),
                         ctypes.POINTER(ctypes.c_uint8),
                         ctypes.c_uint,ctypes.c_uint8]
    elf.encode.restype=ctypes.c_uint
    return elf,obj

def call(lib,pcm:bytes,n:int,id:int):
    words=(ctypes.c_uint16*33)(*[0x5AA5]*33)
    inp=(ctypes.c_uint8*len(pcm)).from_buffer_copy(pcm)
    rc=lib.encode(words,inp,n,id)
    return rc,bytes(b for w in words for b in ((w&0xff),(w>>8)))

def expected_python(voice:bytes,mid:int):
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=p.pack(voice,msg_id=mid)
    assert len(packets)==1
    assert len(packets[0])==64
    return packets[0]

@pytest.mark.parametrize('n',list(range(1,9)))
@pytest.mark.parametrize('mid',[0,1,73,93,112,255])
def test_48_python_wire_equivalence_cases(mcu_build,n,mid):
    lib,_=mcu_build
    seed=n*257+mid
    r=random.Random(seed)
    voice=r.randbytes(6*n)
    rc,physical=call(lib,voice,n,mid)
    original=expected_python(voice,mid)
    assert rc==33
    assert physical==original+b'\x5a\xa5'
    decoded=pager.profile('L','1200',depth=1,short_last=True).unpack(physical[:64])
    assert decoded is not None
    assert decoded['msg_id']==mid and decoded['seq']==0 and decoded['total']==n
    assert decoded['depth']==1 and decoded['short'] is False
    assert b''.join(decoded['frames'][:n])==voice
    assert b''.join(decoded['frames'][n:])==bytes(6*(8-n))

def test_reject_bad_count_without_touching_output(mcu_build):
    lib,_=mcu_build
    for count in (0,9,255,256):
        rc,physical=call(lib,b'\x00'*48,count,73)
        assert rc==0 and physical==(b'\xa5\x5a'*33)

def test_guard_preserves_full_l64_after_spurious_f8_word(mcu_build):
    lib,_=mcu_build
    frames=bytes(range(48))
    rc,raw=call(lib,frames,8,73)
    assert rc==33
    p=pager.profile('L','1200',depth=1,short_last=True)
    normal=raw[:66]
    shifted=(b'\xf8\x00'+raw)[:66]
    assert len(normal)==len(shifted)==66
    assert p.unpack(normal[:64]) is not None
    assert p.unpack(normal[2:66]) is None
    assert p.unpack(shifted[:64]) is None
    assert p.unpack(shifted[2:66]) is not None
    assert shifted[2:66]==normal[:64]
    # Both recover the same original Sotto L64 bytes, despite sacrificial guard.
    assert normal[:64]==shifted[2:66]==expected_python(frames,73)

@pytest.mark.skipif(not PRIVATE_K6_AUDIO.exists(),reason='private on-air K6 mic codec bytes not on this host')
def test_actual_pa11_mic_host_codec_stream_segments_equivalent(mcu_build):
    lib,_=mcu_build
    bits=PRIVATE_K6_AUDIO.read_bytes()
    assert len(bits)==156 # 26 genuine compressed Sotto-1200 40ms frames
    packet_count=(len(bits)+47)//48
    assert packet_count==4
    for i in range(packet_count):
        seg=bits[i*48:(i+1)*48]
        rc,physical=call(lib,seg,len(seg)//6,112+i)
        assert rc==33
        assert physical[:64]==expected_python(seg,112+i)
        assert physical[-2:]==b'\x5a\xa5'
        assert pager.profile('L','1200',depth=1,short_last=True).unpack(physical[:64]) is not None
