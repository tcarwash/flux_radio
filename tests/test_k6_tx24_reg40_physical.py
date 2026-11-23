"""TX24 read-only replay of REAL B-chip REG40 RF evidence.

Source trace is locally archived; never commit IQ raw data. All scoring
parameters originated in the prospective source/analysis frozen at 5acbd7b.
The independent time-domain discriminator RMS check was designed after the
first physical run and is EXPLORATORY cross-check, not a revised pass rule.
"""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx24_reg40_deviation_analyze import (
    analyze_file,LOW8,STEP_SECONDS
)
from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency

BASE=Path.home()/"flux-bench/tx-injection-20261122/tx24-reg40"
RAW=BASE/"tx24-reg40-run01.npy"
RECEIPT=BASE/"tx24-reg40-run01.json"
SCORE=BASE/"tx24-reg40-run01-locked-v1.json"
BACKUP=BASE/"tx24-reg40-run01-slot30-before.k6m"
RAW_SHA256="4115c2c6d401871e38479edd1393298b76978fbfca9f2b0f110bb28de5cd9f48"
ORIGINAL_SHA="6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522"
SOURCE_CODE_SHA="e792a8bd77d2f055a16f9e1da025a4f7a4555b6dbb9d3417817612e5e21024f0"

def evidence():
    for p in (RAW,RECEIPT,SCORE,BACKUP):
        if not p.exists():
            pytest.skip("TX24 original physical IQ / private bench receipt not on this host")

def test_prospective_first_real_reg40_modulator_pass():
    evidence()
    assert hashlib.sha256(RAW.read_bytes()).hexdigest()==RAW_SHA256
    original=json.loads(SCORE.read_text())
    assert original["source_sha256"]==RAW_SHA256
    assert original["score"]["candidate_deviation_actuator_qualified"]
    assert all(original["score"]["conditions"].values())
    new=analyze_file(RAW)
    assert new["score"]["candidate_deviation_actuator_qualified"]
    assert new["score"]["ratios_by_lowbyte"]==original["score"]["ratios_by_lowbyte"]
    ratios=new["score"]["ratios_by_lowbyte"]
    for level in (0,22,64,128):
        expected=(256+level)/(256+22)
        assert abs(ratios[str(level)]-expected)<.003
    assert new["score"]["pilot_only_frequency_phase_and_amplitude_fit"]["pilot_amplitude_hz"]>1100
    assert new["score"]["baseline_reference_heldout_phase_rms_deg"]<1.0

def test_signed_original_slot_and_both_operator_vfos_restored():
    evidence()
    from k6_tx15t_rf_bench import fingerprint
    meta=json.loads(RECEIPT.read_text())
    tx=meta["hardware_lab_transaction"]
    assert tx["tx_module_requests"]==1
    assert tx["module_completion"]["returned"] and tx["module_completion"]["result"]==13
    assert tx["module_completion"]["reports"]==["tx24 n=13 pa=1"]
    assert tx["module_code_sha256"]==SOURCE_CODE_SHA
    assert tx["module_tx_watchdog_ms"]==1800
    assert tx["original_module_restored"] and tx["original_vfos_restored"]
    assert "restoration_error" not in tx
    assert "module_error" not in tx
    assert tx["original_slot_fingerprint"]["correct_crc"]
    assert tx["original_slot_fingerprint"]["code_sha256"]==ORIGINAL_SHA
    original=fingerprint(BACKUP.read_bytes())
    assert original["code_sha256"]==ORIGINAL_SHA and original["correct_crc"]
    for field in ("initial_radio_status","final_radio_status"):
        status=tx[field]
        assert status["watching"]==0 and not status["transmitting"]
        assert status["standby_idle"] and not status["sleeping"]
        assert [v["hz"] for v in status["vfos"]]==[440850000,144390000]
        assert all(v["mode"]=="FM" and v["wide"] is True for v in status["vfos"])

def test_exploratory_no_sine_fit_rms_corrob_and_rf_magnitude_near_constant():
    evidence()
    z,f,t,mag,discovery=extract_iq_and_rx_frequency(RAW)
    onset=discovery["onset_s"]
    data=[]
    for i,code in enumerate(LOW8):
        selected=((t[:-1]>=onset+(i+.22)*STEP_SECONDS)&
                  (t[:-1]<onset+(i+.78)*STEP_SECONDS))
        assert selected.sum()>1000
        sigma=float(np.sqrt(2*np.var(f[selected])))
        amp=float(np.median(mag[:-1][selected]))
        data.append((code,sigma,amp))
    ref=np.mean([data[i][1] for i in (0,2)])
    assert ref>1000
    for code in (0,22,64,128):
        vals=[sigma/ref for k,sigma,amp in data if k==code]
        expected=(256+code)/(256+22)
        assert abs(np.mean(vals)-expected)<.003
        if code!=22:
            assert len(vals)==2
            assert abs(vals[0]-vals[1])<.006
    levels=[amp for code,sigma,amp in data]
    assert max(levels)/min(levels)<1.025
    assert discovery["signal_to_noise_proxy"]>20
