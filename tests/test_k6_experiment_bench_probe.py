"""Read-only bench probe. No device access in unit tests."""
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
SPEC=importlib.util.spec_from_file_location("k6_experiment_bench_probe",ROOT/"tools/k6_experiment_bench_probe.py")
b=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)

def sample():
    return {"capabilities":{"format":2,"build_id":"339e08a2b77c7679",
                            "module_loader":True,"cable_only":True,"module_api_max":29},
            "status":{"standby_idle":True,"transmitting":False,"sleeping":False,
                      "squelch_open":False,"watching":0,"vfos":[
                      {"hz":433500000,"mode":"FM","wide":True,"memory":False},
                      {"hz":144390000,"mode":"FM","wide":True,"memory":False}]}}

def test_pass_read_only_attestation():
    assert b.check(sample(),expected_build="339e08a2b77c7679",minimum_api=29)

@pytest.mark.parametrize("change,value",[
    ("build_id","other"),("module_api_max",25),
    ("module_loader",False),("cable_only",False)])
def test_capability_fail_closed(change,value):
    a=sample();a["capabilities"][change]=value
    with pytest.raises(ValueError):
        b.check(a,expected_build="339e08a2b77c7679",minimum_api=29)

@pytest.mark.parametrize("field,value",[
    ("transmitting",True),("sleeping",True),("squelch_open",True),
    ("standby_idle",False),("watching",1)])
def test_status_fail_closed(field,value):
    a=sample();a["status"][field]=value
    with pytest.raises(ValueError):
        b.check(a,expected_build="339e08a2b77c7679",minimum_api=29)

def test_vfo_baseline_fail_closed():
    a=sample();baseline=sample()
    a["status"]["vfos"][0]["hz"]=444000000
    with pytest.raises(ValueError):
        b.check(a,baseline=baseline)
