"""Polar DQPSK RFSim smoke and evidence-separation tests; no real radio."""
import json
import numpy as np
import pytest

from tools import k6_rfsim_dqpsk_sweep as lab


def test_polar_case_preserves_simulation_provenance_and_channel_order():
    seen=[]
    class Channel:
        def __init__(self,rate,profile,**kwargs):
            seen.append(("init",profile,rate))
        def apply(self,iq,**kwargs):
            seen.append(("propagate",len(iq),kwargs["snr_db"]))
            return iq
    def rx(iq,**kwargs):
        seen.append(("soapy",kwargs["snr_db"]))
        return iq
    def decode(source,iq):
        seen.append(("decode",len(iq)))
        return dict(payload_ber=0.,sync_errors=0,
                    signed_frequency_mapping_valid=True,
                    phase_level_span_degrees=90.)
    result=lab.run_case(channel_class=Channel,receiver=rx,decoder=decode)
    assert [x[0] for x in seen] == ["init","propagate","soapy","decode"]
    assert result["simulation_pass"]
    assert result["modeled_register_writes"]>0
    assert result["transmit_evidence"].startswith("Unverified")
    assert result["physical_tx"] is False
    assert result["hardware_qualified"] is False
    assert result["decoded_in_K6_firmware"] is False


def test_signed_frequency_polarity_and_symbol_update_are_independent_gates():
    class Channel:
        def __init__(self,*args,**kwargs): pass
        def apply(self,iq,**kwargs): return iq
    def identity(iq,**kwargs): return iq
    def wrong_sign(source,iq):
        return dict(payload_ber=0.,sync_errors=0,
                    signed_frequency_mapping_valid=False,
                    phase_level_span_degrees=90.)
    r=lab.run_case(channel_class=Channel,receiver=identity,decoder=wrong_sign)
    assert r["receiver"]["payload_ber"]==0
    assert not r["simulation_pass"]


def test_invalid_parameters_and_bad_channel_are_errors():
    with pytest.raises(ValueError,match="invalid SNR"):
        lab.run_case(snr_db=float("nan"))
    with pytest.raises(ValueError,match="unknown RFSim profile"):
        lab.run_case(profile="moon")
    class BadChannel:
        def __init__(self,*args,**kwargs): pass
        def apply(self,iq,**kwargs): return np.array([complex(np.nan,0)])
    with pytest.raises(ValueError,match="invalid IQ"):
        lab.run_case(channel_class=BadChannel)


def test_decode_failures_are_not_harness_failures(tmp_path,monkeypatch):
    def fake_run_case(**kwargs):
        if kwargs["baud"]==100:
            raise lab.PilotLockError("pilot not acquired")
        return dict(simulation_pass=True,receiver={"payload_ber":0.},
                    physical_tx=False,hardware_qualified=False)
    monkeypatch.setattr(lab,"run_case",fake_run_case)
    out=tmp_path/"dqpsk.json"
    assert lab.main(["--bauds","50,100","--profiles","static",
                     "--snr-db","30","--json",str(out)])==0
    report=json.loads(out.read_text())
    assert report["decoded_count"]==1
    assert report["decode_failed_count"]==1
    assert report["harness_error_count"]==0
    assert report["cases"][1]["reason"]=="pilot not acquired"
    with pytest.raises(SystemExit):
        lab.main(["--bauds","100,100","--json",str(out)])


def test_rfsim_polar_real_soapy_static_nominal():
    pytest.importorskip("rfsim.session")
    result=lab.run_case(baud=100,bandwidth_hz=135.,dac_bits=7,
                        profile="static",snr_db=30.,seed=42)
    assert result["simulation_pass"],result
    assert result["receiver"]["payload_ber"]==0.
    assert result["receiver"]["sync_errors"]==0
