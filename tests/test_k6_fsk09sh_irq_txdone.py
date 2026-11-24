"""Actual compiled M0 module against a B-chip IRQ TX_DONE completion hypothesis.

Imports independent historic model test without modifying its old worktree.
This is a behavioral model, NOT claim of silicon IRQ semantics.
"""
from pathlib import Path
import sys
import pytest

EMUBASE=Path('/storage5/dev/flux-fsk-bchip-irq-qual-20261123')
sys.path[:0]=[str(EMUBASE/'tests'),str(EMUBASE/'tools'),str(EMUBASE)]
from test_k6_fsk_bchip_ae_emulator import BChipIRQModel
from test_k6_fsklab import TX_REGS
from test_k6_fsk_bchip_ae_emulator import emulate
import test_k6_fsk_bchip_ae_emulator as emu

SRC=Path(__file__).resolve().parents[1]/'c/k6/mod/fsk_bchip_ae_qual_armed.c'

@pytest.fixture(autouse=True)
def real_target_source(monkeypatch):
    monkeypatch.setattr(emu,'SRC',SRC)


def test_bchip_irq_only_after_completed_frame(monkeypatch):
    orig=BChipIRQModel.read
    def real_b_irq(self,reg,us):
        if reg==0x0c:
            return 0  # explicitly model observed B-chip REG0C without bit0
        value=orig(self,reg,us)
        if reg==0x02 and self.frame_start is not None and us-self.frame_start>=213000:
            return (value or 0)|0x8000
        return value
    monkeypatch.setattr(BChipIRQModel,'read',real_b_irq)
    ret,lines,after,writes,tx,model,before=emulate(done=False)
    assert ret==98,(ret,lines)
    assert len(model.frames)==2
    assert not model.unsafe_mid_tx_fifo_writes
    assert tx['log'][-1][0]=='tx_off'
    assert all(after[x]==before[x] for x in (*TX_REGS,0x5e))
    # Both traces should report done near the 220ms minimum dwell.
    assert len([s for s in lines if s.startswith('AE')])==6


def test_no_irq_no_reg0c_completion_fails_closed(monkeypatch):
    orig=BChipIRQModel.read
    def no_done(self,reg,us):
        if reg==0x0c:return 0
        val=orig(self,reg,us)
        return (val or 0)&~0x8000 if reg==0x02 else val
    monkeypatch.setattr(BChipIRQModel,'read',no_done)
    ret,lines,after,writes,tx,model,before=emulate(done=False)
    assert ret==-14 and len(model.frames)==1
    assert tx['log'][-1][0]=='tx_off'
    assert all(after[x]==before[x] for x in (*TX_REGS,0x5e))
    assert any(s.startswith('AE0') for s in lines)


def test_early_false_txdone_never_exits_before_guard(monkeypatch):
    orig=BChipIRQModel.read
    def early(self,reg,us):
        if reg==0x0c:return 0
        val=orig(self,reg,us)
        if reg==0x02 and self.frame_start is not None and us-self.frame_start>=20000:
            return (val or 0)|0x8000
        return val
    monkeypatch.setattr(BChipIRQModel,'read',early)
    ret,lines,after,writes,tx,model,before=emulate(done=False)
    assert ret==98
    assert len(model.frames)==2
    assert tx['log'][-1][0]=='tx_off'
    assert all(after[x]==before[x] for x in (*TX_REGS,0x5e))
    for line in lines:
        if ' done=' in line:
            value=int(line.split(' done=')[1][:4],16)
            assert value>=220
