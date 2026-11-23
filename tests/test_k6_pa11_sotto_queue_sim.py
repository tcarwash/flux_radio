"""CPU/RAM deadline sensitivity with genuine slim codebook fetch counts."""
from pathlib import Path
import pytest
from tools.k6_pa11_sotto_queue_sim import simulate,read_counts

CAPTURE={'samples':8164,'turns':200,'timing_ticks_100us':10261,
         'measured_sample_rate_hz':7956.33953805672}
READS=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/mic-incremental-codebook-reads.csv')


def test_even_10ms_w90_two_buffers_only_in_ideal_distribution():
    s=simulate(CAPTURE,turns=90,buffer_count=2,pattern='even',blocks=100)
    assert s['no_buffer_overrun']
    assert s['completed_10ms_blocks']==100
    assert s['proof_level']=='OFFLINE_BURST_SENSITIVITY_NOT_MCU_MEASUREMENT'
    assert s['nominal_background_time_us']>4500
    assert not simulate(CAPTURE,turns=200,buffer_count=2,pattern='even')['no_buffer_overrun']


def test_bursty_same_average_fails_two_buffers():
    for pattern in ('bursty','severe'):
        r=simulate(CAPTURE,turns=90,buffer_count=2,pattern=pattern)
        assert not r['no_buffer_overrun']
        assert r['first_buffer_overrun_block']==4
    assert simulate(CAPTURE,turns=90,buffer_count=4,pattern='bursty')['no_buffer_overrun']


def test_real_recorded_mic_read_counts_are_clustered():
    if not READS.exists():pytest.skip('Private K6 mic host-instrumentation read counts absent')
    reads=read_counts(READS)
    assert len(reads)==104 and sum(reads)==3542
    assert max(reads)==191
    assert all(v==0 for i,v in enumerate(reads) if i%4!=0)
    assert reads[:8]==[0,0,0,0,108,0,0,0]
    assert sum(x>0 for x in reads)==25
    a=simulate(CAPTURE,turns=90,buffer_count=2,pattern='even',blocks=len(reads),codebook_reads=reads)
    assert not a['no_buffer_overrun']
    assert a['first_buffer_overrun_block']==5
    b=simulate(CAPTURE,turns=90,buffer_count=3,pattern='even',blocks=len(reads),codebook_reads=reads)
    assert not b['no_buffer_overrun']
    c=simulate(CAPTURE,turns=90,buffer_count=4,pattern='even',blocks=len(reads),codebook_reads=reads)
    assert c['no_buffer_overrun']
    assert c['spi_read_workload_source']=='actual_recorded_mic_slim_encode10_read_counts'
    d=simulate(CAPTURE,turns=80,buffer_count=3,pattern='even',blocks=len(reads),codebook_reads=reads)
    assert d['no_buffer_overrun']


def test_real_trace_with_bursty_codec_changes_buffer_result():
    if not READS.exists():pytest.skip('Private K6 mic host-instrumentation read counts absent')
    reads=read_counts(READS)
    a=simulate(CAPTURE,turns=90,buffer_count=3,pattern='bursty',blocks=len(reads),codebook_reads=reads)
    assert a['no_buffer_overrun']
    # The codec work distribution is not measured; changing it alters results.
    b=simulate(CAPTURE,turns=90,buffer_count=3,pattern='even',blocks=len(reads),codebook_reads=reads)
    assert not b['no_buffer_overrun']


def test_invalid_model_inputs_rejected():
    with pytest.raises(ValueError):simulate(CAPTURE,buffer_count=1)
    with pytest.raises(ValueError):simulate(CAPTURE,pattern='unknown')
    with pytest.raises(ValueError,match='trace'):simulate(CAPTURE,codebook_reads=[1,2])
    with pytest.raises(ValueError,match='trace'):simulate(CAPTURE,codebook_reads=[-1]*100)
