"""Compile genuine incremental slim C API and compare exact batch bitstream."""
from pathlib import Path
import csv
import shutil
import subprocess
import pytest

SOTTO=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
USER=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw')
REFERENCE=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit')
WRAPPER=Path(__file__).resolve().parents[1]/'tools/k6_sotto_slim_streaming_host.c'


def compile_incremental(out:Path):
    subprocess.run(['clang','-O2','-DSOTTO_V36','-DSOTTO_PITCH_THIRD',
        '-DFX_NARROW_MUL','-I',str(SOTTO),str(WRAPPER),
        str(SOTTO/'sotto_fx_slim.c'),str(SOTTO/'fx_tables.c'),'-lm',
        '-o',str(out)],check=True,timeout=60,capture_output=True)


@pytest.mark.skipif(not (shutil.which('clang') and (SOTTO/'sotto_fx_slim.c').exists()),
                    reason='Sotto C source/clang not available on this host')
def test_host_incremental_10ms_c_api_matches_batch_synthetic_audio(tmp_path):
    exe=tmp_path/'incremental';compile_incremental(exe)
    raw=tmp_path/'pcm.raw'
    import numpy as np
    t=np.arange(0,8000,dtype=np.float64)/8000
    raw.write_bytes(np.rint(3000*np.sin(2*np.pi*440*t)).astype('<i2').tobytes())
    bits=tmp_path/'bits.bit';timeline=tmp_path/'times.csv';batch=tmp_path/'batch.bit'
    subprocess.run([str(exe),str(raw),str(bits),str(timeline)],check=True,capture_output=True,timeout=30)
    subprocess.run([str(SOTTO/'sotto_slim_enc'),str(raw),str(batch)],check=True,capture_output=True,timeout=30)
    assert bits.read_bytes()==batch.read_bytes()
    assert len(bits.read_bytes())==25*6
    rows=list(csv.DictReader(timeline.open()))
    assert len(rows)==25
    assert int(rows[0]['10ms_blocks_received'])==9
    assert sum(int(x['flush_phase']) for x in rows)==2


@pytest.mark.skipif(not (shutil.which('clang') and USER.exists() and REFERENCE.exists()),
                    reason='Recorded K6 microphone or slim codec not available on this host')
def test_host_incremental_10ms_c_api_exact_on_real_k6_microphone(tmp_path):
    exe=tmp_path/'incremental';compile_incremental(exe)
    bits=tmp_path/'k6.bit';timeline=tmp_path/'timeline.csv'
    subprocess.run([str(exe),str(USER),str(bits),str(timeline)],check=True,capture_output=True,timeout=30)
    assert bits.read_bytes()==REFERENCE.read_bytes()
    rows=list(csv.DictReader(timeline.open()))
    assert len(rows)==26
    assert int(rows[0]['10ms_blocks_received'])==9
    assert int(rows[23]['10ms_blocks_received'])==101
    assert int(rows[-1]['10ms_blocks_received'])==104
    assert sum(int(x['flush_phase']) for x in rows)==2


def test_real_k6_mic_instrumented_codebook_burst_is_bit_exact(tmp_path):
    if not (shutil.which('clang') and USER.exists() and REFERENCE.exists()):
        pytest.skip('Saved K6 mic or host compiler absent')
    exe=tmp_path/'stats-encoder'
    subprocess.run(['clang','-O2','-DSOTTO_V36','-DSOTTO_PITCH_THIRD',
        '-DFX_NARROW_MUL','-DSLIM_TUNE','-I',str(SOTTO),str(WRAPPER),
        str(SOTTO/'sotto_fx_slim.c'),str(SOTTO/'fx_tables.c'),'-lm',
        '-o',str(exe)],check=True,timeout=60,capture_output=True)
    bits=tmp_path/'slim.bit';times=tmp_path/'events.csv';stats=tmp_path/'read-counts.csv'
    subprocess.run([str(exe),str(USER),str(bits),str(times),str(stats)],
        check=True,capture_output=True,timeout=40)
    assert bits.read_bytes()==REFERENCE.read_bytes()
    rows=list(csv.DictReader(stats.open()))
    reads=[int(r['codebook_vectors_read_this_call']) for r in rows]
    assert len(reads)==104
    assert sum(reads)==3542
    assert max(reads)==191
    assert reads[4]==108
    assert all(v==0 for i,v in enumerate(reads) if i%4!=0)
    assert all(int(r['10ms_block_index'])==i for i,r in enumerate(rows))
