"""Production C guarded DV packet queue/receiver on the Sotto firmware branch.

These functions only produce/validate in-memory bytes, NO PTT, SDR or radio.
Physical FSK-13/14 proved the 33-word guard; this is a compiler/oracle
cross-check for the next firmware's exact little-endian C ABI.
"""
from __future__ import annotations
import ctypes
from pathlib import Path
import random
import subprocess

import pytest
from modem import pager

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "c/k6/sotto_dv_wire.c"
VOICE = pager.profile("L","1200",depth=1,parity=False,short_last=True)


class Session(ctypes.Structure):
    _fields_ = [("hi",ctypes.c_uint32), ("lo",ctypes.c_uint32),
                ("kind",ctypes.c_uint8), ("first",ctypes.c_uint8),
                ("last",ctypes.c_uint8), ("frames",ctypes.c_uint8),
                ("segments",ctypes.c_uint8)]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    tmp=tmp_path_factory.mktemp("flux-sotto-wire")
    so=tmp/"sotto-wire.so"
    subprocess.run(["clang","-O2","-std=c99","-Wall","-Wextra","-Werror",
                    "-shared","-fPIC",str(SOURCE),"-o",str(so)],
                   check=True,capture_output=True)
    obj=tmp/"sotto-wire.arm.o"
    subprocess.run(["clang","--target=thumbv6m-none-eabi","-mcpu=cortex-m0",
                    "-mthumb","-mfloat-abi=soft","-Oz","-ffreestanding",
                    "-fno-builtin","-std=c99","-Wall","-Wextra","-Werror",
                    "-c",str(SOURCE),"-o",str(obj)],check=True,capture_output=True)
    sym=subprocess.run(["llvm-nm",str(obj)],check=True,capture_output=True,text=True).stdout
    for required in ("flux_dv_wire_rx66","flux_dv_wire_session_tx",
                     "flux_dv_wire_session_rx66","flux_dv_wire_push"):
        assert required in sym
    l=ctypes.CDLL(str(so))
    l.flux_dv_wire_rx66.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,
                                  ctypes.c_void_p,ctypes.c_void_p]
    l.flux_dv_wire_rx66.restype=ctypes.c_uint
    l.flux_dv_wire_session_tx.argtypes=[ctypes.POINTER(Session),ctypes.c_void_p]
    l.flux_dv_wire_session_tx.restype=ctypes.c_uint
    l.flux_dv_wire_session_rx66.argtypes=[ctypes.c_void_p,ctypes.POINTER(Session),ctypes.c_void_p]
    l.flux_dv_wire_session_rx66.restype=ctypes.c_uint
    return l


def rx(l,physical):
    raw=(ctypes.c_uint8*66).from_buffer_copy(physical)
    result=(ctypes.c_uint8*48)(*[0xa7]*48)
    msg=ctypes.c_uint8(0xe7)
    n=ctypes.c_uint(777)
    off=ctypes.c_uint(777)
    ok=l.flux_dv_wire_rx66(raw,result,ctypes.byref(msg),ctypes.byref(n),
                            ctypes.byref(off))
    return ok,bytes(result),msg.value,n.value,off.value


@pytest.mark.parametrize("n",range(1,9))
@pytest.mark.parametrize("id",[1,73,93,112,255])
def test_real_arm_build_and_native_c_crc_voice_receiver(lib,n,id):
    voice=random.Random(n*1000+id).randbytes(n*6)
    wire=VOICE.pack(voice,msg_id=id)[0]+bytes([0x5a,0xa5])
    for displacement in (0,2):
        physical=wire if displacement==0 else (bytes([0xf8,0])+wire)[:66]
        ok,frames,mid,count,offset=rx(lib,physical)
        assert ok==1 and mid==id and count==n and offset==displacement
        assert frames[:n*6]==voice and frames[n*6:]==bytes(48-n*6)


def test_bad_voice_crc_and_non_voice_never_overwrite_buffer(lib):
    pkt=bytearray(VOICE.pack(b'abcxyz'*3,msg_id=112)[0]+bytes([0x5a,0xa5]))
    pkt[7]^=0x10
    assert rx(lib,bytes(pkt))==(0,bytes([0xa7]*48),0xe7,777,777)
    assert rx(lib,bytes(66))==(0,bytes([0xa7]*48),0xe7,777,777)


def test_session_control_arm_compiled_and_native_roundtrip(lib):
    from modem import pager
    for control in (Session(0x12345678,0x90abcdef,1,112,0,0,0),
                    Session(0x12345678,0x90abcdef,2,112,115,2,4)):
        words=(ctypes.c_uint16*33)()
        assert lib.flux_dv_wire_session_tx(ctypes.byref(control),words)==33
        physical=bytes(z for w in words for z in (w&255,w>>8))
        assert physical[-2:]==bytes([0x5a,0xa5])
        plain=bytes(a^b for a,b in zip(physical[:64],pager.pn9(64)))
        assert plain[0]==0xe2 and plain[1]==1
        assert pager.crc16_ccitt(plain[:62])==int.from_bytes(plain[62:64],"big")
        for shift in (0,2):
            radio=physical if not shift else (bytes([0xf8,0])+physical)[:66]
            result=Session()
            offset=ctypes.c_uint(999)
            copied=(ctypes.c_uint8*66).from_buffer_copy(radio)
            assert lib.flux_dv_wire_session_rx66(copied,ctypes.byref(result),
                                                 ctypes.byref(offset))==1
            assert offset.value==shift
            assert (result.hi,result.lo,result.kind,result.first,result.last,
                    result.frames,result.segments)==(control.hi,control.lo,
                    control.kind,control.first,control.last,control.frames,
                    control.segments)


def test_library_gate_only_for_explicit_sotto_profile():
    build=(ROOT/"tools/k6_build.sh").read_text()
    assert 'K6_DV_WIRE' in build and 'sotto_dv_wire.c' in build
    target=(ROOT/"firmware/k6-aura/build-sotto.sh").read_text()
    assert "K6_CODEC=1200" in target
    assert "K6_SOTTO_SERVICE=1" in target
    assert "K6_LEAN_TXDOG=1" in target
