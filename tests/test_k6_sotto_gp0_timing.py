"""Firmware-owned GPT1 timing supplement: parser, memory, and failure path tests."""
from pathlib import Path
import struct
import sys
import os
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from k6_sotto_gp0_fault import decode as gpf_decode
from k6_sotto_gp0_timing import decode_timing, TIMING_SIZE
import k6_sotto_gp0_fault_gate as G

def fault(*,ret=-12,started=True,error=3,serial=1795730062):
    flags=1|2*(started)|4*(error!=0)|32*(ret<0)
    raw=b'GPF1'+struct.pack('<IiI2Ii4I',serial,ret,flags,0,0,0,error,
                            1746 if started else 0,3188 if started else 0,0)
    return gpf_decode(raw)

def gpt(*,serial=1795730062,flags=1,reads=1024,peak=10931,total=300000,
        preload_peak=57,preloads=1,cause=2,backlog=4,offset=0):
    return b'GPT1'+struct.pack('<9I',serial,flags,reads,peak,total,
                              preload_peak,preloads,cause,backlog|(offset<<8))

def test_positive_pcm_full_report():
    parsed=decode_timing(gpt(),fault())
    assert parsed['valid']
    assert parsed['resource_reads']==1024
    assert parsed['resource_peak_approx_us']==round(10931/96,2)
    assert parsed['fsk_preload_peak_approx_ms']==5.7
    assert parsed['gp0_code3_reason']=='pcm_queue_full'
    assert parsed['pcm_backlog_at_stop_blocks']==4
    assert parsed['pcm_offset_at_stop_samples']==0

@pytest.mark.parametrize('backlog',[0,1,2,3,4])
def test_queue_full_failure_reason_can_have_lower_backlog_at_stop(backlog):
    # The firmware captures cause=2 at the failing IRQ, but the foreground
    # encoder can consume a queued block before op1 shuts down the DMA.
    r=decode_timing(gpt(cause=2,backlog=backlog,offset=0),fault())
    assert r['gp0_code3_reason']=='pcm_queue_full'
    assert r['pcm_backlog_at_stop_blocks']==backlog
    assert 'not at IRQ failure' in r['pcm_backlog_qualifier']

def test_empty_pretest_pair():
    basic=gpf_decode(b'GPF1'+bytes(40))
    x=decode_timing(b'GPT1'+bytes(36),basic)
    assert not x['valid'] and x['resource_reads']==0 and x['pcm_backlog_at_stop_blocks']==0

@pytest.mark.parametrize('cause,backlog,offset,name',[
    (1,0,0,'decimator_rejected_input'),
    (2,4,0,'pcm_queue_full'),
    (3,1,80,'pcm_offset_invalid'),
    (4,1,20,'pcm_already_lost_or_other')
])
def test_specific_code3_failure(cause,backlog,offset,name):
    assert decode_timing(gpt(cause=cause,backlog=backlog,offset=offset),fault())['gp0_code3_reason']==name

def test_rejects_bad_wire_validity_and_run_mismatch():
    normal=fault()
    for bad in (gpt()[:-1],b'xxxx'+gpt()[4:],
                gpt(flags=3),gpt(serial=5),gpt(flags=0),
                gpt(reads=0),gpt(reads=0,total=0,peak=0,preloads=0,preload_peak=4),
                gpt(total=3),gpt(cause=9),gpt(cause=0),
                gpt(cause=2,backlog=3,offset=95),
                gpt(cause=2,backlog=5),
                gpt(cause=2,offset=81),
                gpt(cause=1,backlog=5),
                gpt(cause=3,backlog=1,offset=79),
                gpt()[:-4]+b'\0\0\0\x01'):
        with pytest.raises(ValueError):
            decode_timing(bad,normal)
    assert len(gpt())==TIMING_SIZE==40

def test_rejects_stale_capture_and_other_error_causes():
    with pytest.raises(ValueError):
        decode_timing(gpt(),fault(started=False,error=0))
    with pytest.raises(ValueError):
        decode_timing(gpt(),fault(error=1))
    with pytest.raises(ValueError):
        decode_timing(gpt(cause=0,backlog=0,offset=0),fault(error=3))

def test_firmware_has_bounded_ram0_and_failure_only_irq_bookkeeping():
    src=(ROOT/'firmware/k6-aura/sotto-api30-gp0-capture.rs').read_text()
    helper=(ROOT/'firmware/k6-aura/sotto-gp0-fault.rs.inc').read_text()
    assert '#[cfg(k6_sotto_gp0_timing)]' in src
    assert 'reason:u8' in src and 'failure_reason()->u8' in src
    assert '(*st).reason=1;' in src
    assert '(*st).reason=if h.wrapping_sub(t)>=4{2} else if o>=80{3}else{4};' in src
    assert '(*st).reason=0;' in src
    assert 'size_of::<Gp0Timing>()==16' in helper
    assert 'link_section=".ram0"' in helper
    assert 'resource_total_ticks.saturating_add' in helper
    assert 'gp0_timing_report' in helper
    assert 'if op==1 && !ring.is_null()' in helper

def test_reversible_overlays_preserve_both_cable_commands(tmp_path):
    from test_k6_sotto_gp0_fault_trace import fixture as simple_fixture, patch
    work,files=simple_fixture(tmp_path)
    patch(work,True)
    cps=(work/'src/cps/mod.rs').read_text()
    assert '0x75 if f.addr == 0 && f.len == 0' in cps
    assert '0x76 if f.addr == 0 && f.len == 0' in cps
    assert '#[cfg(k6_sotto_gp0_timing)]' in cps
    assert 'gp0_timing_report()' in cps
    gp0=(work/'src/hal/gp0_capture.rs').read_text()
    assert 'fault_ring_backlog' in gp0
    patch(work,False)
    assert {name:(work/name).read_text() for name in files}==files

