"""Exact C raw-group module, codec-independent RF packet tests, no hardware."""
from pathlib import Path
import ctypes as C
import hashlib
import subprocess
import numpy as np
import pytest
from tools.k6_gp0_popcount_air import parse_physical,parse_framed_bytes,assemble,PACKETS,N_SAMPLES,WIRE_BYTES
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def host(tmp_path_factory):
    tmp=tmp_path_factory.mktemp('raw-popcount-c-module')
    so=tmp/'libraw.so'
    subprocess.run(['cc','-std=c99','-O2','-Wall','-Wextra','-Werror','-fPIC','-shared',
        '-I'+str(ROOT/'c/k6'),'-I'+str(ROOT/'c/k6/mod'),
        str(ROOT/'c/k6/mod/gp0_popcount_air_tx.c'),str(ROOT/'tests/data/k6/gp0_popcount_air_host.c'),
        '-o',str(so)],check=True,capture_output=True,text=True)
    lib=C.CDLL(str(so))
    lib.gpraw_host_run.argtypes=[C.c_int];lib.gpraw_host_run.restype=C.c_int
    lib.gpraw_host_faults.restype=C.c_int
    lib.gpraw_host_packet_count.restype=C.c_uint
    lib.gpraw_host_elapsed_us.restype=C.c_uint32
    lib.gpraw_host_packet.argtypes=[C.c_uint];lib.gpraw_host_packet.restype=C.c_void_p
    return lib


def frames(lib):
    return [C.string_at(lib.gpraw_host_packet(i),WIRE_BYTES) for i in range(lib.gpraw_host_packet_count())]


def test_full_raw_group_dump_is_bit_exact_and_bounded(host):
    assert host.gpraw_host_run(0)==PACKETS
    assert host.gpraw_host_faults()==0
    assert host.gpraw_host_packet_count()==PACKETS
    assert 11_000_000<host.gpraw_host_elapsed_us()<24_000_000
    got=frames(host)
    assert all(p[-2:]==b'\x5a\xa5' for p in got)
    parsed=parse_framed_bytes(b''.join(got))
    assert all(p is not None for p in parsed)
    assert [p['seq'] for p in parsed]==list(range(PACKETS))
    meta,raw=assemble(parsed)
    assert len(raw)==N_SAMPLES==4096
    assert raw==bytes(i%65 for i in range(N_SAMPLES))
    assert meta['pdm_bits_per_group']==64
    assert 40<meta['capture_wall_duration_ms']<90
    assert meta['raw_popcount_sha256']==hashlib.sha256(raw).hexdigest()
    assert 60_000<meta['measured_gp0_groups_per_wall_sec']<130_000

@pytest.mark.parametrize('mode,expected',[(2,-13),(3,-11),(4,-4),(5,-6),
                                            (6,-21),(7,-22),(8,-17)])
def test_failure_never_claims_complete_or_leaves_pa_enabled(host,mode,expected):
    assert host.gpraw_host_run(mode)==expected
    assert host.gpraw_host_faults()==0
    assert host.gpraw_host_packet_count()<PACKETS


def test_packet_rejects_crc_bad_group_count_and_mixed_sessions(host):
    assert host.gpraw_host_run(0)==PACKETS
    got=frames(host)
    broken=bytearray(got[0]);broken[18]^=0x19
    assert parse_physical(bytes(broken)) is None
    parsed=parse_framed_bytes(b''.join(got))
    with pytest.raises(ValueError,match='missing'):assemble(parsed[:-1])
    with pytest.raises(ValueError,match='66-byte aligned'):parse_framed_bytes(got[0]+b'x')
    t=dict(parsed[3],token=parsed[3]['token']^1)
    with pytest.raises(ValueError,match='inconsistent'):assemble(parsed[:3]+[t]+parsed[4:])
    b=dict(parsed[2],payload=b'\x01'*len(parsed[2]['payload']))
    with pytest.raises(ValueError,match='conflicting'):assemble(parsed+[b])


def test_arm_module_compiles_while_preserving_arena_budget():
    from modem import k6mod
    code,entry=k6mod.build(str(ROOT/'c/k6/mod/gp0_popcount_air_tx.c'),cflags=k6mod.CFLAGS)
    assert k6mod.module_name(code)=='GP0 RAW AIR' and entry==k6mod.bin_entry(code)
    assert len(code)<4000
    assert 14976-1040-24-len(code)>=644+4096+66
