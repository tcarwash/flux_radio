"""Finite measured RF/GP0 evidence and opt-in hardware behavioral annotations."""
from pathlib import Path
import json
import math
import pytest

from sim.k6emu.rf.physical_calibration import (
    FIXTURE, Gp0MicCalibration, PaFifoTimeline, PhysicalTxCalibration,
    TxCondition, load_calibration,
)
from sim.k6emu.rf.virtual_link import VirtualAir, VirtualTxBackend
from test_k6_virtual_link import configure, HZ


def test_fixture_provenance_and_per_run_packet_deltas():
    d=load_calibration()
    assert d['schema']==1 and d['measured']
    assert len(d['transmit'])==21 and len(d['microphone'])==6
    names={r['id']:r for r in d['transmit']}
    assert names['api31-onradio-pcm-pa20-gap0-run6']['crc_valid_delta']==3
    assert names['api31-pa0-settle20-prefetch-control1']['crc_valid_delta']==0
    assert names['api31-onradio-pcm-direct-run4']['crc_rejected_delta']==1
    assert names['fsk13-prefetch-usb-fix-run1']['crc_valid_delta']==8
    assert names['frozen-fsk13-current-fw-run1']['crc_rejected_delta']==4
    assert all(len(r['sha256'])==64 for r in d['transmit']+d['microphone'])
    assert all(r['provenance'].startswith('physical_') for r in d['transmit']+d['microphone'])


def test_rederive_exact_sha_from_real_sources_if_available():
    import hashlib
    p=Path('/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125')
    if p.exists():
        for r in load_calibration()['transmit']:
            f=p/r['id']/'result.json'
            assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==r['sha256']


def test_reject_invalid_count_and_nonmeasured_model(tmp_path):
    d=load_calibration()
    d['transmit'][0]['crc_valid_delta']+=2
    f=tmp_path/'bad.json';f.write_text(json.dumps(d))
    with pytest.raises(ValueError, match='accounting'):
        load_calibration(f)
    d['transmit'][0]['crc_valid_delta']-=2
    d['measured']=False
    f.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='provenance'):
        load_calibration(f)


def test_startup_evidence_no_extrapolation_and_holdout():
    model=PhysicalTxCalibration()
    pa=TxCondition(True,20,0,False,'onradio_codec')
    r=model.assess(pa)
    assert r['cases']==6 and r['complete_sessions']==6
    assert r['crc_valid_packets']==18 and r['status']=='repeatably_observed'
    muted=model.assess(TxCondition(False,20,60,False,'direct_fsk'))
    assert muted['cases']==1 and muted['complete_sessions']==0
    assert muted['unobserved_packets']==3
    assert muted['status']=='no_complete_sessions_observed'
    no_guard=model.assess(TxCondition(True,0,0,False,'direct_fsk'))
    assert no_guard['cases']==1 and no_guard['status']=='mixed_or_sparse_evidence'
    assert model.assess(TxCondition(True,17,0,False,'onradio_codec'))['status']=='unobserved'
    assert model.assess(TxCondition(True,20,0,False,'fsk13_old_reader'))['status']=='unobserved'
    a=model.assess(TxCondition(True,None,None,False,'fsk13_old_reader'))
    assert a['status']=='measurement_chain_only'
    assert model.leave_one_out()==[
        {'held_out':'api31-direct-pa20-gap60-prefetch-run5','training_cases':4,
         'train_status':'repeatably_observed','observed_complete':True,'matched_conditions_only':True},
        {'held_out':'api31-onradio-pcm-pa20-gap0-run6','training_cases':5,
         'train_status':'repeatably_observed','observed_complete':True,'matched_conditions_only':True}]


def test_pa_fifo_event_sequence_annotations_not_fake_crc():
    cal=PhysicalTxCalibration()
    trace=PaFifoTimeline(receiver_mode='direct_fsk')
    trace.note_pa(True,0.010)
    trace.note_fifo_preload(.015)
    report=trace.note_tx_start(.030,cal,configured_settle_ms=20,interframe_gap_ms=60)
    assert report['status']=='repeatably_observed'
    assert report['actual_pa_on_time_ms']==20
    assert report['crc_valid_packets']==15
    trace.note_pa(False,.16)
    trace.note_pa(True,.17)
    trace.note_fifo_preload(.175)
    report=trace.note_tx_start(.19,cal,configured_settle_ms=20,interframe_gap_ms=0)
    assert report['condition']['rekey']
    assert report['status']=='no_complete_sessions_observed'  # both documented rekey controls were incomplete
    with pytest.raises(ValueError):
        trace.note_tx_start(float('nan'),cal)


