"""Public read-only report and stable fixture reproduction."""
import json
import subprocess
import sys
from pathlib import Path
from tools.k6_physical_model_report import report

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/k6_bchip_20261125_calibration.json'


def test_report_provenance_and_heldout():
    r=report(FIX)
    assert r['physical_runs']=={'transmit':21,'microphone':6}
    assert not r['is_validated_silicon_model'] and r['measurement_only']
    assert r['tx_profiles']['onradio_pa20_gap0']['crc_valid_packets']==18
    assert r['tx_profiles']['direct_pa20_gap60']['crc_valid_packets']==15
    assert r['tx_profiles']['pa_muted_preload']['unobserved_packets']==3
    assert r['tx_profiles']['legacy_sdr_reader']['status']=='measurement_chain_only'
    assert r['gp0_pcm']['2khz']['average_2khz_score']==23867
    assert all(r['holdouts'][i]['observed_complete'] for i in (0,1))


def test_cli_and_committed_fixture_repeatability(tmp_path):
    p=tmp_path/'k6-model-cal.json'
    subprocess.run([sys.executable,'tools/k6_physical_model_report.py','--output',str(p)],
                   cwd=ROOT,check=True,timeout=20)
    assert json.loads(p.read_text())['measurement_only']
    tx=Path('/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125')
    mic=Path('/home/tyler/flux-bench/sotto-mic-calibration-20261125')
    if tx.is_dir() and mic.is_dir():
        from tools.k6_build_physical_model_fixtures import build
        assert build(tx,mic)==json.loads(FIX.read_text())
