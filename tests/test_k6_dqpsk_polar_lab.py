"""Offline signed-FM integral DQPSK versus SoapyFang, never radio TX."""
import json
import math
import numpy as np
import pytest
from sim.modulation.dqpsk_polar import Parameters,public_frame,synthesize,RATE
from tools.k6_dqpsk_polar_lab import run,main


def test_public_pilot_sync_private_seeded_payload():
    settings=Parameters(baud=100)
    a,n,s=public_frame(settings)
    b,_,_=public_frame(settings)
    c,_,_=public_frame(Parameters(baud=100,seed=43))
    assert n==64 and s==16 and len(a)==208
    assert np.array_equal(a[:n],np.tile([0,1,3,2],16))
    assert np.array_equal(a[n:n+s],np.tile([3,0,2,1],4))
    assert np.array_equal(a,b)
    assert not np.array_equal(a[n+s:],c[n+s:])


@pytest.mark.parametrize('baud', [50,75,100,150])
def test_narrow_subaudio_model_recovers_low_rate_differential_phase(baud):
    r=run(Parameters(baud=baud,analog_bandwidth_hz=135),snr_db=30,cfo_hz=25)
    assert r['simulated_decode_pass'],r
    assert r['payload_bit_errors']==r['sync_errors']==0
    assert r['iq_constant_envelope']
    assert not r['physical_tx'] and not r['hardware_qualified'] and not r['stock_k6_supported']
    assert r['register_writes']>100
    assert r['dropped_register_writes']==0


@pytest.mark.parametrize('baud', [200,300,400,600,1200])
def test_135hz_subaudio_model_is_not_a_high_rate_qpsk_transmitter(baud):
    r=run(Parameters(baud=baud,analog_bandwidth_hz=135),snr_db=30)
    assert not r['simulated_decode_pass'],r
    assert r['payload_ber']>.005


def test_300_baud_requires_hypothetically_wider_dac_bandwidth():
    narrow=run(Parameters(baud=300,analog_bandwidth_hz=135))
    wide=run(Parameters(baud=300,analog_bandwidth_hz=350))
    assert not narrow['simulated_decode_pass'] and narrow['payload_ber']>.03
    assert wide['simulated_decode_pass'] and wide['payload_ber']==0
    assert wide['true_linear_qpsk'] is False


@pytest.mark.parametrize('cfo',[-80,-25,0,25,80])
def test_phase_increment_pilot_removes_constant_carrier_offset(cfo):
    r=run(Parameters(baud=100),cfo_hz=cfo)
    assert r['simulated_decode_pass'],r
    assert r['acquisition']=='known_frame_start_pilot_only_phase_centroids'
    assert r['payload_ber']==0


def test_rx_pilot_relabel_cannot_hide_reversed_physical_frequency_sign():
    normal=run(Parameters(baud=100))
    flipped=run(Parameters(baud=100),reverse_sign=True)
    assert normal['simulated_decode_pass']
    assert flipped['payload_ber']==0 and flipped['sync_errors']==0
    assert not flipped['signed_frequency_mapping_valid']
    assert not flipped['simulated_decode_pass']


def test_no_hardware_deviation_updates_not_misclassified_as_qpsk():
    r=run(Parameters(baud=100),disable_updates=True)
    assert r['register_writes']==1
    assert not r['simulated_decode_pass']
    assert r['payload_ber']>.4
    assert not r['signed_frequency_mapping_valid']


@pytest.mark.parametrize('write_us,expected',[(5,True),(117,True),(250,True),(1000,False),(5000,False),(15000,False)])
def test_register_completion_time_bound_and_waveform_fidelity(write_us,expected):
    r=run(Parameters(baud=100,spi_write_us=write_us))
    assert r['simulated_decode_pass'] is expected,r
    if write_us>=15000:assert r['dropped_register_writes']>0
    if write_us<=1000:assert r['dropped_register_writes']==0


