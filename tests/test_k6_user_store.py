"""NOR fault injection against actual allocator C code, no radio required."""
import ctypes as C
import pathlib,subprocess
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=0x110000;SECTOR=4096;CAP=512*1024
READ=C.CFUNCTYPE(C.c_int,C.c_uint32,C.c_void_p,C.c_uint32)
PROG=READ
ERASE=C.CFUNCTYPE(C.c_int,C.c_uint32)
class Flash(C.Structure):_fields_=[('read',READ),('prog',PROG),('erase',ERASE)]
class Dir(C.Structure):_fields_=[('bytes',C.c_uint8*1036),('copy',C.c_uint)]
@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    p=tmp_path_factory.mktemp('ku')/'ku.so'
    subprocess.run(['cc','-shared','-fPIC','-O2',str(ROOT/'c/k6/user_store.c'),'-o',str(p)],check=True)
    l=C.CDLL(str(p))
    l.ku_put.argtypes=[C.POINTER(Dir),C.POINTER(Flash),C.c_uint,C.c_char_p,C.c_uint]
    return l
class Nor:
    def __init__(self,lib,raw=None):
        self.lib=lib;self.data=bytearray(raw if raw is not None else b'\xff'*CAP);self.dir=Dir();self.limit=None;self.ops=0
        def read(a,p,n):
            a-=BASE
            if a<0 or a+n>CAP:return -1
            C.memmove(p,bytes(self.data[a:a+n]),n);return 0
        def prog(a,p,n):
            a-=BASE;self.ops+=1
            if a<0 or a+n>CAP or a//256!=(a+n-1)//256:return -1
            b=C.string_at(p,n)
            if self.limit is not None and self.ops>=self.limit:
                if self.ops==self.limit:
                    for i,v in enumerate(b[:max(1,n//2)]):self.data[a+i]&=v
                return -1
            for i,v in enumerate(b):self.data[a+i]&=v
            return 0
        def erase(a):
            a-=BASE;self.ops+=1
            if a%SECTOR or a<0 or a+SECTOR>CAP:return -1
            if self.limit is not None and self.ops>=self.limit:return -1
            self.data[a:a+SECTOR]=b'\xff'*SECTOR;return 0
        self.flash=Flash(READ(read),PROG(prog),ERASE(erase))
    def call(self,name,*args):return getattr(self.lib,'ku_'+name)(C.byref(self.dir),C.byref(self.flash),*args)
    def put(self,id,b):return self.call('put',id,b,len(b))
    def get(self,id):
        a=C.c_uint32();n=C.c_uint();r=self.lib.ku_lookup(C.byref(self.dir),id,C.byref(a),C.byref(n));return None if r else bytes(self.data[a.value-BASE:a.value-BASE+n.value])
def test_pack(lib):
    n=Nor(lib);assert n.call('open')<0;assert n.call('format')==0
    for i,s in enumerate([1500,6000,10000]):assert n.put(i,bytes([i+1])*s)==0
    assert n.dir.bytes[16:28]==[0,1,220,5,1,2,112,23,3,3,16,39];assert n.call('remove',1)==0
    assert n.put(200,b'z'*5000)==0;assert n.dir.bytes[16+4*200]==1;assert n.call('open')==0;assert n.get(200)==b'z'*5000;assert n.get(1) is None

def test_interrupted_replace(lib):
    n=Nor(lib);assert n.call('format')==0;assert n.put(7,b'A'*3000)==0;assert n.put(8,b'B'*4500)==0
    raw=bytes(n.data);probe=Nor(lib,raw);assert probe.call('open')==0;assert probe.put(7,b'C'*6000)==0
    for cut in range(1,probe.ops+1):
        m=Nor(lib,raw);assert m.call('open')==0;m.limit=cut;m.put(7,b'C'*6000)
        recovered=Nor(lib,m.data);assert recovered.call('open')==0;assert recovered.get(7) in (b'A'*3000,b'C'*6000);assert recovered.get(8)==b'B'*4500

def test_corrupt_newest(lib):
    n=Nor(lib);assert n.call('format')==0;assert n.put(9,b'x'*700)==0;n.data[n.dir.copy*SECTOR+30]^=1
    m=Nor(lib,n.data);assert m.call('open')==0;assert m.get(9) is None

def test_full(lib):
    n=Nor(lib);assert n.call('format')==0
    for i in range(126):assert n.put(i,b'x')==0
    assert n.put(200,b'y')<0;assert n.call('remove',20)==0;assert n.put(200,b'y')==0;assert n.get(200)==b'y'

def test_offline_migrate(lib):
    import sys
    sys.path.insert(0,str(ROOT/'tools'))
    from k6_user_store_migrate import migrate
    raw=bytearray(b'\xff'*CAP);img=bytearray(1500);img[:4]=b'K6M\x06';img[4:6]=(1476).to_bytes(2,'little');img[13]=1;img[15]=9;raw[9*16384:9*16384+len(img)]=img
    packed,manifest=migrate(raw);assert manifest==[(9,1500,1)]
    n=Nor(lib,packed);assert n.call('open')==0;assert n.get(9)==img
    assert n.get(0) is None
    with pytest.raises(ValueError):migrate(packed)
    raw[0]=0
    with pytest.raises(ValueError):migrate(raw)

def test_host_compact_fragmented(lib):
    from k6_user_store_migrate import upgrade,objects,locations
    n=Nor(lib);assert n.call('format')==0
    assert n.put(1,b'a'*5000)==0;assert n.put(17,b'b'*2000)==0;assert n.call('remove',1)==0
    assert n.put(23,b'c'*12000)==0
    raw,manifest=upgrade(n.data);assert objects(raw)=={17:b'b'*2000,23:b'c'*12000}
    assert locations(raw)=={17:(8192,2000),23:(12288,12000)}
    recovered=Nor(lib,raw);assert recovered.call('open')==0;assert recovered.get(23)==b'c'*12000

def test_opaque_legacy_preservation():
    from k6_user_store_migrate import upgrade,objects
    raw=bytearray(b'\xff'*CAP);opaque=bytes(range(256))*64;raw[3*16384:4*16384]=opaque
    packed,manifest=upgrade(raw,preserve_opaque=True)
    assert objects(packed)=={3:opaque};assert manifest==[(3,16384,4)]


def test_recovery_journal(tmp_path):
    from k6_user_store_restore import prepare,load_recovery,complete
    from types import SimpleNamespace
    raw=b'\xff'*CAP;fingerprint=b'x'*4096
    c=SimpleNamespace(read=lambda a,n:raw if a==BASE else fingerprint)
    bank,journal=prepare(c,tmp_path,'1234','test',lambda *_:None)
    assert load_recovery(journal,c,'1234','test')==bank
    with pytest.raises(ValueError):load_recovery(journal,c,'5678','test')
    c.read=lambda a,n:b'y'*n
    with pytest.raises(ValueError):load_recovery(journal,c,'1234','test')
    c.read=lambda a,n:raw if a==BASE else fingerprint
    complete(journal)
    with pytest.raises(ValueError):load_recovery(journal,c,'1234','test')


def test_packed_short_module_removal(tmp_path):
    from modem import k6mod
    from host_link import gc_sections, exported_symbols
    path=tmp_path/'packed.so'
    flags=exported_symbols(tmp_path/'exports', ('ku_*','kl_install','kl_info','kl_remove'))
    subprocess.run(['cc','-shared','-fPIC','-O2','-ffunction-sections','-fdata-sections',
                    gc_sections(),flags,'-DKM_USER_STORE',
                    str(ROOT/'c/k6/user_store.c'),str(ROOT/'c/k6/k6_lib.c'),'-o',str(path)],check=True)
    l=C.CDLL(str(path));n=Nor(l);assert n.call('format')==0
    l.kl_install.argtypes=[C.c_char_p,C.POINTER(Flash)]
    for slot,code,entry in [(24,b'\x2a\x20\x70\x47',0),(25,b'K6NM'+b'PACK TEST'.ljust(12,b'\0')+b'\x2a\x20\x70\x47',16)]:
        img=k6mod.image(code,entry,1,None,lib=slot,level=1)
        assert l.kl_install(img,C.byref(n.flash))==len(img)
        assert n.call('open')==0 and n.get(slot)==img
        assert l.kl_remove(slot,C.byref(n.flash))==0
        assert n.call('open')==0 and n.get(slot) is None


@pytest.fixture(scope="module")
def live_lib(tmp_path_factory):
    from host_link import gc_sections,exported_symbols
    p=tmp_path_factory.mktemp("live-lib")
    flags=exported_symbols(p/"exports",("ku_*","kl_live_mutate","km_serial_highwater","km_armed","km_init"))
    so=p/"live.so"
    subprocess.run(["cc","-shared","-fPIC","-O2","-ffunction-sections","-fdata-sections",
        gc_sections(),flags,"-DKM_USER_STORE","-DKM_KEY="+",".join(map(str,range(16))),
        *(str(ROOT/"c/k6"/f) for f in ("user_store.c","k6_lib.c","k6_mod.c")),
        "-o",str(so)],check=True)
    l=C.CDLL(str(so))
    l.kl_live_mutate.argtypes=[C.c_char_p,C.c_uint,C.POINTER(Flash)]
    return l


def test_live_mutation_signed_and_typed(live_lib):
    from modem import k6mod
    l=live_lib;l.km_init()
    n=Nor(l);assert n.call("format")==0
    key=bytes(range(16))
    def img(serial,slot=24,**kw):
        return k6mod.image(b"\x2a\x20\x70\x47",0,serial,key,lib=slot,level=1,**kw)
    def mutate(b):return l.kl_live_mutate(b,len(b),C.byref(n.flash))
    original=img(10);bad=original[:-1]+bytes([original[-1]^1])
    raw=bytes(n.data)
    for malformed in (bad,original+b"x",original[:-1],img(11,slot=9),img(11,page=True)):
        assert mutate(malformed)<0
        assert bytes(n.data)==raw and l.km_serial_highwater()==0
    assert mutate(original)==len(original)
    assert l.km_armed()==0 and l.km_serial_highwater()==10
    assert n.call("open")==0 and n.get(24)==original
    raw=bytes(n.data);assert mutate(original)<0 and bytes(n.data)==raw
    # A typed page in an ordinary slot and unknown opaque objects are protected.
    for slot,blob in [(25,img(11,slot=25,page=True)),(26,b"opaque"),(27,b"K6T\x09"+b"x"*20)]:
        assert n.put(slot,blob)==0
        before=bytes(n.data)
        assert mutate(img(20+slot,slot=slot))<0
        assert bytes(n.data)==before
    delete=k6mod.image(b"K6ERASE",0,100,key,lib=24,level=1,remove=True)
    assert mutate(delete)==0 and not l.km_armed()
    assert n.call("open")==0 and n.get(24) is None


def test_live_mutation_interrupted_commit(live_lib):
    from modem import k6mod
    l=live_lib;l.km_init()
    key=bytes(range(16))
    n=Nor(l);assert n.call("format")==0
    old=k6mod.image(b"\x70\x47",0,1,key,lib=24,level=1)
    new=k6mod.image(b"\x70\x47"+b"x"*2000,0,2,key,lib=24,level=1)
    delete=k6mod.image(b"K6ERASE",0,3,key,lib=24,level=1,remove=True)
    assert n.put(24,old)==0 and n.put(25,b"untouched")==0
    for blob,want in ((new,new),(delete,None)):
        raw=bytes(n.data)
        probe=Nor(l,raw);l.km_init()
        assert l.kl_live_mutate(blob,len(blob),C.byref(probe.flash))>=0
        for cut in range(1,probe.ops+1):
            m=Nor(l,raw);l.km_init();m.limit=cut
            l.kl_live_mutate(blob,len(blob),C.byref(m.flash))
            recovered=Nor(l,m.data);assert recovered.call("open")==0
            assert recovered.get(24) in (old,want)
            assert recovered.get(25)==b"untouched"
            assert not l.km_armed()
