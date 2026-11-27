"""Real C pager decoder-pool reuse + profile selection guards."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/sotto_pager.c'
TEST=ROOT/'tests/data/k6/sotto1200_halfduplex_pool_host.c'


def test_decoder_borrows_module_arena_and_recycles_after_tx(tmp_path):
    exe=tmp_path/'recycle'
    proc=subprocess.run(['cc','-std=c99','-O2','-ffunction-sections',
        '-fdata-sections','-DSP_LR','-DSP_ONLY_1200','-DSP_DEC_POOL',
        '-DFX_SYNTH_STATE','-I'+str(ROOT/'c'),str(TEST),str(SRC),
        '-Wl,--gc-sections','-o',str(exe)],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    result=subprocess.check_output([exe],text=True)
    assert 'shared_arena=14976' in result
    assert 'decoder_offset=5376' in result
    assert 'decoder_need=3232' in result
    assert 'reuse=yes' in result


def test_1200_exclusive_host_profile_enables_actual_lr_decoder_pool():
    build=(ROOT/'firmware/k6-aura/build-fd6818b.sh').read_text()
    pool=(ROOT/'firmware/k6-aura/apply-sotto1200-exclusive-pool.py').read_text()
    assert 'K6_SOTTO1200_ONLY' in build
    assert 'K6_CODEC=1200' in build
    assert 'k6_sotto_1200_only' in build
    assert 'K6_SOTTO_SERVICE=1' in build
    assert 'ffi::sp_dec_pool(arena::dec(), arena::DEC_BYTES as u32)' in pool
    assert "k6_sotto_1200_only" in pool
    assert "K6_RAM0" in pool