def test_register_writes_are_one_per_symbol_or_less_and_nonoverlapping():
    for baud in (50,100,150,300):
        params=Parameters(baud=baud)
        model=synthesize(params)
        events=model['writes']
        n=RATE//baud
        period=math.ceil(params.spi_write_us*RATE/1e6)
        assert len(events)<=len(model['symbols'])+1
        assert all((b[0]-a[0])>=period for a,b in zip(events,events[1:]))
        assert all(reg==0x51 for _,reg,value in events)
        assert all(0<=value<=0xffff and (value&0x8000) for _,reg,value in events)
        assert np.all(np.isfinite(model['iq']))
        assert np.max(np.abs(np.abs(model['iq'])-1))<1e-6
        assert np.max(np.abs(model['instantaneous_frequency_hz']))<params.max_deviation_hz
        assert not model['physical_tx'] and not model['hardware_qualified']


def test_dac_quantization_can_block_lower_rate_or_coarse_resolution():
    low=run(Parameters(baud=25))
    coarse=run(Parameters(baud=150,dac_bits=4))
    assert not low['simulated_decode_pass'] and low['payload_ber']>.1
    assert not coarse['simulated_decode_pass'] and coarse['payload_ber']>.25


def test_bad_parameter_rejection_and_non_aliasing_deviation():
    invalid=[dict(baud=13),dict(baud=0),dict(baud=1300),dict(dac_bits=2),
             dict(spi_write_us=0),dict(spi_write_us=float('nan')),
             dict(analog_bandwidth_hz=-1),dict(max_deviation_hz=5)]
    for item in invalid:
        with pytest.raises(ValueError):synthesize(Parameters(**item))
    with pytest.raises(ValueError):synthesize(Parameters(baud=1200,max_deviation_hz=100))
    with pytest.raises(ValueError):run(Parameters(),snr_db=float('nan'))
    with pytest.raises(ValueError):run(Parameters(),cfo_hz=300)
    with pytest.raises(ValueError):public_frame(Parameters(),payload_symbols=0)


def test_cli_pass_and_fail_both_write_provenance_json(tmp_path):
    path=tmp_path/'polar.json'
    assert main(['--baud','100','--json',str(path)])==0
    good=json.loads(path.read_text())
    assert good['simulated_decode_pass'] and not good['physical_tx']
    assert good['register_writes']>100
    assert main(['--baud','300','--json',str(path)])==1
    bad=json.loads(path.read_text())
    assert bad['payload_ber']>.05 and not bad['simulated_decode_pass']
    assert main(['--baud','100','--reverse-sign','--json',str(path)])==1
    reversed_report=json.loads(path.read_text())
    assert reversed_report['payload_ber']==0
    assert not reversed_report['signed_frequency_mapping_valid']
    with pytest.raises(SystemExit):main(['--baud','13'])


def test_virtual_three_wire_captures_only_schematic_signed_fm_register_intent(tmp_path):
    import struct
    from sim.k6emu.__main__ import APP_BASE,RAM_BASE,K6Board
    model=synthesize(Parameters(baud=100))
    path=tmp_path/'safe_loop.bin'
    path.write_bytes(struct.pack('<IIH',RAM_BASE+24*1024,APP_BASE+9,0xe7fe))
    board=K6Board(path,capture_tx_events=True)
    try:
        for samples,register,value in model['writes']:
            board.cycles=samples*(96_000_000//RATE)
            board._fd_pin_change(8,0)
            packet=(register<<16)|value
            for shift in range(23,-1,-1):
                odr=((packet>>shift)&1)<<6
                board._fd_pin_change(odr,odr|32)
            board._fd_pin_change(0,8)
        actual=[(cycles//2000,reg,value) for cycles,reg,value in board.fd_write_events]
        assert actual==model['writes']
        assert all(reg==0x51 for _,reg,value in actual)
        assert len(actual)>100
    finally:board.close_rf()
