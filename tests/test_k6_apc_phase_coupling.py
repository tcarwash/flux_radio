"""Synthetic APC-only phase-coupling analysis; no radio/serial/SDR access."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_apc_phase_coupling import analyze,FS_OUT

FS=1_024_000


def make_archive(tmp_path:Path,*,edge_phase_offsets=False):
    path=tmp_path/"test_iq.npy"
    sample_count=round(1.95*FS)
    raw=np.full(2*sample_count,128,dtype=np.uint8)
    onset=.96
    holding=[(1.,.08)]
    holding.extend([(v,.0206) for _ in range(8) for v in (.8,1.)])
    holding.extend([(v,.0105) for _ in range(8) for v in (.8,1.)])
    holding.extend([(v,.0055) for _ in range(12) for v in (.8,1.)])
    holding.append((1.,.08))
    duration=sum(t for _,t in holding)
    start=round(onset*FS)
    end=round((onset+duration)*FS)
    t=np.arange(end-start)/FS
    amp=np.full(len(t),18.,dtype=np.float64)
    phase=(-280484.375*2*np.pi)*t
    next_time=0.
    for i,(level,dt) in enumerate(holding):
        mask=(t>=next_time)&(t<next_time+dt)
        amp[mask]=18.*level
        if edge_phase_offsets and 1<=i<=16:
            # Controlled synthetic phase discontinuities. These are
            # NOT claims that the physical radio phase jumped.
            phase[t>=next_time] += (.15 if i%2==1 else -.28)
        next_time+=dt
    raw[2*start:2*end:2]=np.clip(np.round(127.5+amp*np.cos(phase)),0,255).astype(np.uint8)
    raw[2*start+1:2*end:2]=np.clip(np.round(127.5+amp*np.sin(phase)),0,255).astype(np.uint8)
    np.save(path,raw)
    manifest={
        "sample_rate_hz":FS,
        "capture_center_hz":433750000,
        "iq_raw_u8_sha256":hashlib.sha256(raw.tobytes()).hexdigest(),
        "hardware_lab_transaction":{
            "module_completion":{"returned":True,"result":24},
            "original_module_restored":True,
            "original_radio_frequency_restored":True,
        },
    }
    path.with_suffix(".json").write_text(json.dumps(manifest))
    return path


def test_no_phase_effect_gives_near_zero_edge_fits(tmp_path):
    r=analyze(make_archive(tmp_path,edge_phase_offsets=False))
    assert r["apc_stages"]==16
    assert abs(r["high_to_low_phase_fit_offset_median_rad"])<.055
    assert abs(r["low_to_high_phase_fit_offset_median_rad"])<.055
    assert r["median_absolute_stage_edge_phase_fit_offset_rad"]<.055
    assert r["combined_frequency_and_amplitude_modulation_demonstrated"] is False


def test_synthetic_known_directional_phase_effect_is_recovered(tmp_path):
    r=analyze(make_archive(tmp_path,edge_phase_offsets=True))
    assert r["apc_stages"]==16
    assert r["high_to_low_phase_fit_offset_median_rad"]==pytest.approx(.15,abs=.065)
    assert r["low_to_high_phase_fit_offset_median_rad"]==pytest.approx(-.28,abs=.065)
    assert r["all_edge_phases_directionally_consistent"]
    assert r["median_absolute_stage_edge_phase_fit_offset_rad"] > r["median_absolute_nonedge_comparison_phase_fit_offset_rad"]


def test_invalid_capture_restoration_rejected(tmp_path):
    p=make_archive(tmp_path)
    meta_path=p.with_suffix(".json")
    meta=json.loads(meta_path.read_text())
    meta["hardware_lab_transaction"]["original_module_restored"]=False
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="hardware-validated"):
        analyze(p)


def test_phase_analyzer_does_not_control_radio():
    s=(ROOT/"tools/k6_apc_phase_coupling.py").read_text()
    for x in ("import serial","from k6_live","tx_on(","fd_write(",
              "mutate_library(","run_module(","flash("):
        assert x not in s
    assert "phase_coupling_source_attribution_proven" in s
    assert "NOT\nnecessarily mathematically instantaneous" in s
