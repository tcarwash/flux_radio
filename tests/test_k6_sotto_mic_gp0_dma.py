"""Physical mic GP0 DMA path: binary, host RTL model, and old firmware rejection."""
import base64
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_gp0_dma.c'
HOST=ROOT/'tests/data/k6/sotto_mic_gp0_dma_host.c'


def test_compile_and_safety_contract():
    code,entry=k6mod.build(str(SRC))
    assert entry==16 and k6mod.module_name(code)=='MIC GP0'
    assert len(code)<5000
    s=SRC.read_text()
    assert 'a->mic_stream(7,0,0)' in s
    assert 'a->mic_stream(3,0,0)' in s
    assert 'a->tx_on(2500u)' in s
    assert 'a->tx_pa(' not in s
    assert 'a->mic_stream(0' not in s
    assert 'a->mic_stream(1,0,0)' in s
    assert 'raw[0]=6' not in s # uses v6 record in result
    assert 'result[0]=6' in s and 'result[0]=7' in s
    assert s.index('stop_dma();wr(DMA_CNDTR1')<s.index('a->mic_stream(1,0,0)')
    assert s.index('a->tx_off()')<s.index('emit(a,result);')


def test_route_only_mic_service_is_opt_in_no_isr():
    inc=(ROOT/'firmware/k6-aura/sotto-api30-mic.rs.inc').read_text()
    assert 'let route_only=op==7;' in inc
    assert 'if !route_only && !crate::hal::pcm_capture::start(ptr)' in inc
    assert 'if op==1{sotto_mic_stop();return 0;}' in inc
    assert 'if LIVE_ACTIVE.is_some(){return -1;}' in inc
    stop=(ROOT/'firmware/k6-aura/sotto-api30-pcm-capture.rs').read_text()
    assert 'if !active(){return;}' in stop


@pytest.fixture
def host_executable(tmp_path):
    exe=tmp_path/'sotto_gp0_dma'
    subprocess.run(['clang','-O2','-std=c11',str(HOST),'-lm','-o',str(exe)],
                   text=True,capture_output=True,check=True)
    return exe


def run(exe,mode):
    p=subprocess.run([str(exe),mode],text=True,capture_output=True,check=True,timeout=25)
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                 if line.startswith('encoded='))
    assert len(encoded)==49 and encoded.startswith('MAB1:')
    return decode_mic_result(b'\x01'+encoded[5:].encode()),p.stdout


def test_tone_frequency_separates_in_real_c_density_demod(host_executable):
    one,report1=run(host_executable,'1k')
    two,report2=run(host_executable,'2k')
    off,report0=run(host_executable,'silent')
    assert 'GP0 DMA AND TX MIC RESTORE PASS' in report1
    assert 'GP0 DMA AND TX MIC RESTORE PASS' in report2
    assert 'GP0 DMA AND TX MIC RESTORE PASS' in report0
    assert all(d['version']==6 for d in (one,two,off))
    assert 70000<one['decimated_samples_per_second']<100000
    assert 4900<one['raw_dma_ksamples_per_second']<6000
    assert one['vox_max']==650 and off['vox_max']==180
    for a,b,c in zip(one['windows'],two['windows'],off['windows']):
        assert a['amplitude_1khz_q']>b['amplitude_1khz_q']+10
        assert b['amplitude_2khz_q']>a['amplitude_2khz_q']+10
        assert a['amplitude_1khz_q']>c['amplitude_1khz_q']+10
        assert b['amplitude_2khz_q']>c['amplitude_2khz_q']+10
        assert .4<a['gp0_raw_high_fraction']<.6


def test_hardware_busy_and_older_firmware_fail_without_tx_leak(host_executable):
    for mode,code in [('oldfirmware',6),('busy',20)]:
        doc,report=run(host_executable,mode)
        assert doc['version']==7 and doc['error_code']==code
        assert doc['completed'] is False
        assert ('REJECT PASS' in report or 'RESTORE PASS' in report)


def test_uart_exclusion_no_mic_or_tx(host_executable):
    result=subprocess.run([str(host_executable),'uart'],text=True,capture_output=True,
                          check=True,timeout=15)
    assert 'UART EXCLUSION PASS' in result.stdout


def test_malformed_and_corrupted_v6_v7_rejected(host_executable):
    for mode in ('1k','busy'):
        p=subprocess.run([str(host_executable),mode],text=True,capture_output=True,check=True)
        encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                     if line.startswith('encoded='))
        raw=bytearray(base64.b64decode(encoded[5:]))
        raw[6]^=1
        with pytest.raises(ValueError,match='checksum'):
            decode_mic_result(b'\x01'+base64.b64encode(raw))
