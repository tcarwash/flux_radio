"""Native FIFO RX behavior using actual SoapyFang registers, not a bit oracle."""
import json
import numpy as np
import pytest

from tools.k6_frequency_fifo_replay import (
    MARKER,RATE,native_burst,replay,main,
)

DATA=np.random.default_rng(712).integers(0,256,64,dtype=np.uint8).tobytes()


@pytest.mark.parametrize('lead,polarity,cfo',[(0,1,0),(13,-1,230),(5231,1,-500),
                                            (9777,-1,700),(22000,1,1300),
                                            (1459,-1,-1300)])
def test_unknown_burst_start_cfo_and_polarity_read_fully_from_fsk_fifo(lead,polarity,cfo):
    r=replay(DATA,lead_samples=lead,polarity=polarity,offset_hz=cfo)
    assert r['pass_exact'],r
    assert r['recovered_payload_hex']==DATA.hex()
    assert r['recovered_bytes']==len(DATA)
    assert r['frames']==r['sync_events']==r['done_events']==1
    assert r['overruns']==r['underflows']==r['gaps']==0
    assert r['full_events']>0
    assert r['status'] is None
    assert r['rf_hardware'] is False and r['firmware_tx'] is False


@pytest.mark.parametrize('interval',[1.,5.,15.])
def test_fifo_service_before_overflow(interval):
    r=replay(DATA,poll_ms=interval)
    assert r['pass_exact']
    assert r['overruns']==0


def test_slow_poll_corrupts_fifo_not_silently_accepted():
    r=replay(DATA,poll_ms=25.)
    assert not r['pass_exact']
    assert r['frames']==1 and r['done_events']==1
    assert r['overruns']>0
    assert r['recovered_payload_hex'] != DATA.hex()


def test_wrong_sync_and_noise_only_cannot_generate_valid_frame():
    wrong=replay(DATA,marker=MARKER[:-4]+b'\x00'*4)
    assert not wrong['pass_exact']
    assert wrong['frames']==wrong['sync_events']==0
    noise=replay(DATA,tx_amplitude=0.)
    assert not noise['pass_exact']
    assert noise['frames']==noise['sync_events']==0


def test_induced_payload_fault_is_detected_by_external_truth_comparison():
    r=replay(DATA,flip_payload_bit=11)
    # This particular RFIC model has no CRC enabled. It must report frame
    # completion, but independent known-payload comparison catches corruption.
    assert not r['pass_exact']
    assert r['frames']==1
    assert r['overruns']==0
    assert r['recovered_payload_hex']!=DATA.hex()


def test_parameter_guards_and_deterministic_waveform():
    a=native_burst(DATA,lead_samples=13)
    b=native_burst(DATA,lead_samples=13)
    np.testing.assert_array_equal(a,b)
    with pytest.raises(ValueError):native_burst(DATA+b'\x01')
    with pytest.raises(ValueError):native_burst(DATA,marker=b'\x55'*19)
    with pytest.raises(ValueError):native_burst(DATA,tx_amplitude=2.)
    with pytest.raises(ValueError):native_burst(DATA,flip_payload_bit=9999)
    with pytest.raises(ValueError):replay(DATA,poll_ms=0)


def test_json_runner_and_fail_closed_report(tmp_path):
    out=tmp_path/'burst.json'
    assert main(['--lead-samples','9777','--polarity','-1','--json',str(out)])==0
    result=json.loads(out.read_text())
    assert result['pass_exact'] and result['frames']==1
    assert result['recovered_bytes']==64
    assert result['rf_hardware'] is False
    assert main(['--poll-ms','25','--json',str(out)])==1
    bad=json.loads(out.read_text())
    assert not bad['pass_exact'] and bad['overruns']>0
    with pytest.raises(SystemExit):main(['--payload-bytes','63'])
