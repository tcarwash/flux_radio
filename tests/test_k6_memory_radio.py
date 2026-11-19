import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import pytest
import k6_memory_radio as m
from k6_memories import builtins,channel

def test_builtin_encoding():
    for rows in builtins().values():
        for row in rows:
            b=m.encode(row);assert len(b)==32
            r=m.decode(b,0);assert m.encode(r)==b
            if row['Duplex']=='off':assert b[4:8]==bytes(4)

def test_cross_tone():
    r=channel('TEST',146.94,'-',.6);r.update(Tone='Cross',CrossMode='Tone->DTCS',rToneFreq='100',RxDtcsCode='125',DtcsPolarity='NR')
    b=m.encode(r);assert m.encode(m.decode(b,42))==b

def test_merge():
    old=bytearray(b'\xff'*m.BANK);old[:32]=m.encode(channel('TEST',146.52));old[32000:]=b'x'*768
    new,info=m.plan(bytes(old),[channel('TEST',146.52),channel('WX',162.55,'off')],'merge');assert new[:32]==old[:32] and new[32000:]==old[32000:];assert info['added']==1 and info['duplicates']==1
    with pytest.raises(ValueError):m.plan(new,[channel('X',146.52,mode='DMR')],'merge')

def test_routes(monkeypatch):
    import k6_store
    c=k6_store.create_app().test_client();c.get('/memories');c.post('/settings',data={'port':'virtual-test'})
    with c.session_transaction() as s:token=s['memory_token']
    monkeypatch.setattr(m,'operate',lambda *args:{'channels':[],'opaque_slots':[]})
    assert c.post('/memories/radio',json={'action':'read'}).status_code==403
    assert c.post('/memories/radio',json={'action':'replace','token':token}).status_code==400
    assert c.post('/memories/radio',json={'action':'read','token':token}).status_code==200

def test_interrupted_recovery(tmp_path):
    import json
    class Cable:
        def __init__(self):self.bank=bytearray(b'\xff'*m.BANK);self.cut=True
        def call(self,op,addr=0,data=b''):
            if op==0x90:return m.CAP
            if op==0x91:return bytes(self.bank[addr*64:addr*64+64])
            if op==0x92:self.sector=addr;self.stage=bytearray(4096)
            if op==0x93:self.stage[addr*64:addr*64+64]=data
            if op==0x94:
                start=addr*4096;self.bank[start:start+4096]=b'\xff'*4096
                if self.cut:
                    self.bank[start:start+128]=self.stage[:128];raise OSError('power lost')
                self.bank[start:start+4096]=self.stage
            return bytes(4)
    c=Cable();old=bytes(c.bank);target,_=m.plan(old,builtins()['NOAA Weather'],'merge');identity={'port':'sim','calibration':'test'}
    with pytest.raises(OSError):m.write_bank(c,old,target,tmp_path,identity=identity)
    path=next(tmp_path.iterdir());assert json.loads((path/'journal.json').read_text())['status']=='pending'
    with pytest.raises(ValueError):m.write_bank(c,bytes(c.bank),target,tmp_path,identity={},recovery=path)
    c.cut=False;m.write_bank(c,bytes(c.bank),target,tmp_path,identity=identity,recovery=path)
    assert bytes(c.bank)==target and json.loads((path/'journal.json').read_text())['status']=='complete'
