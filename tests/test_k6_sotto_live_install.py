"""Read-only preflight and exact resource/ordinary module provisioning."""
import pytest
from modem import k6mod
from test_k6_sotto_module import artifacts
from tools.k6_sotto_module import build
from k6_sotto_live_install import provision,check_codebook
from k6_install import InstallError

class Link:
    def __init__(self,cb,objects=None):
        self.cb=cb; self.objects=dict(objects or {}); self.writes=[]; self.serial=100
    def module_capabilities(self):return {"module_api_max":27,"cable_only":True}
    def capabilities(self):return {"features":["lib_10k","resource_deck","lib_mutate"]}
    def info(self):return {"protocol":5}
    def status(self):return {"standby_idle":True}
    def library_limit(self,_):return 10240
    def read(self,addr,n):assert (addr,n)==(0x100000,30752);return self.cb
    def library_directory(self):return {k:(0,len(v)) for k,v in self.objects.items()}
    def library_read(self,slot,n):return self.objects[slot][:n]
    def serial_watermark(self):return self.serial
    def mutate_library(self,img):
        slot=img[15]
        assert slot not in self.objects
        self.objects[slot]=img; self.writes.append(slot)
        self.serial=int.from_bytes(img[8:12],"little")
        return {"verified":True}

def test_provision_preserves_unrelated_objects(artifacts):
    _,_,cb,deck_env,_=artifacts
    code,_=build(host_resources=True)
    deck=deck_env[16:-8]
    old=k6mod.image(b"ordinary help deck",0,1,None,level=1,lib=14,cards=True)
    link=Link(cb,{14:old,16:b"opaque"})
    assert check_codebook(cb)
    plan=provision(link,code,deck)
    assert (plan["deck_slot"],plan["module_slot"])==(15,17)
    assert not link.writes and not plan["installed"]
    plan=provision(link,code,deck,install=True)
    assert plan["installed"] and link.writes==[15,17]
    assert link.objects[14]==old and link.objects[16]==b"opaque"
    again=provision(link,code,deck,install=True)
    assert again["module_reused"] and again["deck_reused"] and link.writes==[15,17]

def test_refuse_wrong_codebook_or_occupied_slot(artifacts):
    code,_,cb,deck_env,_=artifacts
    deck=deck_env[16:-8]
    link=Link(cb[:-1]+bytes([cb[-1]^1]))
    with pytest.raises(InstallError,match="K6CB"):provision(link,code,deck,install=True)
    assert not link.writes
    link=Link(cb,{14:b"protected"})
    with pytest.raises(InstallError,match="No compatible free slot"):
        provision(link,code,deck,deck_slot=14,install=True)
    assert not link.writes

def test_bench_tx_provision_requires_explicit_authorization(artifacts):
    from tools.k6_sotto_module import build
    code,_=build(host_resources=True,live_tx=True)
    _,_,cb,deck_env,_=artifacts
    deck=deck_env[16:-8]
    link=Link(cb)
    link.module_capabilities=lambda: {"module_api_max":29,"cable_only":True}
    plan=provision(link,code,deck)
    assert not plan["installed"] and not link.writes
    with pytest.raises(InstallError,match="accept-experimental-tx"):
        provision(link,code,deck,install=True)
    assert not link.writes
    plan=provision(link,code,deck,install=True,accept_experimental_tx=True)
    assert plan["installed"] and len(link.writes)==2
