"""Opt-in Designer theme regression on a compiled virtual K6, never RF hardware.

Build with K6_DESIGNER_SERVICE=1 K6_DESIGNER_TEST_SLOT=31.
Set K6_SIM_THEME_TX_IMAGE and K6_SIM_THEME_TX_ELF to the matching image and ELF.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_feature_packs import encode_layer
from k6_theme_package import pack


def test_theme_survives_physical_ptt():
    image = os.getenv("K6_SIM_THEME_TX_IMAGE")
    elf = os.getenv("K6_SIM_THEME_TX_ELF")
    if not image or not elf:
        pytest.skip("set matching Designer bench-slot image and ELF")
    unicorn = pytest.importorskip("unicorn")
    from sim.k6emu.__main__ import K6Board

    board = K6Board(Path(image), collect_mmio=False)
    board.flash_data[0xB000:0xB004] = b"AURA"
    # Distinctive art in a normally blank corner of the standby screen.
    art = bytearray(8192)
    points = [(x,y) for y in range(60,64) for x in range(120,128)]
    for x,y in points:
        art[y*128+x] = 1 if (x+y)%2 == 0 else 2
    blob = pack("custom-11-0000000000000000-l"+encode_layer(art)+".0.0")
    base = 0x110000+31*0x4000
    board.flash_data[base:base+len(blob)] = blob

    rows = subprocess.check_output(["llvm-nm","-C",elf],text=True).splitlines()
    tx = int(next(r for r in rows if r.endswith("<aura::device::radio::Radio>::enter_tx")).split()[0],16)&~1
    calls = []
    board.uc.hook_add(unicorn.UC_HOOK_CODE,lambda *args: calls.append(1),begin=tx,end=tx)

    def advance(n):
        board.run(n)
        assert not board.fault, board.fault

    def assert_art():
        pixels = board.screen.ppm().split(b"\n",3)[3]
        for x,y in points:
            at = 3*(y*128+x)
            assert pixels[at:at+3] == (b"\x17\x20\x18" if (x+y)%2 == 0 else b"\xe7\xe6\xd6"), (x,y)

    board.set_gpio("A",10,1)
    advance(70_000_000)
    assert_art()
    before = board.screen.snapshot()
    board.set_gpio("A",10,0)
    advance(8_000_000)
    assert calls, "virtual radio did not actually enter TX"
    assert_art()
    assert board.screen.snapshot() != before
    board.set_gpio("A",10,1)
    advance(8_000_000)
    assert_art()
