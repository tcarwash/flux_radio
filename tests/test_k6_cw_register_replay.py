"""Model-only register command replay, costed at one 3-wire write per slot."""
import json
import math
import struct
from pathlib import Path
import numpy as np
import pytest
from sim.k6emu.__main__ import K6Board, APP_BASE, RAM_BASE
from sim.k6emu.rf.tx_trace_replay import (
    ReplayAssumptions,carrier_iq,replay_envelope,schedule_cw_envelope)
from tools.k6_soapyfang_modlab import fixture
from tools.k6_cw_register_replay import main,run


def test_register_gain_requires_gate_evidence():
    n=200
    gain_only=[(0,0x36,63),(96000,0x36,33)]
    a,p=replay_envelope(gain_only,n)
    assert np.count_nonzero(a)==0
    assert p['gated_samples']==0 and not p['physical_rf']
    # Register reconfiguration is not arbitrary 2FSK IQ: without an RF
    # gate indication the model cannot manufacture an RF transmission.
    assert np.count_nonzero(replay_envelope([(0,0x70,0x8000),
                                            (96000,0x72,0xc194)],n)[0])==0


def test_causal_order_one_sample_and_last_write_wins():
    c=96_000_000
    n=60
    # One register write completes at 1.2 sample periods: sample 2 first.
    events=[(0,0x36,63),(0,0x30,8),
            (2400,0x36,31),
            (16000,0x30,0),
            (1_000_000,0x30,8)]
    y,p=replay_envelope(events,n)
    assert y[0]==1.
    assert y[1]==1.
    assert y[2]==pytest.approx(31/63)
    assert not y[8:].any()
    assert p['after_end_events']==1
    assert p['applied_events']==4
    assert len(y)==n


def test_no_unrequested_spurs_or_overshoot_with_schematic_analog_tail():
    ev=[(0,0x36,63),(0,0x30,8),(96000,0x30,0)]
    immediate,_=replay_envelope(ev,400)
    delayed,p=replay_envelope(ev,400,options=ReplayAssumptions(
        attack_ms=3.0,release_ms=1.0))
    assert np.all(immediate[:48]==1) and np.all(immediate[48:]==0)
    assert 0<=delayed.min()<=delayed.max()<=1
    assert delayed[48]>0
    assert delayed[200]<delayed[48]
    assert p['rf_transfer_calibrated'] is False


def test_two_analog_gain_components_fail_closed_without_both_controls():
    events=[(0,0x36,63),(0,0x30,8)]
    y,_=replay_envelope(events,64,options=ReplayAssumptions(gain_source='product'))
    assert not y.any()  # Unknown REG19 bias must not imply gain 1
    events.append((128000,0x19,15))
    z,_=replay_envelope(events,100,options=ReplayAssumptions(gain_source='product'))
    assert not z[:64].any() and np.all(z[64:]==1)


def test_cw_synthetic_scheduler_emits_serial_transaction_timestamps():
    target,_=fixture('cw')
    for write_us in (5,35,117,250,1000):
        ev=schedule_cw_envelope(np.abs(target),write_us=write_us)
        cycles=[e[0] for e in ev]
        period=math.ceil(48000*write_us/1e6)
        assert cycles[0]==0
        assert all((n-p)>=period*2000 for p,n in zip(cycles,cycles[1:]))
        assert ev[0][1:]==(0x30,0)
        gates=[e for e in ev if e[1]==0x30]
        assert gates[-1][2]==0  # gain cleanup may follow gate-off
        r,p=replay_envelope(ev,len(target))
        assert np.all((r>=0)&(r<=1))
        assert p['applied_events']>0
        assert not p['physical_rf']


