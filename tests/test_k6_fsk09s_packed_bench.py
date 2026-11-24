"""Mocked radio/SDR-only tests. No port, flash, SDR or TX access."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk09s_packed_bench import (
    preflight_source,preflight_state,select_empty_slot,score_iq,
    run_transaction,EXPECTED_BUILD
)
import k6_fsk09s_packed_bench as bench


def caps(build=EXPECTED_BUILD,api=29):
    return {"build_id":build,"module_api_max":api,"cable_only":True,
            "fsk_rx":True,"module_loader":True}

def status():
    return {"standby_idle":True,"transmitting":False,"sleeping":False,
            "squelch_open":False,"watching":0,
            "vfos":[{"hz":433500000,"mode":"FM","wide":True,"memory":False},
                    {"hz":144390000,"mode":"FM","wide":True,"memory":False}]}


def test_frozen_module_is_api3_and_unchanged():
    p=preflight_source()
    assert p["level"]==3 and p["entry"]==0 and p["code_bytes"]==1690


@pytest.mark.parametrize("bad",[
    {"build_id":"aba032cfc1d2b5e1"},{"module_api_max":25},
    {"cable_only":False},{"fsk_rx":False}
])
def test_wrong_radio_firmware_refused(bad):
    c=caps();c.update(bad)
    with pytest.raises(RuntimeError,match="unsafe firmware"):
        preflight_state(c,status())


@pytest.mark.parametrize("bad",[
    {"transmitting":True},{"standby_idle":False},{"watching":1},
    {"vfos":[{"hz":1,"mode":"FM","wide":True,"memory":False},status()["vfos"][1]]}
])
def test_nonidle_or_tuned_radio_refused(bad):
    s=status();s.update(bad)
    with pytest.raises(RuntimeError,match="unsafe radio state"):
        preflight_state(caps(),s)


def test_only_known_empty_slot30_is_eligible():
    assert select_empty_slot({0:(0,8),31:(4,10)})==30
    with pytest.raises(RuntimeError,match="occupied"):
        select_empty_slot({30:(0,8)})


def test_no_flags_never_reaches_device(monkeypatch,tmp_path):
    monkeypatch.setattr(bench,"preflight_source",lambda:pytest.fail("no preflight needed"))
    with pytest.raises(RuntimeError,match="without dummy load"):
        run_transaction("/dev/ttyUSB0",tmp_path/"test")


def test_compiled_pin_drift_blocks(monkeypatch):
    monkeypatch.setattr(bench,"_sha",lambda p:"0"*64)
    with pytest.raises(ValueError,match="frozen source altered"):
        bench.preflight_source()


def test_rf_decoder_rejects_wrong_frame_count(monkeypatch):
    truth=bench.expected_packet()
    def fake_find(iq,**kwargs):
        return {"packets":[{"raw_payload_hex":(truth+bytes.fromhex("5aa5")).hex(),
                "payload_start_s":.2,"baud_estimate":4800,
                "sync_correlation":.99,"sync_bit_errors":0}],
                "frames_found":1}
    monkeypatch.setattr(bench,"find_frames",fake_find)
    x=score_iq(np.ones(1000,dtype=np.complex64))
    assert x["result"]=="RF_PAYLOAD_FAIL" and len(x["frames"])==1


@pytest.mark.parametrize("radio_fail",[False,True])
def test_mocked_single_shot_and_verified_restoration(monkeypatch,tmp_path,radio_fail):
    """Exercises real host orchestration against in-memory doubles only."""
    tree={0:bytes([1])*100,31:bytes([2])*50}
    commands=[]
    img=b"packed-probe-verified-image"
    baseline=status()
    monkeypatch.setattr(bench,"serial_lock",lambda port:nullcontext())
    monkeypatch.setattr(bench,"_open",lambda port:nullcontext(object()))
    monkeypatch.setattr(bench,"preflight_source",lambda:{"source_ok":True,
        "code_sha256":"a"*64,"entry":0,"code_bytes":1690})
    monkeypatch.setattr(bench,"frozen_code",lambda:bytes([9])*1690)
    monkeypatch.setattr(bench.k6mod,"image",lambda *args,**kwargs:img)
    monkeypatch.setattr(bench,"directory",lambda link:{n:(n*100,len(b)) for n,b in tree.items()})
    monkeypatch.setattr(bench,"snapshot",lambda link:{
        str(n):str(len(b))+":"+str(sum(b)) for n,b in tree.items()})
    monkeypatch.setattr(bench,"wait_live",lambda port:baseline)
    monkeypatch.setattr(bench.time,"sleep",lambda seconds:None)

    class FakeLink:
        def __init__(self,*args,**kwargs):pass
        def module_capabilities(self):return caps()
        def status(self):return baseline
        def read(self,off,n):return img if off==bench.LIB_BASE+3000 else bytes(n)
        def run_module(self,slot,**kwargs):
            assert slot==30
            assert kwargs.get("allow_higher_api") is True  # API3 requires 0x100
            commands.append("run")
            if radio_fail:
                raise RuntimeError("simulated radio TX failure")
            return {"returned":True,"result":98,"reports":[]}

    class FakeCable:
        def __init__(self,*args,**kwargs):pass
        def handshake(self,*args,**kwargs):return True
        def capabilities(self):return caps()
        def library_directory(self):return tree
        def serial_watermark(self):return 3
        def send_image(self,image,lowbyte):
            assert 30 not in tree
            tree[30]=image
            commands.append("install")
            return (3,None,None,len(image))
        def library_read(self,slot,n):return tree[slot][:n]
        def remove_slot(self,slot,*args):
            commands.append("remove")
            del tree[slot]
        def end(self):pass

    class FakeSdr:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,seconds):
            return np.full(2048,128,dtype=np.uint8)

    monkeypatch.setattr(bench,"LiveLink",FakeLink)
    monkeypatch.setattr(bench,"Cable",FakeCable)
    monkeypatch.setattr(bench,"RtlCapture",FakeSdr)
    monkeypatch.setattr(bench,"summarize_raw_iq",lambda raw:{"potential_occupied_or_overloaded":False})
    monkeypatch.setattr(bench,"save_capture",lambda raw,prefix,summary:np.save(prefix.with_suffix(".npy"),raw))
    import k6_fsk_iq_edges
    monkeypatch.setattr(k6_fsk_iq_edges,"read_iq",lambda *args,**kwargs:np.ones(100,dtype=np.complex64))
    monkeypatch.setattr(bench,"score_iq",lambda iq:{"result":"RF_PAYLOAD_PASS"})
    monkeypatch.setattr(bench,"extract_module_lines",lambda _:[])
    monkeypatch.setattr(bench,"score_joint",lambda rf,raw:{
        "joint_qualification":"RF_AND_PROVISIONAL_AE"})
    # The harness takes a standalone per-job local directory with private owner.
    out=tmp_path/"probe"
    if radio_fail:
        with pytest.raises(RuntimeError,match="did not independently confirm"):
            run_transaction("/dev/ttyUSB0",out,
                dummy_load_confirmed=True,operator_lease_confirmed=True,
                queue_lease_acknowledged=True)
    else:
        result=run_transaction("/dev/ttyUSB0",out,
            dummy_load_confirmed=True,operator_lease_confirmed=True,
            queue_lease_acknowledged=True)
        assert result["tx_module_requests"]==1
        assert result["original_module_restored"] is True
        assert result["original_radio_frequency_restored"] is True
    assert commands==["install","run","remove"]
    assert 30 not in tree
    assert out.with_suffix(".npy").exists()


def test_restoration_verifier_refuses_absent_baseline(tmp_path):
    with pytest.raises(RuntimeError,match="missing preflight"):
        bench.verify_restored("/dev/fake",tmp_path/"missing")


def test_restoration_verifier_uses_only_read_only_operations(monkeypatch,tmp_path):
    prefix=tmp_path/"trial"
    baseline={"build":EXPECTED_BUILD,"temp_slot":30,
      "slot_hashes":{"0":"hash0","31":"hash31"},"status":status()}
    prefix.with_name("trial-pre.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(bench,"serial_lock",lambda port:nullcontext())
    monkeypatch.setattr(bench,"_open",lambda port:nullcontext(object()))
    class FakeLink:
        def __init__(self,*args,**kwargs):pass
        def module_capabilities(self):return caps()
        def status(self):return status()
    monkeypatch.setattr(bench,"LiveLink",FakeLink)
    monkeypatch.setattr(bench,"directory",lambda link:{0:(0,3),31:(50,4)})
    monkeypatch.setattr(bench,"snapshot",lambda link:{"0":"hash0","31":"hash31"})
    out=bench.verify_restored("/dev/fake",prefix)
    assert out["verified"] and out["slot30_free"]
    monkeypatch.setattr(bench,"directory",lambda link:{0:(0,3),30:(10,2),31:(50,4)})
    with pytest.raises(RuntimeError,match="remains occupied"):
        bench.verify_restored("/dev/fake",prefix)

def test_live_firmware_requires_explicit_level3_override():
    root=ROOT/"firmware/k6-aura/apply-live-serial.py"
    source=root.read_text()
    assert "(higher || info.level <= 2)" in source
