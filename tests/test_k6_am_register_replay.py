"""Offline true-AM hypothesis using bounded driver6 writes and SoapyFang IQ.

No transmitter, physical PA control or radio hardware accessible in this test.
"""
import json
import math
import struct
import numpy as np
import pytest
from sim.k6emu.__main__ import APP_BASE,RAM_BASE,K6Board
from sim.k6emu.rf.am_trace import (
    schedule_driver_am,tone_quality,spectral_quality,percent_modulation)
from sim.k6emu.rf.tx_trace_replay import replay_envelope,ReplayAssumptions
from tools.k6_am_register_replay import run,main
from tools.k6_soapyfang_modlab import fixture,RATE


def _desired():
    signal,_=fixture('am')
    a=np.abs(signal)
    return (a/a.max()).astype(float)


def test_guarded_synthetic_gain_is_downward_only_and_serialized():
    for max_gain in (0x8,0x22):
        command=schedule_driver_am(_desired(),gain_ceiling=max_gain,write_us=117)
        assert command[0]==(0,0x30,0)
        assert command[1][1]==0x36
        assert command[2][1:]==(0x30,8)
        assert command[-1][1:]==(0x30,0)
        times=[x[0] for x in command]
        assert all((n-p)>=6*2000 for p,n in zip(times,times[1:]))
        assert len(command)>1000
        assert all(0<=val<=max_gain for _,reg,val in command if reg==0x36)
        assert all(val in (0,8) for _,reg,val in command if reg==0x30)
        assert all(reg in (0x30,0x36) for _,reg,val in command)


def test_end_of_waveform_keyup_is_not_squeezed_into_partial_spi_slot():
    a=np.full(125,.5)
    command=schedule_driver_am(a,write_us=117)
    stop=command[-1][0]/(96000000/48000)
    assert stop>=len(a)
    assert stop-len(a)<=6
    assert command[-1][1:]==(0x30,0)
    assert all((n[0]-p[0])>=12000 for p,n in zip(command,command[1:]))


def test_replay_never_claims_am_from_ungated_register_gain():
    writes=schedule_driver_am(_desired(),write_us=117)
    gated,_=replay_envelope(writes,12000+480)
    off_writes=[w for w in writes if w[1]!=0x30 or w[2]==0]
    silent,meta=replay_envelope(off_writes,12000+480)
    assert gated.max()==pytest.approx(0x22/63,abs=.03)
    assert np.all(silent==0)
    assert meta['physical_rf'] is False
    assert np.all(gated[12000+6:]==0)


def test_captured_bounded_3wire_write_stream_matches_replayed_register_intent(tmp_path):
    image=tmp_path/'noop.bin'
    image.write_bytes(struct.pack('<IIH',RAM_BASE+24*1024,APP_BASE+9,0xe7fe))
    commands=schedule_driver_am(_desired(),write_us=117)
    board=K6Board(image,capture_tx_events=True)
    try:
        for cycles,reg,val in commands:
            board.cycles=cycles
            board._fd_pin_change(8,0)
            raw=(reg<<16)|val
            for bit in range(23,-1,-1):
                out=((raw>>bit)&1)<<6
                board._fd_pin_change(out,out|32)
            board._fd_pin_change(0,8)
        assert board.fd_write_events==commands
        replayed,meta=replay_envelope(board.fd_write_events,12000+480)
        assert replayed[:12].sum()==0
        assert np.any(replayed[25:12000]>0)
        assert np.all(replayed[12000+6:]==0)
        assert meta['applied_events']==len(commands)
        assert meta['rf_transfer_calibrated'] is False
    finally:board.close_rf()


def test_ideal_am_spectral_tone_and_overmodulation_metrics():
    ideal,_=fixture('am')
    envelope=np.abs(ideal)
    q=tone_quality(envelope)
    spec=spectral_quality(ideal)
    assert q['depth']==pytest.approx(.7,abs=.001)
    assert q['thd_percent']<.001
    assert abs(spec['sideband_asymmetry_db'])<.001
    assert spec['phase_rms_degrees']<1e-6
    clipped=np.minimum(envelope,1.2)
    assert tone_quality(clipped)['thd_percent']>10


@pytest.mark.parametrize('us,expected',[(5,True),(35,True),(117,True),(250,False),(1000,False)])
def test_write_rate_sensitivity_and_soapyfang_filtered_am(us,expected):
    r=run(write_us=us)
    assert r['simulated_am_shape_pass'] is expected,r
    assert r['simulated_am_spectral_pass'] is expected
    assert r['replay']['total_events']==r['command_count']
    assert not r['physical_rf'] and not r['hardware_am_qualified']
    assert r['post_key_up_rms']<.02
    assert r['soapyfang_iq_reference_lag_samples']>=0
    if expected:
        assert abs(r['soapyfang_envelope']['depth']-.7)<.055
        assert r['soapyfang_envelope']['thd_percent']<12


def test_schematic_analog_pa_lag_blocks_am_despite_fast_register_clock():
    r=run(write_us=117,attack_ms=.5,release_ms=.765)
    assert not r['simulated_am_shape_pass']
    assert not r['simulated_am_spectral_pass']
    assert r['soapyfang_envelope']['depth']<.25
    assert r['post_key_up_rms']>.02


def test_am_pm_phase_distortion_hidden_from_envelope_but_not_rf_check():
    clean=run(write_us=117)
    coupled=run(write_us=117,carrier_phase_coupling_deg=60)
    assert coupled['simulated_am_shape_pass']
    assert not coupled['simulated_am_spectral_pass']
    assert coupled['soapyfang_spectrum']['phase_rms_degrees']>8
    assert clean['soapyfang_spectrum']['phase_rms_degrees']<2


@pytest.mark.parametrize('bad',[
    lambda: schedule_driver_am(np.ones(120),gain_ceiling=0x23),
    lambda: schedule_driver_am(np.ones(120),gain_ceiling=0),
    lambda: schedule_driver_am(np.ones(120)*1.01),
    lambda: schedule_driver_am(np.ones(120)*-1),
    lambda: schedule_driver_am(np.array([np.nan]*120)),
    lambda: schedule_driver_am(np.ones(120),write_us=-1),
    lambda: schedule_driver_am(np.ones(120),gate_mask=3),
    lambda: schedule_driver_am(np.ones(10)),
    lambda: run(carrier_phase_coupling_deg=240),
])
def test_guarded_models_reject_bad_configuration(bad):
    with pytest.raises(ValueError):bad()


def test_cli_passes_good_hypothesis_and_writes_failed_diagnostic(tmp_path):
    report=tmp_path/'am.json'
    assert main(['--write-us','117','--json',str(report)])==0
    a=json.loads(report.read_text())
    assert a['simulated_am_spectral_pass'] and not a['physical_rf']
    assert a['gain_max_code']==34
    assert main(['--write-us','250','--json',str(report)])==1
    fail=json.loads(report.read_text())
    assert not fail['simulated_am_shape_pass']
    assert fail['soapyfang_envelope']['thd_percent']>20
    assert main(['--am-pm-degrees','60','--json',str(report)])==1
    phase=json.loads(report.read_text())
    assert phase['simulated_am_shape_pass'] and not phase['simulated_am_spectral_pass']
    with pytest.raises(SystemExit):main(['--ceiling','0x30'])
