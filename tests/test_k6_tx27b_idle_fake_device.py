"""One-shot RX-only hardware supervisor injection tests, NO real radio/serial."""
from __future__ import annotations
import contextlib
import json
from pathlib import Path
import sys
import types
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import k6_tx27b_idle_bench as bench


class FakeSerials:
    def __init__(self):self.counter=100
    def advance_after(self,n):self.counter=max(self.counter,n)
    def next(self):self.counter+=1;return self.counter


class FakeRadio:
    def __init__(self,*,scenario='clean'):
        self.scenario=scenario
        self.slots={0:b'a'*400,1:b'b'*400,31:b'c'*400}
        self.mutations=[]
        self.started=0
        self.module_result={'returned':True,'result':27,'reports':[
            'tx27b 51a=9037 51b=9037',
            'tx27b 08=87ff 52=0292',
            'tx27b 70=0000 71=1234',
            'tx27b 30a=bff1 30b=bff1']}
        self.vfos=[dict(hz=433500000,mode='FM',wide=True),
                   dict(hz=144390000,mode='FM',wide=True)]
    def info(self):return {'build_id':bytes.fromhex(bench.EXPECTED_BUILD),'protocol':5}
    def module_capabilities(self,**kw):return {'module_api_max':bench.EXPECTED_API,'cable_only':True}
    def capabilities(self):return {'features':['lib_mutate','module_caps','run']}
    def status(self):return {'transmitting':False,'standby_idle':True,'vfos':self.vfos}
    def library_directory(self):return {k:(k*16384,len(x)) for k,x in self.slots.items()}
    def library_read(self,slot,length):return self.slots[slot][:length]
    def serial_watermark(self):return 100
    def mutate_library(self,image):
        self.mutations.append(image)
        if image.startswith(b'ERASE'):
            if self.scenario=='stuck_erase':raise RuntimeError('fake erase refused')
            self.slots.pop(30,None)
        else:
            self.slots[30]=b'd'*600
            if self.scenario in ('uncertain_install','stuck_erase'):
                raise RuntimeError('injected uncertain install ACK')
        return {'verified':True}
    def run_module(self,slot,*,timeout,allow_higher_api,on_report):
        self.started+=1
        assert slot==30 and timeout<=12 and allow_higher_api
        for text in self.module_result['reports']:on_report(text)
        if self.scenario=='callback_only':
            return {'returned':True,'result':27,'reports':[]}
        return self.module_result


def configured(monkeypatch,tmp_path,radio):
    @contextlib.contextmanager
    def port(*args,**kwargs):yield object()
    @contextlib.contextmanager
    def lock(*args,**kwargs):yield
    monkeypatch.setitem(sys.modules,'k6_install',types.SimpleNamespace(
        open_port=port,Serials=FakeSerials))
    monkeypatch.setitem(sys.modules,'k6_live',types.SimpleNamespace(
        LiveLink=lambda p:radio))
    monkeypatch.setitem(sys.modules,'k6_port_lock',types.SimpleNamespace(serial_lock=lock))
    from modem import k6mod
    monkeypatch.setattr(k6mod,'image',lambda code,entry,serial,key,**kw:
        b'ERASE'+bytes([serial%256]) if kw.get('remove') else b'INSTALL'+bytes([serial%256]))
    hold=tmp_path/'bench.hold'
    monkeypatch.setattr(bench,'HOLD_SENTINEL',hold)
    monkeypatch.setattr(bench,'bench_lock',lambda: lock())
    return hold


@pytest.mark.parametrize('scenario',('clean','callback_only'))
def test_one_shot_install_run_erase_full_restore(scenario,monkeypatch,tmp_path):
    radio=FakeRadio(scenario=scenario)
    hold=configured(monkeypatch,tmp_path,radio)
    meta={'issue':40};prefix=tmp_path/'session'
    result=bench.execute(prefix,meta,b'code',0)
    saved=json.loads((prefix/'result.json').read_text())
    assert result==0 and saved['verdict']=='PASS_RX_ONLY'
    assert saved['diagnostic']['reg51_hex']=='9037'
    assert saved['diagnostic']['reg51_nonzero']
    assert radio.started==1
    assert len(radio.mutations)==2
    assert all(not c.startswith(b'RADIO') for c in radio.mutations)
    assert 30 not in radio.slots and not hold.exists()
    assert set(radio.slots)=={0,1,31}
    with pytest.raises(bench.Refused):bench.execute(prefix,meta,b'code',0)


def test_uncertain_install_ack_cleans_without_restarting_the_probe(monkeypatch,tmp_path):
    radio=FakeRadio(scenario='uncertain_install')
    configured(monkeypatch,tmp_path,radio)
    prefix=tmp_path/'uncertain'
    assert bench.execute(prefix,{'issue':40},b'code',0)==2
    d=json.loads((prefix/'result.json').read_text())
    assert d['verdict']=='BLOCKED_RESTORED'
    assert radio.started==0
    assert len(radio.mutations)==2
    assert 30 not in radio.slots
    assert d['failure_cleanup']['verified']


def test_failed_restore_creates_persistent_hold_and_forbids_future_bench(monkeypatch,tmp_path):
    radio=FakeRadio(scenario='stuck_erase')
    hold=configured(monkeypatch,tmp_path,radio)
    prefix=tmp_path/'stuck'
    assert bench.execute(prefix,{'issue':40},b'code',0)==2
    d=json.loads((prefix/'result.json').read_text())
    assert d['verdict']=='HOLD_UNVERIFIED'
    assert hold.exists() and 30 in radio.slots
    assert radio.started==0
    assert len(radio.mutations)==2
    # Exercise real lock against a held sentinel path, not fake monkeypatch.
    monkeypatch.undo()
    actual=tmp_path/'bench.lock';(tmp_path/'bench.lock.hold').write_text('HOLD')
    with pytest.raises(bench.Refused,match='HOLD'):
        with bench.bench_lock(actual):pass


def test_missing_live_enrollment_never_mutates_or_leaves_hold(monkeypatch,tmp_path):
    radio=FakeRadio()
    radio.info=lambda:{'build_id':bytes.fromhex('0000000000000000'),'protocol':5}
    hold=configured(monkeypatch,tmp_path,radio)
    assert bench.execute(tmp_path/'invalid',{'issue':40},b'code',0)==2
    report=json.loads((tmp_path/'invalid'/'result.json').read_text())
    assert report['verdict']=='BLOCKED_NO_MUTATION'
    assert not radio.mutations and radio.started==0 and not hold.exists()
