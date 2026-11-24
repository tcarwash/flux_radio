"""Host-native C mailbox lifecycle tests. No radio/serial/network/hardware."""
import ctypes as C
from pathlib import Path
import subprocess
import shutil
import pytest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"c/k6/mod/k6_packet_mailbox.h"

SHIM=r'''
#include <stdint.h>
#include "k6_packet_mailbox.h"
static k6mb_bus_t BUS;
void reset(void){k6mb_init(&BUS);}
void phase(unsigned n){BUS.tnc_active = (uint8_t)n;}
int stage(uint32_t now,uint16_t id,uint16_t caps,const uint8_t *data,
          unsigned n,uint32_t ttl,uint32_t *gen){
  return k6mb_stage(&BUS,now,id,caps,data,n,ttl,gen);}
int claim(uint32_t now,uint16_t id,uint16_t caps,uint32_t gen,uint8_t *out,unsigned cap,
          unsigned *n){return k6mb_claim(&BUS,now,id,caps,gen,out,cap,n);}
int complete(uint32_t gen,int result){return k6mb_complete(&BUS,gen,result);}
int release(void){return k6mb_release(&BUS);}
int poll(uint32_t *gen,uint32_t *state,int32_t *result){
 return k6mb_poll(&BUS,gen,state,result);}
int rx_put(uint16_t id,const uint8_t *data,unsigned n,uint32_t *gen){
 return k6mb_rx_put(&BUS,id,data,n,gen);}
int rx_take(uint8_t *out,unsigned cap,uint16_t *id,unsigned *n){
 return k6mb_rx_take(&BUS,out,cap,id,n);}
void corrupt(void){BUS.tx.payload[0] ^= 1;}
unsigned rx_count(void){return BUS.rx_count;}
'''
@pytest.fixture(scope="module")
def c(tmp_path_factory):
    if not shutil.which("cc"):pytest.skip("C compiler unavailable")
    folder=tmp_path_factory.mktemp("k6-packet-mailbox")
    source=folder/"mailbox.c";source.write_text(SHIM)
    dest=folder/"mailbox.so"
    subprocess.run(["cc","-std=c11","-O2","-Wall","-Wextra","-Werror",
                    "-shared","-fPIC","-I",str(SRC.parent),str(source),
                    "-o",str(dest)],check=True)
    obj=C.CDLL(str(dest))
    obj.phase.argtypes=[C.c_uint]
    obj.stage.argtypes=[C.c_uint32,C.c_uint16,C.c_uint16,C.c_char_p,
                        C.c_uint,C.c_uint32,C.POINTER(C.c_uint32)]
    obj.stage.restype=C.c_int
    obj.claim.argtypes=[C.c_uint32,C.c_uint16,C.c_uint16,C.c_uint32,
                        C.c_char_p,C.c_uint,C.POINTER(C.c_uint)]
    obj.claim.restype=C.c_int
    obj.complete.argtypes=[C.c_uint32,C.c_int]
    obj.complete.restype=C.c_int
    obj.poll.argtypes=[C.POINTER(C.c_uint32),C.POINTER(C.c_uint32),C.POINTER(C.c_int32)]
    obj.poll.restype=C.c_int
    obj.rx_put.argtypes=[C.c_uint16,C.c_char_p,C.c_uint,C.POINTER(C.c_uint32)]
    obj.rx_put.restype=C.c_int
    obj.rx_take.argtypes=[C.c_char_p,C.c_uint,C.POINTER(C.c_uint16),C.POINTER(C.c_uint)]
    obj.rx_take.restype=C.c_int
    obj.rx_count.restype=C.c_uint
    return obj


@pytest.fixture
def bus(c):
    c.reset()
    return c


def stage(c,data=b"ABC",id=0x200,ttl=1000,now=1234):
    gen=C.c_uint32(0)
    ret=c.stage(now,id,1,data,len(data),ttl,C.byref(gen))
    return ret,gen.value


def claim(c,gen,id=0x200,caps=1,now=1334):
    buf=C.create_string_buffer(128)
    n=C.c_uint(0)
    rc=c.claim(now,id,caps,gen,buf,len(buf),C.byref(n))
    return rc,bytes(buf[:n.value])


