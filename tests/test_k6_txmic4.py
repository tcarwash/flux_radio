"""MICIN transmit module compile gates; hardware RF not emulated in this suite."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from modem import k6mod
from test_k6_i2s import need_clang, run

SRC = ROOT / "c/k6/mod/txmic4.c"


@need_clang
def test_unconfigured_module_is_inert_in_cortex_m0_emulator():
    code, entry = k6mod.build(str(SRC))
    assert entry == 0 and len(code) < 4096
    assert k6mod.source_level(str(SRC)) == 26
    ret, lines, after, writes = run({0:0x4829},
                                   src=str(SRC), level=26,
                                   count=2_000_000)
    assert ret == -9
    assert lines == ["txmic4 inert: fixture required"]
    assert not writes


@need_clang
def test_explicit_fixture_and_watchdog_optin_compiles_only(tmp_path):
    wrapper = tmp_path/"enabled.c"
    wrapper.write_text("#define K6_MODULE_LEVEL 26\n"
                       "#define TXMIC_ENABLE 1\n"
                       "#define TXMIC_FIXTURE_ACK 1\n"
                       "#define TXMIC_WATCHDOG_ACK 1\n"
                       f'#include "{SRC}"\n')
    code, entry = k6mod.build(str(wrapper))
    assert entry == 0 and len(code) < 4096
    assert k6mod.source_level(str(wrapper)) == 26
    # No RF execution: C module requires a physical conducted fixture.


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(1,0),(0,1)])
def test_missing_any_protection_refuses_to_compile(tmp_path,fixture,watchdog):
    wrapper=tmp_path/f"rejected_{fixture}_{watchdog}.c"
    wrapper.write_text("#define K6_MODULE_LEVEL 26\n"
                       "#define TXMIC_ENABLE 1\n"
                       f"#define TXMIC_FIXTURE_ACK {fixture}\n"
                       f"#define TXMIC_WATCHDOG_ACK {watchdog}\n"
                       f'#include "{SRC}"\n')
    with pytest.raises((ValueError, RuntimeError, OSError, Exception)):
        k6mod.build(str(wrapper))


def test_safety_covenants_stay_in_module_source():
    s=SRC.read_text()
    for contract in ("api->tx_on(TEST_MAX_MS)", "api->tx_pa(0)",
                     "api->micin_tx(1)", "api->micin_tx(0)",
                     "api->tx_off()", "api->fd_read(0x7Eu)",
                     "REG_AF_FILTER", "TXMIC_FIXTURE_ACK",
                     "TXMIC_WATCHDOG_ACK", "TEST_MAX_MS 750u"):
        assert contract in s
    assert "api->fd_write(0x30" not in s
    assert "api->fd_write(0x36" not in s
