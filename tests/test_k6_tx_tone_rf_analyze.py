"""Independent held-out RF tone decoding, no hardware access."""
from pathlib import Path
import json
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx_tone_rf_analyze import (
    FS_PROC,N_SYMBOLS,SYMBOL_BAUD,TRAIN_SYMBOLS,TONES,
    symbol_truth,classify_symbol,evaluate,fit_training_only
)


def truth():
    return symbol_truth(ROOT/"c/k6/mod/tx8_probe.h")


def synthetic_fm(*,seed=9):
    rng=np.random.default_rng(seed)
    samples=int(.6*FS_PROC)
    t=np.arange(samples,dtype=float)/FS_PROC+.8
    base=rng.normal(0,20,samples)
    t0=.89015
    for i,tone in enumerate(truth()):
        start=t0+i/SYMBOL_BAUD
        mask=(t>=start)&(t<start+1/SYMBOL_BAUD)
        dt=t[mask]-start
        # Per-symbol arbitrary phase: classifier must tolerate FM
        # oscillator phase resetting rather than integrating tone
        # phases across the entire waveform.
        base[mask]+=1300*np.sin(2*np.pi*TONES[tone]*dt+
                                rng.uniform(-np.pi,np.pi))
    return base,t0


def test_reference_64_symbol_schedule():
    s=truth()
    assert len(s)==64
    assert s[:8]==list(range(8))
    assert s[8:16]==list(range(7,-1,-1))
    assert set(s)==set(range(8))


def test_synthetic_tone_classification_known_timing():
    signal,start=synthetic_fm()
    r=evaluate(signal,truth(),start)
    assert r["training_errors"]==0
    assert r["heldout_errors"]==0
    assert r["heldout_median_true_vs_best_other_ratio"]>2


def test_training_only_timing_fit_holds_out_final_symbols():
    signal,start=synthetic_fm()
    r=fit_training_only(signal,truth())
    assert abs(r["timing_s"]-start)<.0015  # finite tone window permits a short intra-symbol offset
    assert r["heldout_errors"]<=1
    assert r["training_selection"]["selected_on_first_symbols_only"]==TRAIN_SYMBOLS


def test_reject_wrong_length_or_window():
    signal,start=synthetic_fm()
    with pytest.raises(ValueError):
        evaluate(signal,[0]*4,start)
    with pytest.raises(ValueError):
        classify_symbol(signal,.8,5.0)


def test_analyzer_never_uses_holdout_for_acquisition():
    source=(ROOT/"tools/k6_tx_tone_rf_analyze.py").read_text()
    assert "range(TRAIN_SYMBOLS)" in source
    assert "selected_on_first_symbols_only" in source
    for token in ("import serial","from k6_live","mutate_library(",
                  "run_module(","tx_on(","RtlCapture(","import SoapySDR"):
        assert token not in source


def test_output_writes_only_to_new_file(tmp_path):
    p=tmp_path/"iq"
    import numpy as np
    # Error on wrong IQ must not create output.
    np.save(p.with_suffix(".npy"),np.zeros(64,dtype=np.uint8))
    args=[sys.executable,str(ROOT/"tools/k6_tx_tone_rf_analyze.py"),
          "--iq",str(p.with_suffix(".npy")),"--out",str(tmp_path/"report.json")]
    r=subprocess.run(args,capture_output=True,text=True,timeout=20)
    assert r.returncode!=0
    assert not (tmp_path/"report.json").exists()