def test_timing_flag_requires_trace_and_ram0():
    sh=(ROOT/'firmware/k6-aura/build-fd6818b.sh').read_text()
    assert 'K6_SOTTO_GP0_TIMING requires GPF1 trace, mic capture, and expanded RAM0=1' in sh
    assert 'k6_sotto_gp0_timing' in sh

def test_gate_requires_40_bytes_and_correct_gpt_signature(monkeypatch):
    from test_k6_sotto_gp0_fault_gate import fixture as basefixture, REF_LAYOUT
    field,baseline,diagnostic,module=basefixture(monkeypatch)
    timed=diagnostic[:4]+b'GPT1'+diagnostic[4:]
    profile={**REF_LAYOUT,'ram0_used':3808,'ram0_free':32}
    r=G.audit(field,baseline,timed,REF_LAYOUT,REF_LAYOUT,profile,module,timing=True)
    assert r['status']=='READY_FOR_ATTENDED_REVIEW'
    assert r['ram0_trace_bytes']==40 and r['diagnostic_ram0_free']==32 and r['timing_enabled']
    assert G.audit(field,baseline,timed,REF_LAYOUT,REF_LAYOUT,profile,module)['status']=='STOP_DO_NOT_FLASH'
    assert G.audit(field,baseline,diagnostic,REF_LAYOUT,REF_LAYOUT,profile,module,timing=True)['status']=='STOP_DO_NOT_FLASH'
    for changes in ({'ram0_used':3792},{'ram0_free':31},{'arena_base':'0x20001950'}):
        p={**profile,**changes}
        assert G.audit(field,baseline,timed,REF_LAYOUT,REF_LAYOUT,p,module,timing=True)['status']=='STOP_DO_NOT_FLASH'

def test_capture_saves_wire_evidence_before_cable_end_even_on_decode_failure(tmp_path,monkeypatch):
    import contextlib
    import json
    import types
    from k6_sotto_gp0_timing import run

    raw_fault=b'GPF1'+struct.pack('<IiI2Ii4I',1795730062,-12,0x27,
                                  0,0,0,3,1746,3188,0)
    # Replicate the hardware observation: cause=2, but only two blocks
    # remain by the time op1 stops DMA.
    raw_timing=gpt(cause=2,backlog=2,offset=20)
    saved=tmp_path/'physical.json'

    class FakeCable:
        def __init__(self,dev,printer):
            self.dev=dev
        def handshake(self,*a,**kw):return True
        def info(self):return {'protocol':5,'arena':14976}
        def call(self,code):
            assert code in (0x75,0x76)
            return raw_fault if code==0x75 else raw_timing
        def end(self):
            # Strong ordering guarantee: evidence exists BEFORE reboot.
            assert saved.is_file()
            record=json.loads(saved.read_text())
            assert record['fault_raw_hex']==raw_fault.hex()
            assert record['timing_raw_hex']==raw_timing.hex()
            assert record['decode_error'] is None
            assert record['timing']['gp0_code3_reason']=='pcm_queue_full'
    monkeypatch.setitem(sys.modules,'k6_install',types.SimpleNamespace(
        Cable=FakeCable,CABLE_HANDSHAKE=b'',open_port=lambda opts:contextlib.nullcontext(None)))
    monkeypatch.setitem(sys.modules,'k6_port_lock',types.SimpleNamespace(
        serial_lock=lambda port:contextlib.nullcontext()))
    record=run('/dev/does-not-exist',save=saved)
    assert record['timing']['pcm_backlog_at_stop_blocks']==2

def test_malformed_timing_is_saved_raw_before_reboot(tmp_path,monkeypatch):
    import contextlib
    import json
    import types
    from k6_sotto_gp0_timing import run
    raw_fault=b'GPF1'+struct.pack('<IiI2Ii4I',1795730062,-12,0x27,
                                  0,0,0,3,1746,3188,0)
    raw_timing=gpt(cause=2,backlog=6)
    saved=tmp_path/'bad-report.json'
    class FakeCable:
        def __init__(self,*args):pass
        def handshake(self,*a,**kw):return True
        def info(self):return {'protocol':5,'arena':14976}
        def call(self,code):return raw_fault if code==0x75 else raw_timing
        def end(self):
            assert saved.exists(), 'raw evidence must persist before reboot'
    monkeypatch.setitem(sys.modules,'k6_install',types.SimpleNamespace(
        Cable=FakeCable,CABLE_HANDSHAKE=b'',open_port=lambda opts:contextlib.nullcontext(None)))
    monkeypatch.setitem(sys.modules,'k6_port_lock',types.SimpleNamespace(
        serial_lock=lambda port:contextlib.nullcontext()))
    with pytest.raises(ValueError,match='exceeds four'):
        run('/dev/does-not-exist',save=saved)
    evidence=json.loads(saved.read_text())
    assert evidence['fault_raw_hex']==raw_fault.hex()
    assert evidence['timing_raw_hex']==raw_timing.hex()
    assert evidence['fault']['valid']
    assert evidence['timing'] is None
    assert evidence['decode_error'].startswith('ValueError:')
