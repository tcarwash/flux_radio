"""No-hardware build and safety checks for the 80-100% RF APC response test."""
from pathlib import Path
import hashlib
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_apc_fast_bench import run_transaction
SRC=ROOT/"c/k6/mod/apc_fast_envelope.c"
HASH="80e528b0caf273c245c0396a302bc8e72b0f6878c82515158163d5a7a9aacb3e"


def test_opt_in_only_compilation(tmp_path):
    inert,e=k6mod.build(str(SRC))
    assert e==0 and k6mod.source_level(str(SRC))==27
    for f,w in ((0,0),(1,0),(0,1)):
        file=tmp_path/f"bad_{f}{w}.c"
        file.write_text("#define APC_FAST_ENABLE 1\n"
                        f"#define APC_FAST_FIXTURE_ACK {f}\n"
                        f"#define APC_FAST_WATCHDOG_ACK {w}\n"
                        f'#include "{SRC}"\n')
        with pytest.raises(Exception):
            k6mod.build(str(file))
    enabled=tmp_path/"enabled.c"
    enabled.write_text("#define K6_MODULE_LEVEL 27\n"
                       "#define APC_FAST_ENABLE 1\n"
                       "#define APC_FAST_FIXTURE_ACK 1\n"
                       "#define APC_FAST_WATCHDOG_ACK 1\n"
                       f'#include "{SRC}"\n')
    code,entry=k6mod.build(str(enabled))
    assert entry==0 and 0<len(code)<4096
    assert hashlib.sha256(code).hexdigest()==HASH


def test_fast_module_never_exceeds_calibrated_apc():
    s=SRC.read_text()
    assert "APC_FAST_TX_LIMIT_MS 1800u" in s
    assert "original>>8" in s
    assert "percent!=80u && percent!=100u" in s
    assert "a->apc_set(percent)" in s
    assert "a->fd_read(R_PA)!=expected" in s
    assert "if(!safe(a))" in s
    assert "a->tx_off()" in s
    assert "a->fd_write" not in s
    assert "km_power_switch_off()" in s
    assert "a->key_down()" in s
    # Total of all desired holds = 760ms, safely inside the 1.8s lease.
    assert 80+8*2*20+8*2*10+12*2*5+80 == 760
    bench=(ROOT/"tools/k6_apc_fast_bench.py").read_text()
    assert HASH in bench and 'EXPECTED_BUILD="ef53ce1d2ca74667"' in bench
    assert "level=27,lib=SLOT" in bench
    assert "mutate_library(restored_img)" in bench


def test_unarmed_lab_aborts_without_io(tmp_path):
    with pytest.raises(ValueError,match="dummy-load-confirmed"):
        run_transaction("/dev/should-not-open",tmp_path/"foo", SRC,
                        tmp_path/"slot.k6m",armed=False,expected_result=24)
    assert not list(tmp_path.iterdir())
