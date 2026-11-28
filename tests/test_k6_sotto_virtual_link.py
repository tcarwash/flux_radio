"""Offline FDV1 session oracle and opt-in actual Sotto development integration."""
import os
from pathlib import Path
import shutil
import pytest
from modem import pager
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import accepted,decode_session,prepare,trial,wire_decode,wire_records_session

ROOT=Path(__file__).resolve().parents[1]

def seal(p):
    p=bytes(p)+pager.crc16_ccitt(p).to_bytes(2,"big")
    return bytes(a^b for a,b in zip(p,pager.pn9(64)))+b"\x5a\xa5"

def control(kind,last=0,tail=0,count=0):
    p=bytearray(62);p[:4]=bytes([0xe2,1,kind,0]);p[4:12]=b"TESTTAG!"
    p[12:16]=bytes([112,last,tail,count]);p[16:20]=b"FDV1"
    return seal(p)

def voice(bits):
    p=bytearray(62);p[:6]=bytes([0xa9,112,0xc0,0,0,len(bits)//6]);p[6:6+len(bits)]=bits
    return seal(p)

def test_strict_session_oracle():
    bits=bytes(range(12))
    frames=[control(1),voice(bits),control(2,112,2,1)]
    assert decode_session(frames)==(bits,dict(valid=3,invalid=0,complete=True,offset_2=0))
    shifted=[b"\xf8\x00"+x[:-2] for x in frames]
    assert decode_session(shifted)==(bits,dict(valid=3,invalid=0,complete=True,offset_2=3))
    assert not decode_session(frames[:-1])[1]["complete"]
    assert not decode_session([frames[0],frames[2]])[1]["complete"]
    assert not decode_session([frames[0],frames[1],control(2,112,3,1)])[1]["complete"]
    assert decode_session([control(1,112,2,1)])[1]["valid"]==0
    assert decode_session([control(2,113,2,1)])[1]["valid"]==0
    damaged=bytearray(frames[1]);damaged[7]^=1
    assert decode_session([frames[0],damaged,frames[2]])[1]["invalid"]==1
    assert not decode_session([damaged,*frames])[1]["complete"]
    assert not decode_session([*frames,damaged])[1]["complete"]

@pytest.fixture(scope="module")
def actual(tmp_path_factory):
    tree=Path(os.environ.get("FLUX_SOTTO_TREE",ROOT))
    if not (tree/"c/k6/mod/sotto_live_tx.c").is_file():
        pytest.skip("API29 Sotto source not on this checkout; set FLUX_SOTTO_TREE")
    if not all(shutil.which(x) for x in ("clang","ld.lld","llvm-objcopy","llvm-nm","cc")):
        pytest.skip("ARM/native build tools unavailable")
    return prepare(tree,tmp_path_factory.mktemp("actual-sotto"))

def test_actual_wire_decoder(actual):
    bits=bytes(range(12));frames=[control(1),voice(bits),control(2,112,2,1)]
    frames[1]=b"\xf8\x00"+frames[1][:-2]
    records=wire_decode(actual,frames)
    assert [r[0] for r in records]==[2,1,3]
    assert [r[1] for r in records]==[0,2,0]
    assert wire_records_session(records)==(bits,True)
    assert not wire_decode(actual,[control(1,112,2,1)])[0][0]

@pytest.mark.parametrize("prefix",[0,2])
def test_actual_live_module_rf(actual,prefix):
    r=trial(actual,Channel(prefix_every=prefix),cpi=1.5,spi_us=34)
    assert accepted(r)
    assert r["module_return"]==r["codec_frames"]==26
    assert r["samples"]==8320 and not r["pcm_lost"]
    assert r["tx"]["completed"]==r["received"]==6
    assert r["tx_before_release"]>0 and r["codec_exact"]
    assert r["session"]["complete"] and r["wire_decoder_agrees"]
    assert r["session"]["offset_2"]==(3 if prefix else 0)
    assert r["rx"]["overruns"]==r["rx"]["underflows"]==0

def test_actual_cpu_contention(actual):
    good=trial(actual,cpi=1.5,spi_us=10,adc_cycles=4800)
    assert accepted(good)
    assert good["codec_exact"] and good["pcm_highwater"]<4
    bad=trial(actual,cpi=1.5,spi_us=34,adc_cycles=5745)
    assert bad["module_return"]<0 and bad["pcm_lost"] and bad["pcm_highwater"]==4
    assert not accepted(bad)
    assert not bad["codec_exact"] and not bad["tx"]["active"]

def test_strict_cli_signals_failed_model(tmp_path):
    """A completed sweep is not automatically a successful modem regression."""
    import json
    import subprocess
    import sys
    tree=Path(os.environ.get("FLUX_SOTTO_TREE",ROOT))
    if not (tree/"c/k6/mod/sotto_live_tx.c").is_file():
        pytest.skip("API29 Sotto source unavailable")
    out=tmp_path/"strict.json"
    cmd=[sys.executable,str(ROOT/"tools/k6_sotto_virtual_link.py"),
         "--sotto-tree",str(tree),"--cpi","1.5","--spi-us","34",
         "--adc-cycles","5745","--strict","--json",str(out)]
    done=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=90)
    assert done.returncode==1,done.stderr
    rows=json.loads(out.read_text())
    assert len(rows)==1 and rows[0]["accepted"] is False
    assert rows[0]["pcm_lost"]

def test_external_audio_fixture(tmp_path):
    """Non-default PCM uses the same input for compiled encoder and RF oracle."""
    import json
    import subprocess
    import sys
    import wave
    import numpy as np
    tree=Path(os.environ.get("FLUX_SOTTO_TREE",ROOT))
    if not (tree/"c/k6/mod/sotto_live_tx.c").is_file():
        pytest.skip("API29 Sotto source unavailable")
    t=np.arange(9600)/8000
    pcm=np.zeros(9600,dtype="<i2")
    pcm[1600:4000]=(9000*np.sin(2*np.pi*185*t[1600:4000])).astype("<i2")
    pcm[4000:]=(7000*np.sin(2*np.pi*315*t[4000:])).astype("<i2")
    path=tmp_path/"mixed.wav"
    with wave.open(str(path),"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(pcm.tobytes())
    out=tmp_path/"mixed.json"
    cmd=[sys.executable,str(ROOT/"tools/k6_sotto_virtual_link.py"),"--sotto-tree",str(tree),
         "--pcm-wav",str(path),"--strict","--json",str(out)]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stderr
    rows=json.loads(out.read_text())
    assert len(rows)==1
    row=rows[0]
    assert row["samples"]==row["samples_expected"]==9600
    assert row["accepted"] and row["codec_exact"] and row["wire_decoder_agrees"]
    assert row["codebook_profile"]["reads"]>0


def test_actual_resource_contract(actual):
    """Simulated deck reads require successful resource_open, as on-device."""
    from sim.k6emu.rf.virtual_link import VirtualAir, VirtualTxBackend
    from tools.k6_sotto_virtual_link import HZ, LiveTx
    air=VirtualAir(Channel())
    backend=VirtualTxBackend(air,240000,HZ)
    try:
        e=LiveTx(actual,backend)
        a=e.A
        regs=(a.UC_ARM_REG_R0,a.UC_ARM_REG_R1,a.UC_ARM_REG_R2,a.UC_ARM_REG_R3)
        def invoke(name,args):
            for reg,value in zip(regs,args):e.mu.reg_write(reg,value)
            e.on_stub(e.mu,e.stubs+16*e.names.index(name)+1,2,None)
            val=e.mu.reg_read(regs[0])
            return val-(1<<32) if val>>31 else val
        assert invoke("resource_read",(1,0,e.scr,4))==-1
        assert invoke("resource_open",(2,0,0,0))==-1
        assert invoke("resource_open",(14,0,0,0))==len(actual["deck"])
        assert invoke("resource_read",(1,0,e.scr,4))==0
        assert bytes(e.mu.mem_read(e.scr,4))==actual["deck"][:4]
    finally:backend.close()


def test_no_wrap_fdv1_control(actual):
    """FDV1 deliberately forbids wrapped voice IDs, in both C and Python."""
    p=bytearray(62)
    p[:4]=bytes((0xe2,1,2,0));p[4:12]=b"TESTTAG!"
    p[12:16]=bytes((254,1,8,4));p[16:20]=b"FDV1"
    packet=seal(p)
    assert decode_session([packet])[1]["valid"]==0
    assert wire_decode(actual,[packet])[0][0]==0

def test_resource_spi_byte_time(actual):
    """Fixed setup + transfer length is charged per successful resource read."""
    import pytest
    from sim.k6emu.rf.virtual_link import VirtualAir,VirtualTxBackend
    from tools.k6_sotto_virtual_link import HZ,LiveTx,trial
    air=VirtualAir(Channel())
    b=VirtualTxBackend(air,240000,HZ)
    try:
        e=LiveTx(actual,b,spi_us=4,spi_byte_us=.5)
        a=e.A
        regs=(a.UC_ARM_REG_R0,a.UC_ARM_REG_R1,a.UC_ARM_REG_R2,a.UC_ARM_REG_R3)
        def resource_read(n):
            for reg,val in zip(regs,(0,32,e.scr,n)):e.mu.reg_write(reg,val)
            start=e.cyc
            e.on_stub(e.mu,e.stubs+16*e.names.index('resource_read')+1,2,None)
            assert e.mu.reg_read(regs[0])==0
            return e.cyc-start
        assert resource_read(20)==(4+.5*20)*96
        assert resource_read(128)==(4+.5*128)*96
        assert e.spi_reads==2 and e.spi_read_bytes==148
        assert e.keyed_reads==0 and e.keyed_read_bytes==0
    finally:b.close()
    with pytest.raises(ValueError):trial(actual,spi_byte_us=float('nan'))
    with pytest.raises(ValueError):trial(actual,spi_byte_us=-1)
    with pytest.raises(ValueError):trial(actual,spi_byte_us=101)