def test_virtual_tx_backend_opt_in_does_not_change_rf_waveform():
    air=VirtualAir()
    a=VirtualTxBackend(air,240000,HZ,
                       startup_observer=PaFifoTimeline(receiver_mode='direct_fsk'),
                       startup_calibration=PhysicalTxCalibration(),
                       configured_settle_ms=20,interframe_gap_ms=60)
    try:
        regs,w=configure(a)
        a.note_pa_state(True,.005)
        for i in range(32):w(0x5f,i,.010)
        w(0x59,0x8f8,.025)
        assert len(air.bursts)==1 and len(a.tx_status()['startup_evidence'])==1
        assert a.tx_status()['startup_evidence'][0]['status']=='repeatably_observed'
        assert len(air.bursts[0].bits)==(16+4+64)*8
        # Calibration did not modify bits or invent packet CRC success.
        assert a.tx_status()['completed']==0
    finally:a.close()
    b=VirtualTxBackend(VirtualAir(),240000,HZ)
    try:
        with pytest.raises(RuntimeError):b.note_pa_state(True,0)
        assert 'startup_evidence' not in b.tx_status()
    finally:b.close()


def test_gp0_empirical_window_tone_and_dma_bounds():
    model=Gp0MicCalibration()
    for kind in ('dma','pcm'):
        off=model.evidence(kind,'off')
        one=model.evidence(kind,'1khz')
        two=model.evidence(kind,'2khz')
        assert one['average_1khz_score']>off['average_1khz_score']
        assert two['average_2khz_score']>off['average_2khz_score']
        assert one['vox_max']>off['vox_max'] and two['vox_max']>off['vox_max']
        wrap=model.dma_wrap_us(measurement=kind)
        assert 180<wrap['fastest_wrap_us']<=wrap['slowest_wrap_us']<190
        assert model.sample_observed_window(kind,'2khz',seed=3)['kind']=='observed_window_resampling'
    assert model.evidence('pcm','2khz')['average_2khz_score']==23867
    assert model.evidence('pcm','1khz')['average_1khz_score']==2195
    assert model.evidence('pcm','off')['average_rms']==1872
    with pytest.raises(ValueError,match='unmeasured'):
        model.evidence('pcm','3khz')
    with pytest.raises(ValueError):model.dma_wrap_us(0)


def test_gp0_dma_emulator_opt_in_uses_real_tx_capture_clock_not_old_rx_constant():
    from sim.k6emu.rx_dma import GpioRxDma, MEASURED_FREE_RUNNING_HZ, DMA_BASE, GPIOB_IDR
    from sim.k6emu.rf.physical_calibration import Gp0MicCalibration
    for stimulus in ('off','1khz','2khz'):
        image=bytearray(1024)
        def write_byte(addr,bit):
            image[addr-0x20000000]=bit
        dut=GpioRxDma.measured_tx_microphone(write_byte,
                                             samples=([1,0]*3500),stimulus=stimulus)
        expected=Gp0MicCalibration().evidence('pcm',stimulus)
        assert dut.sample_rate==expected['raw_dma_hz']
        assert dut.measured_provenance==expected['source']
        assert dut.measurement_only
        assert dut.sample_rate<MEASURED_FREE_RUNNING_HZ
        dut.write(DMA_BASE+0x10,GPIOB_IDR)
        dut.write(DMA_BASE+0x14,0x20000000)
        dut.write(DMA_BASE+0x0c,1024)
        dut.write(DMA_BASE+0x08,(1<<7)|(1<<5)|1)
        dut.advance(960)   # 10us at a 96MHz CPU clock
        assert dut.transfers==int(960*dut.sample_rate/96_000_000)
        assert sum(image)>0
    with pytest.raises(ValueError,match='unmeasured'):
        GpioRxDma.measured_tx_microphone(lambda *args:None,stimulus='3khz')


def test_claimed_20ms_guard_cannot_mask_1ms_virtual_pa_warmup():
    cal=PhysicalTxCalibration()
    trace=PaFifoTimeline(receiver_mode='direct_fsk')
    trace.note_pa(True,.029)
    trace.note_fifo_preload(.0295)
    r=trace.note_tx_start(.030,cal,configured_settle_ms=20,interframe_gap_ms=60)
    assert r['status']=='timing_claim_mismatch'
    assert r['cases']==5  # historical cases match metadata but current event trace does not
    assert r['actual_pa_on_time_ms']==1
