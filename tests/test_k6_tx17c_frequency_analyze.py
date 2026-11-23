"""TX-17C RF-frequency latch classification against synthetic complex IQ.

Generated captures simulate the RTL-SDR's tuned center offset and the
unchanged, no-TXON-between-writes source schedule. They are not evidence
about physical BK4829 silicon.
"""
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx17c_direct_frequency_analyze import (
    DELTAS,NSTEP,INTRO_S,STEP_S,TAIL_S,score_captured_raw_iq
)

FS=1_024_000
SHIFT_HZ=280_500.0
ONSET=.910


def synth(path:Path,*,response:bool,noise=0.4):
    n=int(3.0*FS)
    t=np.arange(n,dtype=np.float64)/FS
    # Source is an unmodulated carrier except during synthesizer-word
    # frequency changes. Receiver remains an independent free-running
    # RTL-SDR mixer at a large known IF; the analysis corrects it.
    offset=np.full(n,27.0)
    if response:
        for idx,worddelta in enumerate(DELTAS[:NSTEP]):
            start=ONSET+INTRO_S+idx*STEP_S
            stop=start+STEP_S
            offset[(t>=start)&(t<stop)]=27.+worddelta*10
    active=(t>=ONSET)&(t<(ONSET+INTRO_S+NSTEP*STEP_S+TAIL_S))
    angle=2*np.pi*np.cumsum((-SHIFT_HZ+offset)/FS)
    rng=np.random.default_rng(211)
    sig=0.4*(rng.normal(size=n)+1j*rng.normal(size=n))
    sig+=active*25*np.exp(1j*angle)
    raw=np.empty(2*n,dtype=np.uint8)
    raw[::2]=np.clip(np.real(sig)+127.5,0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.imag(sig)+127.5,0,255).astype(np.uint8)
    np.save(path,raw)


@pytest.mark.parametrize("responsive",[True,False])
def test_synthetic_known_direct_frequency_or_latch_negative(tmp_path,responsive):
    p=tmp_path/"test.npy"
    synth(p,response=responsive)
    score=score_captured_raw_iq(p)
    assert score["source_iq_sha256"]
    assert len(score["regions"])==8
    assert score["inconclusive"] is False
    if responsive:
        assert score["immediate_synth_frequency_response_confirmed"]
        assert not score["no_immediate_latch_detected"]
        assert max(abs(x-y) for x,y in zip(
            score["measured_rf_frequency_deltas_hz"],[500.,-500.,1000.]))<100
    else:
        assert score["no_immediate_latch_detected"]
        assert not score["immediate_synth_frequency_response_confirmed"]
        assert max(abs(x) for x in score["measured_rf_frequency_deltas_hz"])<90


def test_no_radio_device_access_in_analyzer():
    code=(ROOT/"tools/k6_tx17c_direct_frequency_analyze.py").read_text()
    for forbidden in ("import serial", "RtlCapture(", "open_port(",
                      "run_module(", "fd_write(", "tx_on("):
        assert forbidden not in code


def test_archived_physical_if_present():
    p=Path.home()/(
      "flux-bench/tx-injection-20261122/tone-oscillator/"
      "tx17c-pll-lowword-1-run01.npy")
    if not p.exists():
        pytest.skip("TX-17C physical dummy-load RF not recorded yet")
    r=score_captured_raw_iq(p)
    assert r["read_only"] and not (
        r["immediate_synth_frequency_response_confirmed"] and
        r["no_immediate_latch_detected"])
