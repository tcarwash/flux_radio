"""TX24BB one-shot RF harness acceptance, OFFLINE ONLY: never open radio."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"tools"),str(ROOT)]
from tools.k6_tx24b_fsk_reg40_rf_bench import (
    SOURCE,PROBE,CODE_SHA,SOURCE_SHA,PROBE_SHA,SOURCE_BYTES,
    frozen_build,baseline_vfos,run_transaction,output_paths,
    VFO_A,VFO_B,LAB_HZ,
)

def baseline(*,a=VFO_A,b=VFO_B,watch=0,mode="FM",wide=True,memory=False,tx=False):
    return dict(vfos=[dict(hz=a,mode=mode,wide=wide,memory=memory),
                      dict(hz=b,mode=mode,wide=wide,memory=memory)],
                watching=watch,transmitting=tx,
                standby_idle=not tx,sleeping=False)

def test_locked_source_compiles_to_precommitted_size_and_hash():
    code,entry=frozen_build()
    import hashlib
    assert entry==0 and len(code)==SOURCE_BYTES==1080
    assert hashlib.sha256(code).hexdigest()==CODE_SHA
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==SOURCE_SHA
    assert hashlib.sha256(PROBE.read_bytes()).hexdigest()==PROBE_SHA

def test_operator_vfo_gate_preserves_both_exact_original_vfos():
    assert baseline_vfos(baseline())
    assert not baseline_vfos(baseline(a=144390000))
    assert not baseline_vfos(baseline(b=440850000))
    assert not baseline_vfos(baseline(watch=1))
    assert not baseline_vfos(baseline(mode="AM"))
    assert not baseline_vfos(baseline(wide=False))
    assert not baseline_vfos(baseline(memory=True))
    assert not baseline_vfos(baseline(tx=True))
    assert not baseline_vfos(dict(vfos=[]))

@pytest.mark.parametrize("ack,load",[
    (False,False),(False,True),(True,False)
])
def test_no_radio_access_without_two_explicit_checks(tmp_path,ack,load,monkeypatch):
    import tools.k6_tx24b_fsk_reg40_rf_bench as b
    def fail(*a,**k):
        pytest.fail("No radio or SDR operation allowed without both approvals")
    monkeypatch.setattr(b,"serial_lock",fail)
    monkeypatch.setattr(b,"open_port",fail)
    monkeypatch.setattr(b,"RtlCapture",fail)
    with pytest.raises(ValueError,match="independently ACKed"):
        b.run_transaction("/dev/ttyUSB0",tmp_path/"run01",
            operator_lease_confirmed=ack,dummy_load_confirmed=load)

def test_alternate_serial_device_rejected_even_with_flags(tmp_path):
    with pytest.raises(ValueError,match="canonical Flux ttyUSB0"):
        run_transaction("/dev/ttyUSB4",tmp_path/"run01",
            operator_lease_confirmed=True,dummy_load_confirmed=True)

def test_exact_one_shot_cli_refuses_missing_flags(tmp_path):
    p=subprocess.run([
        sys.executable,str(ROOT/"tools/k6_tx24b_fsk_reg40_rf_bench.py"),
        "--prefix",str(tmp_path/"test01")],
        capture_output=True,text=True,timeout=12)
    assert p.returncode !=0
    assert "independently ACKed" in (p.stdout+p.stderr)

def test_refuse_overwrite_before_any_serial_or_sdr(tmp_path,monkeypatch):
    import tools.k6_tx24b_fsk_reg40_rf_bench as b
    def fail(*args,**kwargs):
        pytest.fail("No serial/RF for an existing evidence output")
    monkeypatch.setattr(b,"serial_lock",fail)
    monkeypatch.setattr(b,"open_port",fail)
    monkeypatch.setattr(b,"RtlCapture",fail)
    for key in ("raw","receipt","before"):
        name=tmp_path/key
        name.mkdir()
        pref=name/"tx24b-fsk-run01"
        output_paths(pref)[key].write_bytes(b"do not overwrite")
        with pytest.raises(FileExistsError,match="no overwrites"):
            b.run_transaction("/dev/ttyUSB0",pref,
                operator_lease_confirmed=True,dummy_load_confirmed=True)

def test_frozen_source_mutation_rejected_before_any_hardware(monkeypatch,tmp_path):
    import tools.k6_tx24b_fsk_reg40_rf_bench as b
    p=tmp_path/"bad_source.c"
    p.write_bytes(SOURCE.read_bytes()+b"// injected alternate run\n")
    monkeypatch.setattr(b,"SOURCE",p)
    with pytest.raises(RuntimeError,match="source hash changed"):
        b.run_transaction("/dev/ttyUSB0",tmp_path/"run01",
            operator_lease_confirmed=True,dummy_load_confirmed=True)

def test_harness_contains_canonical_lock_and_full_restoration_controls():
    src=(ROOT/"tools/k6_tx24b_fsk_reg40_rf_bench.py").read_text()
    assert "with serial_lock(port_path),open_port(" in src
    assert 'with paths["before"].open("xb")' in src
    assert 'original[16:-8]!=trusted[16:-8]' in src
    assert "link.mutate_library(rollback)" in src
    assert "link.tune(VFO_A)" in src
    assert "baseline_vfos(restored)" in src
    assert "no_retry=True" in src
    assert 'operator_lease_confirmed=False' in src
    assert 'dummy_load_confirmed=False' in src
