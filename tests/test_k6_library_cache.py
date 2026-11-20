import pytest
from k6_install import Cable, LIB_BASE, LIB_SLOT, InstallError, select_theme_install
from k6_user_store_migrate import compact
from k6_theme_package import pack, unpack
from k6_memory_radio import read_bank, CAP, BANK


def test_directory_cache_and_invalidation(monkeypatch):
    c = Cable(None)
    c._info = {'protocol':5}
    raw, _ = compact({23: b'abc'})
    reads = []
    def read(addr,n):
        reads.append((addr,n))
        return raw[addr-LIB_BASE:addr-LIB_BASE+n]
    monkeypatch.setattr(c,'read',read)
    assert c.library_read(23,5)==b'abc\xff\xff'
    assert c.library_read(24,64)==b'\xff'*64
    assert c.library_directory()=={23:(8192,3)}
    c.library_directory().clear()
    assert c.library_directory()=={23:(8192,3)}
    assert reads.count((LIB_BASE,8192))==1
    c.invalidate_library_directory()
    raw,_=compact({24:b'xyz'})
    assert c.library_read(23,64)==b'\xff'*64
    assert c.library_read(24,3)==b'xyz'
    assert reads.count((LIB_BASE,8192))==2


def test_failed_image_transfer_invalidates_cache(monkeypatch):
    c=Cable(None)
    c._library_directory={23:(8192,3)}
    from k6_install import k6mod
    def fail(*args):raise InstallError('transfer failed')
    monkeypatch.setattr(k6mod,'packets',fail)
    with pytest.raises(InstallError):
        c.send_image(b'bad',0)
    assert not hasattr(c,'_library_directory')


def test_theme_revisions_do_not_overwrite_or_duplicate():
    old=pack('flux','cards')
    new=pack('minimal','cards')
    class Link:
        def __init__(self):self.items={16:old}
        def theme_at(self,slot):
            b=self.items.get(slot)
            return (unpack(b,include_name=True)[1],b) if b else None
        def library_read(self,slot,n):
            return self.items.get(slot,b'').ljust(n,b'\xff')[:n]
    c=Link()
    assert select_theme_install(c,old)==(16,old,True)
    slot,blob,current=select_theme_install(c,new)
    assert slot==17 and not current
    assert unpack(blob,include_name=True)[1]=='cards-17'
    c.items[slot]=blob
    assert select_theme_install(c,new)==(17,blob,True)
    assert c.items[16]==old
    with pytest.raises(InstallError):
        select_theme_install(c,new,16)


def test_memory_bank_uses_bulk_only_after_capability():
    class Link:
        def call(self,op):assert op==0x90;return CAP
        def read(self,a,n):assert (a,n)==(0,BANK);return b'x'*n
    c=Link()
    assert read_bank(c)==b'x'*BANK
    c.call=lambda op:b'bad'
    with pytest.raises(ValueError,match='protocol'):
        read_bank(c)
