"""Real C SPI loader and shared tag check; no radio or published resident ABI."""
import ctypes as C
import struct
import subprocess
from pathlib import Path

import pytest
from modem import k6mod
from host_link import gc_sections, exported_symbols

ROOT = Path(__file__).resolve().parents[1]
BASE, SLOT, FLAGS = 0x110000, 0x4000, 0x25
KEY = bytes(range(16))

class Info(C.Structure):
    _fields_ = [('state_bytes', C.c_uint16), ('callback', C.c_uint16),
                ('state_offset', C.c_uint16), ('id', C.c_uint8), ('label', C.c_char * 8)]

READ = C.CFUNCTYPE(C.c_int, C.c_uint32, C.c_void_p, C.c_uint32)
WRITE = C.CFUNCTYPE(C.c_int, C.c_uint32, C.c_void_p, C.c_uint32)
ERASE = C.CFUNCTYPE(C.c_int, C.c_uint32)
class Flash(C.Structure):
    _fields_ = [('read', READ), ('prog', WRITE), ('erase', ERASE)]

def image(slot=0, mode=41, state=16, callback=20, label=b'PROBE', code=None, key=KEY):
    if code is None:
        code = b'K6VM' + struct.pack('<BBHHBB8s', 1, mode, state, callback, 1, 0, label) + b'\x00\x20\x70\x47'
    plain = k6mod.image(code, 0, 1, key, lib=slot, cards=True)
    body = bytearray(plain[:-8]); body[13] = FLAGS
    return bytes(body) + k6mod.image_tag(key, body)

@pytest.fixture
def loader(tmp_path):
    # Link only the real library/tag/arming code, discarding unrelated modem paths.
    lib = tmp_path / 'loader.so'
    exports = tmp_path / 'exports.map'
    export_flag = exported_symbols(exports, ('kl_*', 'km_arm', 'km_armed', 'km_run'))
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-ffunction-sections',
                    '-fdata-sections', gc_sections(), export_flag,
                    '-DKM_KEY=' + ','.join(map(str, KEY)),
                    str(ROOT/'c/k6/k6_lib.c'), str(ROOT/'c/k6/k6_mod.c'),
                    '-o', str(lib)], check=True)
    dll = C.CDLL(str(lib))
    nor = bytearray(b'\xff' * (32*SLOT))
    def read(addr, buf, n):
        off = addr-BASE
        if off < 0 or off+n > len(nor): return -1
        C.memmove(buf, bytes(nor[off:off+n]), n)
        return 0
    def erase(addr):
        off = addr-BASE
        nor[off:off+4096] = b'\xff'*4096
        return 0
    fl = Flash(READ(read), WRITE(lambda *_: -1), ERASE(erase))
    arena = (C.c_uint8 * 7680)()
    return dll, nor, fl, arena

def put(nor, img, slot=0):
    nor[slot*SLOT:slot*SLOT+len(img)] = img

def load(env, mode=41, cap=7680):
    dll, nor, fl, arena = env
    info = Info()
    r = dll.kl_load_resident(0, C.byref(fl), arena, cap, mode, C.byref(info))
    return r, info

def test_load_authenticates_without_arming_and_places_state(loader):
    dll, nor, fl, arena = loader
    put(nor, image())
    C.memset(arena, 0xAA, len(arena))
    r, info = load(loader)
    assert r == 24 and info.callback == 20 and info.state_offset == 48
    assert bytes(arena[48:64]) == bytes(16) and arena[64] == 0xAA
    assert dll.km_armed() == 0
    assert dll.kl_load(0, C.byref(fl), arena, len(arena)) < 0
    assert dll.kl_page(0, C.byref(fl), arena, len(arena)) < 0
    # Action exports are a separate, optional work-in-progress host extension.
    if hasattr(dll, 'kl_load_action'):
        assert dll.kl_load_action(0, C.byref(fl), arena, len(arena), 41) < 0
    dll.km_arm(arena, 0)
    assert dll.km_run(arena, None) == -3 and dll.km_armed() == 0

@pytest.mark.parametrize('change', ['tag', 'wrong_key', 'callback', 'state', 'label', 'id', 'budget'])
def test_refuses_invalid_provider(loader, change):
    dll, nor, fl, arena = loader
    opts = {'wrong_key': {'key': bytes(16)}, 'callback': {'callback': 22+1},
            'state': {'state': 0}, 'label': {'label': b'A\0B'}, 'id': {'mode': 255}}
    put(nor, image(**opts.get(change, {})))
    if change == 'tag': nor[40] ^= 1
    r, _ = load(loader, cap=63 if change == 'budget' else 7680)
    assert r < 0 and dll.km_armed() == 0

def test_discovery_duplicate_removal_and_expected_identity(loader):
    dll, nor, fl, arena = loader
    put(nor, image())
    assert dll.kl_find_resident(41, C.byref(fl)) == 0
    assert load(loader, mode=42)[0] < 0
    put(nor, image(slot=1), slot=1)
    assert dll.kl_find_resident(41, C.byref(fl)) == -7
    assert dll.kl_remove(1, C.byref(fl)) == 0
    assert dll.kl_find_resident(41, C.byref(fl)) == 0
    assert dll.kl_remove(0, C.byref(fl)) == 0
    assert dll.kl_find_resident(41, C.byref(fl)) == -1
    assert load(loader)[0] < 0

def test_changed_header_between_spi_reads_is_rejected(loader):
    dll, nor, fl, arena = loader
    old = image()
    changed = bytearray(old)
    struct.pack_into('<H', changed, 4, 22)
    changed = bytes(changed[:38]) + k6mod.image_tag(KEY, changed[:38])
    calls = 0
    def read(addr, buf, n):
        nonlocal calls
        calls += 1
        data = old if calls == 1 else changed
        C.memmove(buf, data.ljust(n, b'\0'), n)
        return 0
    race = Flash(READ(read), fl.prog, fl.erase)
    info = Info()
    assert dll.kl_load_resident(0, C.byref(race), arena, len(arena), 41, C.byref(info)) == -2
    assert dll.km_armed() == 0
