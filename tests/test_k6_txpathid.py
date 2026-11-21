"""Safe FD6818B TX-path readout, including a real Cortex-M0 execution test."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from modem import k6mod
from test_k6_i2s import need_clang, run

SOURCE = ROOT / "c/k6/mod/txpathid.c"
REGS = (0, 0x2b, 0x30, 0x38, 0x39, 0x3a, 0x3b, 0x3c, 0x40,
        0x43, 0x47, 0x48, 0x50, 0x58, 0x59, 0x70, 0x71, 0x72,
        0x73, 0x74, 0x75, 0x76, 0x7d, 0x7e)


@need_clang
def test_native_module_is_small_and_api_level_2():
    code, entry = k6mod.build(str(SOURCE))
    assert len(code) < 4096
    assert entry == 0
    assert k6mod.source_level(str(SOURCE)) == 2


@need_clang
def test_register_fingerprint_is_read_only_on_emulated_m0():
    registers = {a: (0x1234 ^ a * 31) & 0xffff for a in REGS}
    registers[0] = 0x4829
    tx = {}
    ret, said, after, writes = run(registers, src=str(SOURCE),
                                   level=2, tx=tx, count=2_000_000)
    assert ret == len(REGS)
    assert said[0] == "txpathid begin RX read-only"
    assert said[-1] == "txpathid end no writes"
    assert after == registers
    assert not writes
    assert not tx.get("log")
    assert len(said) == 2 + (len(REGS) + 2) // 3
    for reg in REGS:
        assert f"r{reg:02x}={registers[reg]:04x}" in " ".join(said)


@need_clang
def test_wrong_chip_never_probes():
    ret, said, after, writes = run({0: 0x4819, 0x2b: 0x1234},
                                   src=str(SOURCE), level=2, count=1_000_000)
    assert ret == -6
    assert said == ["txpathid skip: not FD6818B"]
    assert not writes


@need_clang
def test_pressed_key_never_probes():
    ret, said, after, writes = run({0: 0x4829, 0x2b: 0x1234},
                                   src=str(SOURCE), level=2,
                                   keys=((0, 100_000),), count=1_000_000)
    assert ret == -2
    assert said == ["txpathid skip: key pressed"]
    assert not writes
