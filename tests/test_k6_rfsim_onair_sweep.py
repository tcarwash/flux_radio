"""Tests for virtual on-air sweeps; no radio access or physical RF."""
from __future__ import annotations

import json
import numpy as np
import pytest

from tools import k6_rfsim_onair_sweep as lab


def test_csv_values_rejects_empty_and_duplicate():
    assert lab.csv_values("30,15,5", float) == [30.0,15.0,5.0]
    for bad in ("", "a,,b", "1,1", "1,"):
        with pytest.raises(ValueError):
            lab.csv_values(bad)


def test_tx_classes_distinguish_ideal_phase_only_and_k6_path():
    ref, _ = lab.fixture("qpsk")
    ideal, evidence = lab.candidate_waveform("qpsk", "ideal", ("carrier",),
                                             "constant_pa", 117.)
    assert evidence == "host_ideal_IQ_only"
    assert np.array_equal(ref, ideal)
    phase, evidence = lab.candidate_waveform("qpsk", "phase_only", ("carrier",),
                                             "constant_pa", 117.)
    assert evidence == "hypothetical_unrestricted_carrier_phase"
    assert np.max(np.abs(np.abs(phase)-1.)) < 1e-12
    k6, evidence = lab.candidate_waveform("qpsk", "k6_path", ("carrier",),
                                          "constant_pa", 117.)
    assert evidence == "unqualified_K6_path_model"
    assert k6.shape == ref.shape
    assert np.max(np.abs(np.abs(k6)-1.)) < 1e-5
    with pytest.raises(ValueError):
        lab.candidate_waveform("qpsk","k6_path",("native_fsk","tone"),
                               "constant_pa",117.)
    with pytest.raises(ValueError):
        lab.candidate_waveform("qpsk","k6_path",("ideal",),
                               "constant_pa",117.)


def test_case_uses_channel_before_actual_receiver_and_does_not_claim_hardware():
    calls = []
    class Channel:
        def __init__(self,rate,profile,**kwargs):
            calls.append(("init",rate,profile,kwargs["carrier_hz"]))
        def apply(self,x,**kwargs):
            calls.append(("air",kwargs["snr_db"],kwargs["cfo_hz"],len(x)))
            return x*.7
    def receive(mode,**kwargs):
        assert kwargs["tx_model"] == "offline_path_model"
        assert kwargs["snr_db"] == 120.
        assert np.max(np.abs(kwargs["transmitted_iq"])) > 0
        calls.append(("receiver",mode))
        return {"pass":True, "bit_error_rate":0.0}
    result = lab.run_case("qpsk","static",30.,3,2.,"ideal",
                          channel_class=Channel,receiver=receive)
    assert [item[0] for item in calls] == ["init","air","receiver"]
    assert result["simulation_pass"] is True
    assert result["receiver"]["bit_error_rate"] == 0
    assert not result["physical_tx"]
    assert not result["hardware_qualified"]
    assert not result["decoded_in_k6_firmware"]
    assert result["reference_assisted_receiver"]


def test_bad_channel_and_invalid_parameters_fail_cleanly():
    class Broken:
        def __init__(self,*args,**kwargs): pass
        def apply(self,x,**kwargs): return x[:4]
    with pytest.raises(ValueError,match="invalid IQ"):
        lab.run_case("qpsk","static",30.,0,0.,channel_class=Broken)
    with pytest.raises(ValueError,match="CFO"):
        lab.run_case("qpsk","static",30.,0,float("nan"))
    with pytest.raises(ValueError,match="SNR"):
        lab.run_case("qpsk","static",float("inf"),0,0.)


def test_main_preserves_negative_decode_results_and_harness_errors(tmp_path,monkeypatch):
    def fake_case(mode,profile,snr_db,seed,cfo_hz,**kw):
        if profile=="fixed":
            return {"status":"pending","simulation_pass":False,"physical_tx":False}
        return {"status":"pending","simulation_pass":True,"physical_tx":False}
    monkeypatch.setattr(lab,"run_case",fake_case)
    out=tmp_path/"results.json"
    assert lab.main(["--modes","qpsk","--profiles","static,fixed",
                     "--snr-db","30","--json",str(out)])==0
    data=json.loads(out.read_text())
    assert data["decoded_count"]==1
    assert data["decode_failed_count"]==1
    assert data["harness_error_count"]==0
    assert data["hardware_qualified"] is False
    with pytest.raises(SystemExit):
        lab.main(["--profiles","static,static","--json",str(out)])
    with pytest.raises(SystemExit):
        lab.main(["--modes","qpsk,ofdm","--profiles","mobile-urban",
                  "--snr-db",",".join(str(x) for x in range(30)),
                  "--seeds",",".join(str(x) for x in range(10)),
                  "--json",str(out)])


def test_actual_rfsim_to_soapyfang_static_qpsk_smoke():
    pytest.importorskip("rfsim.session")
    result=lab.run_case("qpsk","static",30.,0,0.,tx_model="ideal")
    assert result["simulation_pass"],result
    assert result["receiver"]["bit_error_rate"]==0.0
    assert result["receiver"]["received_samples"]==result["receiver"]["expected_samples"]
    assert result["receiver"]["source"]=="SigMF RX-only IQ replay"
