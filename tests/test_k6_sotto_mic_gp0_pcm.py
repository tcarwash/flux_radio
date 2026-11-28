"""Cortex-M0 GP0 DMA mic PCM diagnostic with real C code + fake hardware."""
import base64
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_gp0_pcm.c'
HOST=ROOT/'tests/data/k6/sotto_mic_gp0_pcm_host.c'


def test_pic_module_and_guards():
    code,entry=k6mod.build(str(SRC))
    assert entry==16 and k6mod.module_name(code)=='MIC PCM' and len(code)<5000
    s=SRC.read_text()
    assert 'a->mic_stream(7,0,0)' in s
    assert 'a->mic_stream(0' not in s
    assert 'a->tx_pa(' not in s
    assert 'PCM_WARM 800u' in s and 'PCM_WINDOW 256u' in s
    assert 'capture_pcm' in s and 'gp0_pcm_push' in s
    assert s.index('stop_dma();wr(DMA_CNDTR1')<s.index('a->mic_stream(1,0,0)')
    assert s.index('a->tx_off()')<s.index('emit(a,report);')


@pytest.fixture
def exe(tmp_path):
    p=tmp_path/'mic_pcm'
    subprocess.run(['clang','-O2','-std=c11',str(HOST),'-lm','-o',str(p)],
                   text=True,capture_output=True,check=True)
    return p


def run(exe,mode):
    p=subprocess.run([str(exe),mode],text=True,capture_output=True,check=True,timeout=15)
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                 if line.startswith('encoded='))
    assert len(encoded)==49
    return decode_mic_result(b'\x01'+encoded[5:].encode()),p.stdout


def test_8k_pcm_tone_follows_generator_and_is_absent_at_silence(exe):
    one,r1=run(exe,'1k');two,r2=run(exe,'2k');off,r0=run(exe,'silent')
    assert all('GP0 PCM AND TX MIC RESTORE PASS' in r for r in (r1,r2,r0))
    assert all(d['version']==8 and d['pcm_samples_total']==1824 for d in (one,two,off))
    assert all(d['vox_max']==650 for d in (one,two)) and off['vox_max']==180
    assert 70000<one['dma_decimated_samples_per_second']<100000
    for a,b,c in zip(one['windows'],two['windows'],off['windows']):
        assert a['pcm_tone_1khz_peak']>b['pcm_tone_1khz_peak']+1000
        assert b['pcm_tone_2khz_peak']>a['pcm_tone_2khz_peak']+1000
        assert a['pcm_tone_1khz_peak']>c['pcm_tone_1khz_peak']+1000
        assert b['pcm_tone_2khz_peak']>c['pcm_tone_2khz_peak']+1000
        assert a['pcm_clipped_samples']<10
        assert b['pcm_clipped_samples']<10


def test_uart_and_failure_cleanup(exe):
    p=subprocess.run([str(exe),'uart'],text=True,capture_output=True,check=True)
    assert 'UART EXCLUSION PASS' in p.stdout
    for mode,err in [('oldfirmware',6),('busy',20)]:
        d,report=run(exe,mode)
        assert d['version']==9 and d['error_code']==err and not d['completed']
        assert 'PASS' in report


def test_crc_guard_for_both_success_and_failure(exe):
    for mode in ('1k','busy'):
        p=subprocess.run([str(exe),mode],text=True,capture_output=True,check=True)
        encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines() if line.startswith('encoded='))
        raw=bytearray(base64.b64decode(encoded[5:]))
        raw[9]^=1
        with pytest.raises(ValueError,match='checksum'):
            decode_mic_result(b'\x01'+base64.b64encode(raw))
