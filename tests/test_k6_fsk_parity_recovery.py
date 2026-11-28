"""Existing Flux XOR-FEC reconstructed from actual compiled native FSK bursts."""
import json
import pytest
from tools.k6_fsk_burst_recovery import main,trial_parity


def test_compiled_tx_rx_with_parity_delivers_entire_message():
    r=trial_parity(data_packets=4)
    assert r['tx_completed']==r['receiver_frames']==5
    assert r['recovered_voice_slots']==r['total_voice_slots']==32
    assert r['repaired_data_packets']==r['crc_invalid']==0
    assert r['payload_exact'] and r['pass_qualified']
    assert r['clock_fits']==5
    assert all(r['voice_slot_good_flags'])
    assert not r['rf_hardware'] and not r['physical_tx']


@pytest.mark.parametrize('faults,bad_crc',[
    ({'drop_bursts':(2,)},0),
    ({'corrupt_bursts':(1,)},1),
])
def test_parity_repairs_one_missing_or_corrupt_data_packet(faults,bad_crc):
    r=trial_parity(data_packets=4,**faults)
    assert r['tx_completed']==5
    assert r['recovered_voice_slots']==32
    assert r['repaired_data_packets']==1
    assert r['crc_invalid']==bad_crc
    assert r['payload_exact'] and r['pass_qualified']
    assert r['fifo_overruns']==r['fifo_underflows']==0
    assert all(r['voice_slot_good_flags'])


def test_two_missing_packets_in_same_parity_group_remain_missing():
    r=trial_parity(data_packets=4,drop_bursts=(1,2))
    assert not r['payload_exact'] and not r['pass_qualified']
    assert r['repaired_data_packets']==0
    assert r['recovered_voice_slots']==16
    assert sum(r['voice_slot_good_flags'])==16


def test_one_loss_per_group_recovers_both_using_two_independent_parity_bursts():
    r=trial_parity(data_packets=8,drop_bursts=(1,7))
    assert r['on_air_packets']==10
    assert r['tx_completed']==10
    assert r['repaired_data_packets']==2
    assert r['recovered_voice_slots']==r['total_voice_slots']==64
    assert r['pass_qualified'] and r['payload_exact']


def test_two_losses_in_one_group_do_not_borrow_parity_from_other_group():
    r=trial_parity(data_packets=8,drop_bursts=(1,2))
    assert r['repaired_data_packets']==0
    assert r['recovered_voice_slots']==48
    assert not r['pass_qualified']


@pytest.mark.parametrize('ppm',[-6000.,0.,2000.,6000.])
def test_clock_drift_and_single_erasure_combined(ppm):
    r=trial_parity(data_packets=4,clock_ppm=ppm,drop_bursts=(2,))
    assert r['recovered_voice_slots']==32
    assert r['repaired_data_packets']==1
    assert r['pass_qualified']
    assert abs(r['last_acquired_clock_ppm']-ppm)<150


def test_fifo_overrun_cannot_be_hidden_as_qualified_parity_delivery():
    r=trial_parity(data_packets=4,poll_us=25000)
    assert r['fifo_overruns']>0
    assert not r['pass_qualified']


def test_bad_parity_instead_of_missing_data_produces_no_repair_needed():
    # Last burst is XOR parity, all four data packets remain correct.
    r=trial_parity(data_packets=4,drop_bursts=(4,))
    assert r['repaired_data_packets']==0
    assert r['recovered_voice_slots']==32
    assert r['pass_qualified']


def test_parity_cli_reports_reconstructed_and_incomplete(tmp_path):
    path=tmp_path/'parity.json'
    assert main(['--parity','--packets','4','--drop','2','--json',str(path)])==0
    r=json.loads(path.read_text())
    assert r['repaired_data_packets']==1 and r['pass_qualified']
    assert main(['--parity','--packets','4','--drop','1','--drop','2',
                 '--json',str(path)])==1
    r=json.loads(path.read_text())
    assert r['recovered_voice_slots']==16 and not r['pass_qualified']
    with pytest.raises(SystemExit):
        main(['--parity','--packets','4','--drop','2','--corrupt','2'])