def test_real_virtual_3wire_bus_capture_can_feed_same_replay(tmp_path):
    # This is K6Board with a tiny static test image, never a radio flash.
    image=tmp_path/'loop.bin'
    image.write_bytes(struct.pack('<IIH',RAM_BASE+24*1024,APP_BASE+9,0xe7fe))
    board=K6Board(image,capture_tx_events=True)
    try:
        for cycles,reg,value in ((0,0x30,0),(24000,0x36,63),
                                 (48000,0x30,8),(96000,0x30,0)):
            board.cycles=cycles
            board._fd_pin_change(8,0)
            packet=reg<<16|value
            for bit in range(23,-1,-1):
                odr=((packet>>bit)&1)<<6
                board._fd_pin_change(odr,odr|32)
            board._fd_pin_change(0,8)
        expected=[(0,0x30,0),(24000,0x36,63),(48000,0x30,8),(96000,0x30,0)]
        assert board.fd_write_events==expected
        env,meta=replay_envelope(board.fd_write_events,100)
        assert np.all(env[:24]==0)
        assert np.all(env[24:48]==1)
        assert np.all(env[48:]==0)
        assert meta['total_events']==4
    finally:board.close_rf()


def test_cw_strict_positive_and_stress_limits_report_negative_results(tmp_path):
    r=run(write_us=117,snr_db=30)
    assert r['pass_strict_shape']
    assert r['soapyfang']['pass']
    assert r['soapyfang']['rf_model']=='SoapyFang ChipModel'
    assert not r['physical_rf'] and not r['firmware_transmission']
    assert r['modeled']['max_amplitude_error']<.1
    slow=run(write_us=1000,snr_db=30)
    assert not slow['pass_strict_shape']
    assert slow['modeled']['max_amplitude_error']>.1
    assert slow['modeled']['leakage_ratio_relative_to_ideal']>r['modeled']['leakage_ratio_relative_to_ideal']
    out=tmp_path/'cw.json'
    assert main(['--write-us','117','--json',str(out)])==0
    d=json.loads(out.read_text())
    assert d['pass_strict_shape'] and not d['physical_rf']
    assert main(['--write-us','1000','--json',str(out)])==1
    d=json.loads(out.read_text())
    assert not d['pass_strict_shape']


def test_external_trace_gate_assumption_must_be_explicit(tmp_path):
    doc=tmp_path/'trace.json';doc.write_text(json.dumps(dict(writes=[
        [0,0x36,63],[0,0x30,8],[200000,0x30,0]])))
    with pytest.raises(SystemExit) as e:
        main(['--trace',str(doc)])
    assert e.value.code==2
    with pytest.raises(SystemExit) as e:
        main(['--trace',str(doc),'--gate-mask','0x3'])
    assert e.value.code==2


@pytest.mark.parametrize('bad',[ReplayAssumptions(gate_mask=0),
                                ReplayAssumptions(gate_mask=3),
                                ReplayAssumptions(sample_rate=0),
                                ReplayAssumptions(attack_ms=float('nan'))])
def test_replay_assumptions_fail_closed(bad):
    with pytest.raises(ValueError):replay_envelope([],20,options=bad)


def test_end_to_end_emulator_board_bitbang_capture_to_soapyfang(tmp_path):
    """Synthetic bus sequence is captured by real K6Board decode, then replayed.

    The 24-bit values below are *not* resident firmware actions. They are
    test-bench traffic driven into a virtual board with no physical radio.
    """
    signal,_=fixture('cw')
    commands=schedule_cw_envelope(np.abs(signal),write_us=117)
    path=tmp_path/'safe_loop.bin'
    path.write_bytes(struct.pack('<IIH',RAM_BASE+24*1024,APP_BASE+9,0xe7fe))
    board=K6Board(path,capture_tx_events=True)
    try:
        for cycles,reg,value in commands:
            board.cycles=cycles
            board._fd_pin_change(8,0)
            packet=(reg<<16)|value
            for bit in range(23,-1,-1):
                odr=((packet>>bit)&1)<<6
                board._fd_pin_change(odr,odr|32)
            board._fd_pin_change(0,8)
        assert len(board.fd_write_events)==len(commands)
        assert board.fd_write_events==commands
        result=run(trace={'clock_hz':96000000,'writes':board.fd_write_events},
                   gate_mask=8,snr_db=30)
        assert result['pass_strict_shape']
        assert result['source']=='external_K6Board_register_write_trace'
        assert result['command_meta']['applied_events']>500
        assert result['soapyfang']['key_sample_accuracy']>.999
        assert result['soapyfang']['received_samples']==len(signal)
        assert result['physical_rf'] is False
    finally:board.close_rf()
