"""Safety and reproducibility tests for the one-shot APC RF lab harness.

No hardware, MCU flashing, SDR device or TX in these tests.
"""
from pathlib import Path
import hashlib
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_apc_boundary_bench import run_transaction
import k6_apc_boundary_bench as bench


def test_no_radio_access_until_armed(tmp_path):
    with pytest.raises(ValueError,match="dummy-load-confirmed"):
        run_transaction("/dev/no-radio",
                        tmp_path/"iq",ROOT/"c/k6/mod/apc_staircase.c",
                        tmp_path/"slot.k6m",armed=False)
    assert not list(tmp_path.iterdir())


def test_existing_capture_aborts_before_any_device_io(tmp_path):
    (tmp_path/"iq.json").write_text("{}")
    with pytest.raises(FileExistsError):
        run_transaction("/dev/no-radio",
                        tmp_path/"iq",ROOT/"c/k6/mod/apc_staircase.c",
                        tmp_path/"slot.k6m",armed=True)
    assert len(list(tmp_path.iterdir()))==1


def test_compiled_program_is_byte_pinned():
    expected="c876f9d791662f72452b909834da227facd1f06aef32597afcaae7b9e4404e2a"
    code=ROOT/"c/k6/mod/apc_staircase.c"
    s=bench.__file__
    source=Path(s).read_text()
    assert expected in source
    assert "source_level(str(source)) != 27" in source
    assert 'EXPECTED_BUILD="5b80f53e0f177bbb"' in source
    assert "module_api_max" in source and "<27" in source
    assert 'level=27,lib=SLOT' in source
    assert "mutate_library(new_img)" in source
    assert "mutate_library(restored_img)" in source
    assert "link.tune(INITIAL_HZ)" in source
    assert "not report[\"original_module_restored\"]" in source
    assert "if raw is not None" in source
    assert "tx_module_requests" in source
    assert "ThreadPoolExecutor(max_workers=1)" in source
    assert "No MCU firmware flashing" in source
    assert "flash_image(" not in source
    assert "fl_prog(" not in source
    assert code.exists()


def test_enabled_program_hash_reproducible(tmp_path):
    enabled=tmp_path/"enabled.c"
    enabled.write_text(
        "#define K6_MODULE_LEVEL 27\n"
        "#define APC_STEP_ENABLE 1\n"
        "#define APC_STEP_FIXTURE_ACK 1\n"
        "#define APC_STEP_WATCHDOG_ACK 1\n"
        f'#include "{ROOT/"c/k6/mod/apc_staircase.c"}"\n'
    )
    code,entry=k6mod.build(str(enabled))
    assert entry==0 and len(code)<4096
    assert k6mod.source_level(str(enabled))==27
    assert hashlib.sha256(code).hexdigest()=="c876f9d791662f72452b909834da227facd1f06aef32597afcaae7b9e4404e2a"
