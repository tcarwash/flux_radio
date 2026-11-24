"""Offline frequency-path modulation: no hardware, no radio TX or flash."""
from dataclasses import replace
import json
import math
import numpy as np
import pytest
from sim.k6emu.rf.frequency_modem import RATE,PATHS,Settings,frame,build
from tools.k6_frequency_modem_lab import fm_discriminator,rx_soapy,decode_pilot,run,main


def test_finite_public_pilot_sync_and_private_heldout_payload():
    for path in PATHS:
        setting=Settings(path=path,baud=4800 if path in ('native_fsk','reg40_per_frame') else 300)
        levels,n_pilot,n_sync=frame(setting)
        assert n_pilot in (32,64)
        assert n_sync==16
        assert len(levels)==n_pilot+n_sync+128
        assert np.array_equal(levels[:n_pilot],np.tile(np.arange(4 if path=='signed_dac' else 2),16))
        assert np.all((levels>=0)&(levels<(4 if path=='signed_dac' else 2)))
        assert np.array_equal(levels,frame(setting)[0])
        assert not np.array_equal(levels[-128:],frame(replace(setting,seed=72))[0][-128:])


@pytest.mark.parametrize('kwargs',[dict(path='unknown'),dict(baud=0),dict(baud=299),
                                   dict(baud=6000),dict(write_us=0),dict(write_us=float('nan')),
                                   dict(bandwidth_hz=-1),dict(deviation_hz=0),
                                   dict(path='native_fsk',baud=300)])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):Settings(**kwargs).validate()


