"""Compiled TX/RX multi-burst erasures, corruption and clock-drift recovery.

All tests use Unicorn/virtual RF, not actual radio hardware. RX receives no
payload oracle; only post-RX CRC/sequence scoring reads expected packet count.
"""
import numpy as np
import pytest
from modem import pager
from tools.k6_fsk_burst_recovery import trial,decode_sequences

PROFILE=pager.profile('L','1200',depth=1,parity=False,short_last=True)


def test_compiled_five_burst_baseline_and_rearm():
    r=trial(packets=5)
    assert r['tx_completed']==r['receiver_frames']==5
    assert r['accepted_packets']==r['exact_packets']==5
    assert r['sequences']==[0,1,2,3,4]
    assert r['missing_sequences']==[]
    assert r['crc_invalid']==r['rx_overruns']==r['rx_underflows']==0
    assert r['clock_fits']==5
    assert abs(r['last_acquired_clock_ppm'])<100
    assert r['complete'] and r['clean_recovery']
    assert not r['rf_hardware'] and not r['physical_tx']


@pytest.mark.parametrize('faults,seq,holes,badcrc',[
    ({'drop_bursts':(2,)},[0,1,3,4],[2],0),
    ({'corrupt_bursts':(1,)},[0,2,3,4],[1],1),
    ({'drop_bursts':(1,),'corrupt_bursts':(3,)},[0,2,4],[1,3],1),
])
def test_after_bad_burst_following_valid_packets_recover(faults,seq,holes,badcrc):
    r=trial(**faults)
    assert r['tx_completed']==5
    assert r['sequences']==seq
    assert r['missing_sequences']==holes
    assert r['crc_invalid']==badcrc
    assert r['accepted_packets']==len(seq)
    assert r['clock_fits']==5-len(faults.get('drop_bursts',()))
    assert r['rx_overruns']==r['rx_underflows']==0
    assert not r['complete'] and r['clean_recovery']
    # A bad packet is *never* reconstructed without redundancy: gaps are explicit.


@pytest.mark.parametrize('ppm',[-6000.,-2000.,0.,2000.,6000.])
def test_compiled_fifo_public_preamble_clock_recovery(ppm):
    r=trial(packets=4,clock_ppm=ppm)
    assert r['clean_recovery'],r
    assert r['exact_packets']==r['accepted_packets']==4
    assert r['clock_fits']==4
    assert abs(r['last_acquired_clock_ppm']-ppm)<120
    assert r['rx_overruns']==r['rx_underflows']==0


def test_clock_offsets_outside_acquisition_range_not_claimed_as_recovered():
    too_far=trial(packets=4,clock_ppm=8000)
    assert not too_far['clean_recovery']
    assert too_far['exact_packets']==0
    assert too_far['accepted_packets']==0
    assert too_far['missing_sequences']==[0,1,2,3]
    assert too_far['clock_fits']==0


def test_slow_fifo_service_not_accepted_as_recovered():
    r=trial(packets=4,poll_us=25000)
    assert not r['clean_recovery']
    assert r['rx_overruns']>0
    assert r['accepted_packets']<4


def test_sequence_parser_ignores_bad_crc_foreign_and_duplicate_frames():
    original=PROFILE.pack(np.random.default_rng(23).bytes(4*8*6),msg_id=73)
    other=PROFILE.pack(np.random.default_rng(25).bytes(1*8*6),msg_id=74)[0]
    tampered=bytearray(original[1]);tampered[17]^=1
    r=decode_sequences([original[0],original[0],bytes(tampered),other,original[3]],
                       expected_packets=4)
    assert r['sequences']==[0,3]
    assert r['missing_sequences']==[1,2]
    assert r['accepted_packets']==2
    assert r['crc_invalid']==1 and r['duplicate_packets']==1 and r['foreign_packets']==1
    assert not r['complete']


def test_sequence_parser_never_invents_unseen_packets():
    r=decode_sequences([],expected_packets=4)
    assert r['sequences']==[] and r['missing_sequences']==[0,1,2,3]
    assert not r['complete']
    with pytest.raises(ValueError):decode_sequences([],expected_packets=0)
    with pytest.raises(ValueError):trial(packets=1)
    with pytest.raises(ValueError):trial(packets=5,drop_bursts=(2,),corrupt_bursts=(2,))


@pytest.mark.parametrize('seed',[1,2,3])
@pytest.mark.parametrize('ppm',[-6000.,0.,6000.])
def test_clock_recovery_multiple_noise_seeds_at_c_n18(seed,ppm):
    r=trial(packets=4,clock_ppm=ppm,seed=seed,cn_db=18)
    assert r['clean_recovery'],r
    assert r['exact_packets']==4
    assert abs(r['last_acquired_clock_ppm']-ppm)<150


def test_clock_estimate_persisted_across_automatically_rearmed_frames():
    r=trial(packets=5,clock_ppm=-2000,drop_bursts=(2,))
    assert r['clock_fits']==4
    assert abs(r['last_acquired_clock_ppm']+2000)<150
    assert r['missing_sequences']==[2]
    assert r['sequences']==[0,1,3,4]
    assert r['clean_recovery']
