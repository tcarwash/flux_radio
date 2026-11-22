"""Fail-closed envelope and arming tests for reversible boundary lab."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_fsk_boundary_bench import fingerprint,run_transaction,SLOT


def test_cable_envelope_fingerprint():
    code,entry=k6mod.build(str(ROOT/"c/k6/mod/fsk_boundary.c"))
    img=k6mod.image(code,entry,301,None,level=3,lib=SLOT)
    fp=fingerprint(img)
    assert fp["level"]==3
    assert fp["slot"]==SLOT
    assert fp["serial"]==301
    assert fp["correct_crc"] is True
    assert fp["code_sha256"]
    assert fp["envelope_sha256"]


@pytest.mark.parametrize("mutation",[
    lambda b: b"\x00"+b[1:],
    lambda b: b[:-1]+bytes([b[-1]^1]),
    lambda b: b[:15]+bytes([0])+b[16:],
])
def test_envelope_corruption_not_trusted(mutation):
    code,entry=k6mod.build(str(ROOT/"c/k6/mod/fsk_boundary.c"))
    img=k6mod.image(code,entry,301,None,level=3,lib=SLOT)
    broken=mutation(img)
    try:
        fp=fingerprint(broken)
    except ValueError:
        return
    assert not fp["correct_crc"] or broken==img


def test_disarmed_refuses_without_port_or_sdr_access(tmp_path):
    with pytest.raises(ValueError,match="dummy-load-confirmed"):
        run_transaction("/dev/does-not-exist",
            tmp_path/"capture",ROOT/"c/k6/mod/fsk_boundary.c",
            tmp_path/"old.k6m",armed=False)
    assert not list(tmp_path.iterdir())


def test_existing_capture_refuses_before_radio_access(tmp_path):
    (tmp_path/"capture.json").write_text("{}")
    with pytest.raises(FileExistsError):
        run_transaction("/dev/does-not-exist",
            tmp_path/"capture",ROOT/"c/k6/mod/fsk_boundary.c",
            tmp_path/"old.k6m",armed=True)


def test_no_direct_firmware_flash_path_in_bench_source():
    s=(ROOT/"tools/k6_fsk_boundary_bench.py").read_text()
    assert "mutate_library(" in s and "original_module_restored" in s
    assert "fl_prog(" not in s
    assert "flash_image(" not in s
    assert "tx_module_requests" in s
