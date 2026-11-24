"""Actual Cortex-M0 API31 Sotto RX consumer over independently recovered IQ.

This isolates the module ABI and C FDV1 parser. The fsk4800_rx host callback
is scripted from real SoapyFang RFBackend output, not compiled Rust code;
a separate generated Rust firmware build is required for host validation.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from test_k6_sotto_e2e_mock import built
from k6_live_emu import Emu
from modem import k6mod
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import trial

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'tests/data/k6/sotto_rx31_call_emu.c'


class Api31Mock(Emu):
    def __init__(self, code, entry, names, physical, *,
                 fail_at=None, key_after=None, level=31):
        super().__init__(code,entry,np.zeros(1,np.uint8),0,1,
                         level=level,max_s=8,scratch=4096)
        old=len(self.names)
        self.names=list(names)
        for i in range(old,len(names)):
            self.mu.mem_write(self.stubs+16*i,b'\x70\x47')
        self.mu.mem_write(self.api,struct.pack('<HH',1,level)+b''.join(
            struct.pack('<I',1 if n=='mb_version' else self.stubs+16*i|1)
            for i,n in enumerate(self.names)))
        self.mu.hook_add(self.U.UC_HOOK_CODE,self.on_stub,
                         begin=self.stubs+16*old+1,
                         end=self.stubs+16*len(names))
        self.physical=list(physical)
        self.n=0
        self.armed=False
        self.stop_calls=0
        self.arm_calls=0
        self.fail_at=fail_at
        self.key_after=key_after

    def on_stub(self,uc,addr,size,data):
        a=self.A
        name=self.names[(addr-self.stubs)//16]
        if name=='key_down' and self.key_after is not None:
            uc.reg_write(a.UC_ARM_REG_R0,int(self.n>=self.key_after))
            self.tick(50*96)
            return
        if name!='fsk4800_rx':
            return super().on_stub(uc,addr,size,data)
        op=uc.reg_read(a.UC_ARM_REG_R0)
        ptr=uc.reg_read(a.UC_ARM_REG_R1)
        cfg=uc.reg_read(a.UC_ARM_REG_R2)
        ret=-1
        if op==0:
            self.arm_calls+=1
            if cfg==4 and not self.armed:
                self.armed=True;ret=0
        elif op==1 and self.armed:
            if self.fail_at is not None and self.n==self.fail_at:
                self.armed=False;ret=-2
            elif self.n<len(self.physical):
                frame=self.physical[self.n]
                assert len(frame)==66
                assert self.scr<=ptr<self.scr+self.scratch-66
                uc.mem_write(ptr,frame)
                self.n+=1;self.armed=False;ret=33
                self.tick(1250000)  # model 13.02 ms new-radio interrupt/event
            else:
                ret=0
        elif op==2:
            self.stop_calls+=1
            self.armed=False
            ret=0
        uc.reg_write(a.UC_ARM_REG_R0,ret & 0xffffffff)
        self.tick(20*96)


@pytest.fixture(scope='module')
def case(built):
    artifact,decoder=built
    result=trial(artifact,Channel(prefix_every=2),cpi=1.5,
                 adc_cycles=4800,spi_us=10,
                 capture_rf_frames=True,capture_codec=True)
    assert result['session']['complete'] and result['codec_exact']
    assert result['received']==6
    assert len(result['received_physical_frames'])==6
    code,entry=k6mod.build(str(SRC))
    assert len(code)+24<=10240
    return artifact,result['received_physical_frames'],code,entry


def boot(case, frames=None, **kwargs):
    artifact,physical,code,entry=case
    emu=Api31Mock(code,entry,artifact['names'],physical if frames is None else frames,
                  **kwargs)
    raw=emu.run()
    assert raw is not None
    val,stack=raw
    if val>=2**31:val-=2**32
    assert not emu.armed
    assert emu.stop_calls==1
    return emu,val,stack


def test_cortex_m0_strict_fdv1_session_from_real_virtual_iq(case):
    emu,ret,stack=boot(case)
    assert ret==6
    assert emu.n==6 and emu.arm_calls==6
    assert 0<stack<3504


def test_corrupted_crc_refuses_session_and_restores_rx(case):
    artifact,frames,_,_=case
    broken=list(frames)
    corrupt=bytearray(broken[2])
    corrupt[28]^=0x08
    broken[2]=bytes(corrupt)
    emu,ret,stack=boot(case,frames=broken)
    assert ret==-11 and emu.n==3
    assert emu.arm_calls==3


def test_missing_end_does_not_silently_complete(case):
    artifact,frames,_,_=case
    emu,ret,stack=boot(case,frames=frames[:-1])
    assert ret==-8 and emu.n==5
    assert emu.stop_calls==1


def test_ptt_preemption_and_service_refusal_leave_unkeyed(case):
    emu,ret,_=boot(case,key_after=2)
    assert ret==-6 and emu.n==2
    refused,ret,_=boot(case,fail_at=1)
    assert ret==-7 and refused.n==1


def test_level30_does_not_access_api31(case):
    _,frames,code,entry=case
    emu=Api31Mock(code,entry,case[0]['names'],frames,level=30)
    value,_=emu.run()
    assert value==2**32-3
    assert emu.arm_calls==emu.stop_calls==0
