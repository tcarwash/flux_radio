"""AF bypass and GP0->PB1 microphone-route diagnostics, isolated C/Rust ABI."""
import base64
import re
import subprocess
from pathlib import Path

import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/sotto_mic_routes.c'
HOST=ROOT/'tests/data/k6/sotto_mic_routes_host.c'


def test_pic_module_contract():
    code,entry=k6mod.build(str(SOURCE))
    assert entry==16 and k6mod.module_name(code)=='MIC ROUTES' and len(code)<4000
    s=SOURCE.read_text()
    assert 'raw[0]=4' in s
    assert '{4,9,5,1}' in s and '{9,11,12,13,14}' in s
    assert 'a->say("MAB1:CLR")' in s
    assert s.index('saved33=a->fd_read(0x33)')>s.index('a->mic_stream(0,r,sizeof(*r))')
    assert 'raw[0]=5' in s
    assert s.index('a->tx_off()')<s.index('save(a,raw)')
    assert 'if(!MIC_ROUTES_PB1_INPUT())return -15' in s
    assert not re.search(r'\ba->(?:tx_pa|fl_prog|fl_erase|cal_write)\(',s)
    assert 'STAGE_SETTLE_BLOCKS 4' in s and 'STAGE_MEASURE_BLOCKS 16' in s


@pytest.fixture
def host_executable(tmp_path):
    exe=tmp_path/'routes'
    subprocess.run(['clang','-O2','-std=c11',str(HOST),'-lm','-o',str(exe)],
                   capture_output=True,text=True,check=True)
    return exe


def invoke(exe,mode):
    p=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=15,check=True)
    assert 'ROUTES RESULT AND RESTORE PASS' in p.stdout
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                 if line.startswith('encoded='))
    assert len(encoded)==49 and encoded.startswith('MAB1:')
    return decode_mic_result(b'\x01'+encoded[5:].encode())


def test_tone_on_detects_af_bypass_only(host_executable):
    d=invoke(host_executable,'tone')
    assert d['version']==4 and d['stage_count']==9
    assert d['measurement']=='af_bypass_and_gp0_tx_mic_probe'
    af=d['stages'][:4]
    assert [r['af_select'] for r in af]==[4,9,5,1]
    assert af[0]['steady_pcm_mean_abs']<=1
    assert af[1]['steady_pcm_1khz_peak']>4500
    assert af[1]['steady_pcm_mean_abs']>3000
    assert af[2]['steady_pcm_1khz_peak']<=1
    gp0=d['stages'][4:]
    assert [r['gp0_output_select'] for r in gp0]==[9,11,12,13,14]
    assert gp0[0]['pb1_transition_fraction']<gp0[-1]['pb1_transition_fraction']
    assert d['vox_max']==650


def test_tone_off_has_no_pcm(host_executable):
    d=invoke(host_executable,'silent')
    assert all(row['steady_pcm_1khz_peak']<=1 for row in d['stages'][:4])
    assert d['vox_max']==305


def test_uart_exclusion_no_rf(host_executable):
    p=subprocess.run([str(host_executable),'uart'],capture_output=True,text=True,check=True,timeout=15)
    assert 'UART EXCLUSION PASS' in p.stdout


def test_failed_register_write_preserves_error_and_restores_gpio(host_executable):
    p=subprocess.run([str(host_executable),'error'],capture_output=True,text=True,check=True,timeout=15)
    assert 'FAILED WRITE CLEANUP PASS' in p.stdout
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                 if line.startswith('encoded='))
    doc=decode_mic_result(b'\x01'+encoded[5:].encode())
    assert doc['version']==5 and doc['completed'] is False
    assert doc['error_code']==20
    assert doc['completed_stages']==4


def test_tampered_crc_rejected(host_executable):
    p=subprocess.run([str(host_executable),'tone'],capture_output=True,text=True,check=True,timeout=15)
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines()
                 if line.startswith('encoded='))
    raw=bytearray(base64.b64decode(encoded[5:]));raw[3]^=0x20
    with pytest.raises(ValueError,match='checksum'):
        decode_mic_result(b'\x01'+base64.b64encode(raw))
