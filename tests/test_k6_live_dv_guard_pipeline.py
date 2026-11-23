"""Hardware-free C scheduler / genuine Sotto slim integration qualification.

Compiles C host with actual upstream slim_encode10 and the same packet
queue header that compiles for ARM Cortex-M0.  Never transmits or uses
K6 USB. All microphone audio/generated packet bytes live in temporary
private files; no raw PCM, voice bits or payload is committed.
"""
from __future__ import annotations
import hashlib
import math
import os
from pathlib import Path
import subprocess
import struct

import pytest
from modem import pager

ROOT=Path(__file__).resolve().parents[1]
SOTTO=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
SOURCE_PCM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw')
REF_SLIM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit')
C_UNIT=ROOT/'tests/k6_live_dv_guard_unit.c'
C_WRAPPER=ROOT/'tools/k6_live_dv_guarded_host.c'
ARM=ROOT/'c/k6/mod/dv_live_guard_m0_probe.c'
PUBLIC_HEADER=ROOT/'c/k6/mod/dv_live_guard.h'


def compile_code(args):
    return subprocess.run(args,cwd=ROOT,check=True,capture_output=True,text=True,timeout=60)


@pytest.fixture(scope='module')
def host_tools(tmp_path_factory):
    dest=tmp_path_factory.mktemp('guarded-scheduler-build')
    host=dest/'guarded_host'
    unit=dest/'unit'
    compile_code(['clang','-std=gnu11','-O2','-Wall','-Wextra','-Werror',
                  '-DSOTTO_V36','-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
                  '-I',str(SOTTO),
                  str(C_WRAPPER),str(SOTTO/'sotto_fx_slim.c'),
                  str(SOTTO/'fx_tables.c'),'-lm','-o',str(host)])
    compile_code(['clang','-std=c11','-O2','-Wall','-Wextra','-Werror',
                  str(C_UNIT),'-o',str(unit)])
    return host,unit


def run_fixture(host:Path,pcm:bytes,tmp_path:Path):
    tmp_path.chmod(0o700)
    source=tmp_path/'source.raw'
    source.write_bytes(pcm)
    source.chmod(0o600)
    packetfile=tmp_path/'guarded.bin'
    compressed=tmp_path/'slim.bit'
    timeline=tmp_path/'timeline.csv'
    x=compile_code([str(host),str(source),str(packetfile),str(compressed),str(timeline)])
    assert 'guarded live scheduler' in x.stderr
    assert all((f.stat().st_mode & 0o777)==0o600
               for f in (packetfile,compressed,timeline))
    return packetfile.read_bytes(),compressed.read_bytes(),timeline.read_text()


def verify_packets(physical:bytes,codec:bytes,first_id=112):
    assert len(codec)>0 and len(codec)%6==0
    segments=(len(codec)+47)//48
    assert len(physical)==segments*66
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    rebuilt=b''
    for i in range(segments):
        envelope=physical[i*66:(i+1)*66]
        assert envelope[-2:]==b'\x5a\xa5'
        actual=envelope[:64]
        expected=p.pack(codec[i*48:(i+1)*48],msg_id=first_id+i)
        assert len(expected)==1
        assert actual==expected[0]   # includes CRC, padding and PN9
        hdr=p.unpack(actual)
        assert hdr and hdr['msg_id']==first_id+i and hdr['seq']==0
        assert hdr['depth']==1 and hdr['total']==min(8,(len(codec)//6)-8*i)
        for f in hdr['frames'][:hdr['total']]:rebuilt+=f
        for f in hdr['frames'][hdr['total']:]:assert f==bytes(6)
    assert rebuilt==codec
    return segments


def test_bounded_queue_c_portable_unit(host_tools):
    x=compile_code([str(host_tools[1])])
    assert '204 B' in x.stderr
    assert 'PASSED' in x.stderr


def test_synthetic_pcm_full_pipeline_exact_python_compatibility(host_tools,tmp_path):
    # No private mic voice in public tests. 1.16 sec of evolving sine
    # is sufficient for a full 8-frame packet and a final partial.
    samples=[int(9400*math.sin(2*math.pi*(
             260*n/8000+0.000002*n*n))) for n in range(80*116)]
    source=b''.join(struct.pack('<h',x) for x in samples)
    physical,codec,csv=run_fixture(host_tools[0],source,tmp_path)
    n=verify_packets(physical,codec)
    assert n>=2
    lines=csv.splitlines()
    assert lines[0]=='segment,message_id,frames,ready_ms,start_ms,finish_ms'
    assert len(lines)==n+1


def test_private_actual_K6_mic_recording_bit_exact_to_slim_and_python(host_tools,tmp_path):
    if not SOURCE_PCM.exists() or not REF_SLIM.exists():
        pytest.skip('private K6 mic sample absent; no synthetic substitution')
    pcm=SOURCE_PCM.read_bytes()
    physical,codec,csv=run_fixture(host_tools[0],pcm,tmp_path)
    assert codec==REF_SLIM.read_bytes()
    assert hashlib.sha256(codec).hexdigest()==(
        '8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5')
    assert len(codec)==156 and verify_packets(physical,codec)==4
    rows=csv.splitlines()
    assert len(rows)==5
    assert [r.split(',')[1:3] for r in rows[1:]]==[
        ['112','8'],['113','8'],['114','8'],['115','2']]
    assert [int(r.split(',')[3]) for r in rows[1:]]==[370,690,1010,1040]
    assert [int(r.split(',')[4]) for r in rows[1:]]==[370,690,1010,1162]


def test_refuse_overwriting_private_voice_outputs(host_tools,tmp_path):
    pcm=struct.pack('<h',100)*80*10
    run_fixture(host_tools[0],pcm,tmp_path)
    p=tmp_path/'source.raw'
    cmd=subprocess.run([str(host_tools[0]),str(p),
             str(tmp_path/'guarded.bin'),str(tmp_path/'slim.bit'),
             str(tmp_path/'timeline.csv')],capture_output=True,text=True)
    assert cmd.returncode!=0 and 'refuse overwrite' in cmd.stderr.lower()


def test_actual_cortex_m0_armv6m_build_code_and_bss(tmp_path):
    obj=tmp_path/'guarded-arm.o'
    compile_code(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0',
                  '-mthumb','-mfloat-abi=soft','-Os','-ffreestanding',
                  '-fno-builtin','-nostdlib','-fno-stack-protector',
                  '-Wall','-Wextra','-Werror','-c',str(ARM),'-o',str(obj)])
    output=subprocess.run(['llvm-size',str(obj)],check=True,
                          capture_output=True,text=True).stdout.strip().splitlines()
    sizes=[int(t) for t in output[1].split()[:3]]
    assert 0<sizes[0]<=1500    # real ARMv6-M scheduler text
    assert sizes[1]==0
    assert sizes[2]==0         # NO .bss: caller owns 204-byte state in K6 scratch RAM
    assert b'SOTTO' not in obj.read_bytes()[:64]
    assert 'SOTTO_SLIM' in (SOTTO/'sotto_slim.h').read_text()
