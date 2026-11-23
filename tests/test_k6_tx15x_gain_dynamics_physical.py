"""Validate archived TX-15X physical receipts when bench data is mounted."""
import hashlib
import json
from pathlib import Path

import pytest

ARCHIVE=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"
CODE_SHA="77ce5f2c90743c05678b827cb22aa114aa41934e41817b4a14b8ae0255839e93"


@pytest.mark.parametrize("run",[2,3])
def test_physical_run_has_v2_holdout_and_verified_restore(run):
    stem=f"tx15x-gain-dynamics-run{run:02d}"
    raw=ARCHIVE/f"{stem}.npy"
    receipt=ARCHIVE/f"{stem}.json"
    score=ARCHIVE/f"{stem}-score-v2.json"
    if not all(p.is_file() for p in (raw,receipt,score)):
        pytest.skip("TX-15X physical archive is not mounted")
    tx=json.loads(receipt.read_text())["hardware_lab_transaction"]
    result=json.loads(score.read_text())
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==result["source_iq_sha256"]
    assert tx["new_test_code_sha256"]==CODE_SHA
    assert tx["transmission_confirmed_by_firmware"]
    assert tx["original_module_restored"]
    assert tx["original_radio_frequency_restored"]
    assert not tx["final_radio_status"]["transmitting"]
    assert result["analysis_version"]=="v2-blockA-timing-calibration"
    score_data=result["score"]
    assert score_data["fit_uses_dynamic_gain_symbols"] is False
    assert score_data["timing_offset_applied_unchanged_to_faster_blocks"]
    heldout=score_data["fast_gain_blocks"][1:]
    assert len(heldout)==2
    assert all(x["high_minus_low_over_pilot24"]>=.10 for x in heldout)
    assert all(x["symbol_audio_phase_rms_deg"]<5 for x in heldout)


def test_two_qualified_runs_are_independent_iq_captures():
    raws=[ARCHIVE/f"tx15x-gain-dynamics-run{i:02d}.npy" for i in (2,3)]
    if not all(p.is_file() for p in raws):
        pytest.skip("TX-15X physical IQ archives are not mounted")
    assert hashlib.sha256(raws[0].read_bytes()).digest()!=hashlib.sha256(raws[1].read_bytes()).digest()