def status(c):
    gen=C.c_uint32(0);state=C.c_uint32(0);res=C.c_int32(0)
    assert c.poll(C.byref(gen),C.byref(state),C.byref(res))==0
    return gen.value,state.value,res.value


def test_exact_bytes_survive_yield_and_cannot_replay(bus):
    bus.phase(1)
    payload=b"\x00\xdb\xc0\xff"*30
    rc,gen=stage(bus,payload)
    assert rc==0 and gen
    bus.phase(0)
    assert claim(bus,gen)==(0,payload)
    assert claim(bus,gen)[0]<0
    assert bus.complete(gen,0)==0
    assert status(bus)==(gen,3,0)
    assert bus.release()==0
    assert status(bus)==(0,0,0)
    assert claim(bus,gen)[0]<0


def test_reject_nested_claim_while_tnc_active(bus):
    bus.phase(1)
    rc,gen=stage(bus)
    assert rc==0
    assert claim(bus,gen)[0]==-9
    assert status(bus)[1]==1
    bus.phase(0)
    assert claim(bus,gen)==(0,b"ABC")


def test_stage_rejects_without_tnc_ownership(bus):
    rc,_=stage(bus)
    assert rc==-9
    assert status(bus)[1]==0


def test_stage_refuses_busy_without_overwrite(bus):
    bus.phase(1)
    assert stage(bus,b"first")[0]==0
    assert stage(bus,b"second")[0]==-3
    bus.phase(0)
    assert claim(bus,status(bus)[0])==(0,b"first")


@pytest.mark.parametrize("data,expected",[(b"",-1),(bytes(129),-2)])
def test_invalid_packet_size_never_claimable(bus,data,expected):
    bus.phase(1)
    assert stage(bus,data)[0]==expected
    assert status(bus)[1]==0


def test_expiry_stops_transmission_and_fails_session(bus):
    bus.phase(1)
    rc,gen=stage(bus,ttl=20,now=100)
    assert rc==0
    bus.phase(0)
    assert claim(bus,gen,now=122)[0]==-6
    assert status(bus)[1]==4
    assert bus.release()==0


def test_crc_corruption_fails_closed(bus):
    bus.phase(1)
    rc,gen=stage(bus)
    assert rc==0
    bus.corrupt()
    bus.phase(0)
    assert claim(bus,gen)[0]==-7
    assert status(bus)[1]==4


def test_bad_generation_invalidates_pending_transmit(bus):
    bus.phase(1)
    rc,gen=stage(bus)
    assert rc==0
    bus.phase(0)
    assert claim(bus,gen+1)[0]==-8
    assert status(bus)[1]==4
    assert claim(bus,gen)[0]<0


@pytest.mark.parametrize("id,caps",[(0x100,4),(0x200,0),(0x400,1)])
def test_voice_only_unknown_or_wrong_identity_cannot_key(bus,id,caps):
    bus.phase(1)
    rc,gen=stage(bus,id=0x200)
    assert rc==0
    bus.phase(0)
    ret,_=claim(bus,gen,id,caps)
    assert ret < 0
    assert status(bus)[1]==4


def test_receive_ring_is_bounded_and_fifo(bus):
    for i in range(4):
        payload=bytes([i])+b"DATA"
        gen=C.c_uint32()
        assert bus.rx_put(0x200,payload,len(payload),C.byref(gen))==0
    assert bus.rx_count()==4
    gen=C.c_uint32()
    assert bus.rx_put(0x200,b"excess",6,C.byref(gen))==-10
    for i in range(4):
        buf=C.create_string_buffer(128);mode=C.c_uint16(0);n=C.c_uint()
        assert bus.rx_take(buf,128,C.byref(mode),C.byref(n))==0
        assert mode.value==0x200
        assert bytes(buf[:n.value])==bytes([i])+b"DATA"
    assert bus.rx_count()==0
    buf=C.create_string_buffer(128);mode=C.c_uint16();n=C.c_uint()
    assert bus.rx_take(buf,128,C.byref(mode),C.byref(n))==-11


def test_receive_rejected_inside_foreground_tnc(bus):
    bus.phase(1)
    gen=C.c_uint32()
    assert bus.rx_put(0x200,b"abc",3,C.byref(gen))==-9
    assert bus.rx_count()==0
