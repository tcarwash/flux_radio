"""Non-genie burst start acquisition through genuine SoapyFang IQ filtering."""
import json
import numpy as np
import pytest

from sim.k6emu.rf.frequency_modem import RATE,Settings,build
from sim.k6emu.rf.burst_acquisition import acquire
from tools.k6_frequency_modem_lab import (
    fm_discriminator,rx_soapy,run_blind,main,
)

CASES=[('native_fsk',4800,0),('subaudio',300,135),('signed_dac',300,350)]


@pytest.mark.parametrize('path,baud,bw',CASES)
@pytest.mark.parametrize('lead,offset,snr',[(0,0,30),(17,700,30),(1289,-500,30),
                                             (2903,150,25),(3800,400,20)])
def test_unknown_burst_start_and_cfo_resolved_with_public_sync_only(path,baud,bw,lead,offset,snr):
    r=run_blind(Settings(path=path,baud=baud,bandwidth_hz=bw),
                lead_samples=lead,offset_hz=offset,snr_db=snr)
    assert r['lock'] and r['pass_frame'],r
    assert r['payload_ber']==r['payload_symbol_error_rate']==0
    assert r['pilot_errors']==r['sync_errors']==0
    assert r['pilot_correlation']>.80
    assert abs(r['estimated_center_offset_hz']-offset)<60
    assert abs(r['start_error_samples']) <= max(7,int(RATE/baud*.75))
    assert r['acquisition']=='blind_start_public_pilot_plus_sync'
    assert r['sync_validated_candidates']>0
    assert not r['physical_rf'] and not r['hardware_qualified']


def _acquire(source,received):
    n=source['pilot_n'];m=source['sync_n']
    return acquire(fm_discriminator(received),baud=source['symbol_rate'],rate=RATE,
                   order=source['order'],pilot=source['source_levels'][:n],
                   sync=source['source_levels'][n:n+m],
                   payload_symbols=len(source['source_levels'])-n-m)


@pytest.mark.parametrize('path,baud,bw',CASES)
def test_noise_only_does_not_false_lock_despite_correct_length(path,baud,bw):
    source=build(Settings(path=path,baud=baud,bandwidth_hz=bw))
    noise=rx_soapy(np.zeros(2000+len(source['iq']),dtype=np.complex64),snr_db=30)
    found=_acquire(source,noise)
    assert not found['locked']
    assert found['sync_validated_candidates']==0


def test_wrong_public_sync_blocks_lock_and_payload_lookahead():
    s=build(Settings(path='native_fsk'))
    freq=s['frequency_hz'].copy()
    start=s['pilot_n']*(RATE//4800)
    end=(s['pilot_n']+s['sync_n'])*(RATE//4800)
    freq[start:end]=-1500  # deterministic wrong sync, pilot untouched
    wave=np.exp(2j*np.pi*np.cumsum(freq)/RATE).astype(np.complex64)
    result=_acquire(s,rx_soapy(np.r_[np.zeros(1289,dtype=np.complex64),wave]))
    assert not result['locked']


def test_corrupted_heldout_symbol_decodes_as_error_but_does_not_change_lock():
    s=build(Settings(path='native_fsk'))
    freq=s['frequency_hz'].copy()
    frame_data_offset=s['pilot_n']+s['sync_n']
    sps=RATE//4800
    at=(frame_data_offset+10)*sps
    freq[at:at+sps]*=-1
    wave=np.exp(2j*np.pi*np.cumsum(freq)/RATE).astype(np.complex64)
    found=_acquire(s,rx_soapy(np.r_[np.zeros(1289,dtype=np.complex64),wave]))
    assert found['locked']
    start=frame_data_offset
    got=found['decisions'][start:]
    expected=s['source_levels'][start:]
    assert len(got)==len(expected)
    assert np.count_nonzero(got!=expected)==1
    assert found['sync_errors']==found['pilot_errors']==0


def test_signed_four_level_pilot_oracle_cannot_hide_inverted_mapping():
    config=Settings(path='signed_dac',baud=300,bandwidth_hz=350)
    correct=run_blind(config)
    inverted=run_blind(config,wrong_signed_order=True)
    assert correct['pass_frame'] and correct['level_order_valid']
    assert inverted['lock'] and not inverted['pass_frame']
    assert not inverted['level_order_valid']
    assert inverted['payload_ber']==0  # deliberate polarity ambiguity


def test_public_pilot_phase_search_recovers_600_baud_but_not_1200():
    good=run_blind(Settings(path='subaudio',baud=300,bandwidth_hz=135))
    mid=run_blind(Settings(path='subaudio',baud=600,bandwidth_hz=135))
    bad=run_blind(Settings(path='subaudio',baud=1200,bandwidth_hz=135))
    assert good['pass_frame'] and mid['pass_frame']
    assert mid['payload_ber']==0 and mid['sync_errors']==0
    assert not bad['pass_frame']
    assert bad.get('payload_ber',1)>0


def test_submillisecond_tail_flush_keeps_every_sample():
    # 17 sample lead on 300-baud 4-FSK is a .354-ms final chunk. Without the
    # final direct virtual advance the backend leaves those IQ samples unread.
    r=run_blind(Settings(path='signed_dac',baud=300,bandwidth_hz=350),
                lead_samples=17,offset_hz=700)
    assert r['pass_frame']


def test_cli_blind_start_outputs_inert_reproducible_json(tmp_path):
    path=tmp_path/'burst.json'
    assert main(['--path','signed_dac','--baud','300','--bandwidth-hz','350',
                 '--blind-start','--lead-samples','1289','--offset-hz','230',
                 '--json',str(path)])==0
    obj=json.loads(path.read_text())
    assert obj['pass_frame'] and obj['lock']
    assert obj['payload_bits']==256 and obj['payload_ber']==0
    assert obj['physical_rf'] is False
    assert obj['recovered_start_sample']>1289
    with pytest.raises(SystemExit):
        main(['--blind-start','--lead-samples','-1'])


def test_parameter_guards_fail_closed():
    s=Settings(path='native_fsk')
    with pytest.raises(ValueError):run_blind(s,lead_samples=-1)
    with pytest.raises(ValueError):run_blind(s,offset_hz=float('nan'))
    model=build(s)
    with pytest.raises(ValueError):
        acquire(np.ones(300,dtype=float),baud=4800,rate=48000,order=2,
                pilot=model['source_levels'][:model['pilot_n']],
                sync=model['source_levels'][model['pilot_n']:model['pilot_n']+model['sync_n']],
                payload_symbols=128)