def test_native_fifo_is_a_hardware_symbol_clock_not_spi_per_symbol():
    settings=Settings()
    model=build(settings)
    assert model['symbol_rate']==4800 and model['order']==2
    assert len(model['writes'])==1  # setup only, not 224 writes
    assert model['missed_register_updates']==0
    assert not model['physical_rf'] and not model['hardware_qualified']
    assert len(model['iq'])==(len(model['source_levels'])+2)*(RATE//4800)
    assert np.max(np.abs(np.abs(model['iq'])-1))<1e-6
    # phase evolves by integrating finite instantaneous FM (continuous phase)
    dphi=np.angle(model['iq'][1:]*model['iq'][:-1].conj())
    assert np.max(np.abs(dphi))<.5


def test_reg40_model_is_strictly_per_frame_and_unqualified():
    model=build(Settings(path='reg40_per_frame'))
    native=build(Settings(path='native_fsk'))
    assert model['writes']==[(0,0x40,0x0040)]
    assert len(model['writes'])==1
    assert np.max(np.abs(model['frequency_hz']))==pytest.approx(1200)
    assert np.max(np.abs(native['frequency_hz']))==pytest.approx(1500)
    assert not model['hardware_qualified']


def test_sub_audio_and_signed_register_completion_bound_and_mapping():
    for path in ('subaudio','signed_dac'):
        m=build(Settings(path=path,baud=300,bandwidth_hz=350))
        assert all(reg==0x51 for t,reg,word in m['writes'])
        assert min(t for t,reg,word in m['writes'])>=1
        assert all(0<=word<=0xffff and word&0x8000 for t,reg,word in m['writes'])
        assert all(t2>t1 for (t1,_,_),(t2,_,_) in zip(m['writes'],m['writes'][1:]))
        assert m['missed_register_updates']==0
        assert len(m['writes'])<len(m['source_levels'])
    signed=build(Settings(path='signed_dac',baud=300))
    words=[e[2] for e in signed['writes']]
    assert any(w&0x2000 for w in words) and any(not w&0x2000 for w in words)
    assert {w&0x7f for w in words}=={42,127}


def test_serial_bus_deadline_drops_unavailable_symbol_updates():
    s=Settings(path='signed_dac',baud=1200,write_us=1000,bandwidth_hz=350)
    model=build(s)
    assert model['missed_register_updates']>50
    assert len(model['writes'])<len(model['source_levels'])
    assert model['phase_continuous'] and not model['hardware_qualified']


@pytest.mark.parametrize('settings',[
    Settings(path='native_fsk'),
    Settings(path='reg40_per_frame'),
    Settings(path='subaudio',baud=150,bandwidth_hz=135),
    Settings(path='subaudio',baud=300,bandwidth_hz=135),
    Settings(path='signed_dac',baud=300,bandwidth_hz=350),
])
def test_soapyfang_filtered_iq_pilot_and_heldout_payload_decode(settings):
    r=run(settings,snr_db=30)
    assert r['pass_frame'],r
    assert r['payload_ber']==0 and r['sync_errors']==0
    assert r['pilot_errors']==0 and r['level_order_valid']
    assert r['pilot_eye_hz']>100
    assert r['missed_register_updates']==0
    assert r['samples']>1000
    assert not r['physical_rf'] and not r['hardware_qualified']
    assert r['simulator'].startswith('SoapyFang SigMF')


def test_signed_polarity_negative_control_not_masked_by_pilot_oracle():
    s=Settings(path='signed_dac',baud=300,bandwidth_hz=350)
    good=run(s,snr_db=30)
    negative=run(s,snr_db=30,wrong_signed_order=True)
    assert good['pass_frame'] and good['level_order_valid']
    # The pilot successfully *learns* inverted means (so BER can be zero).
    # Qualification independently rejects undocumented signed physical mapping.
    assert negative['payload_ber']==0
    assert not negative['level_order_valid'] and not negative['pass_frame']
    assert negative['level_means_hz'][0]>negative['level_means_hz'][-1]


def test_subaudio_bandwidth_limited_rate_and_four_level_eye_fail():
    good=run(Settings(path='subaudio',baud=300,bandwidth_hz=135),snr_db=30)
    fast=run(Settings(path='subaudio',baud=600,bandwidth_hz=135),snr_db=30)
    wide=run(Settings(path='signed_dac',baud=300,bandwidth_hz=350),snr_db=30)
    narrow=run(Settings(path='signed_dac',baud=300,bandwidth_hz=135),snr_db=30)
    assert good['pass_frame'] and wide['pass_frame']
    assert not fast['pass_frame'] and fast['payload_ber']>0
    assert not narrow['pass_frame'] and narrow['payload_ber']>.05


def test_slow_spi_register_updates_cannot_cheat_1200_baud_four_level():
    s=Settings(path='signed_dac',baud=1200,write_us=1000,bandwidth_hz=350)
    r=run(s,snr_db=30)
    assert r['missed_register_updates']>50
    assert not r['pass_frame']


def test_deterministic_sdr_receive_and_input_validation():
    s=Settings(path='native_fsk')
    x=build(s)
    first=rx_soapy(x['iq'],snr_db=30)
    again=rx_soapy(x['iq'],snr_db=30)
    assert len(first)==len(x['iq'])
    np.testing.assert_array_equal(first,again)
    assert np.array_equal(fm_discriminator(first),fm_discriminator(again))
    with pytest.raises(ValueError):rx_soapy(x['iq'],snr_db=float('nan'))
    with pytest.raises(ValueError):fm_discriminator(np.ones(1,dtype=complex))


def test_cli_serializable_pass_and_negative_report(tmp_path):
    path=tmp_path/'native.json'
    assert main(['--path','native_fsk','--json',str(path)])==0
    a=json.loads(path.read_text())
    assert a['pass_frame'] and a['payload_ber']==0
    assert not a['hardware_qualified'] and not a['physical_rf']
    path=tmp_path/'negative.json'
    assert main(['--path','signed_dac','--baud','300','--bandwidth-hz','350',
                 '--wrong-signed-order','--json',str(path)])==1
    negative=json.loads(path.read_text())
    assert not negative['pass_frame'] and negative['payload_ber']==0
    with pytest.raises(SystemExit):main(['--path','signed_dac','--baud','299'])
