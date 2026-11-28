"""Time-resolved microphone module and v2 serial mailbox behavior."""
from __future__ import annotations
import base64
import re
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result, decompress_pcm, TIME_BIN_END_MS

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_time.c'
FIXTURE=ROOT/'tests/data/k6/sotto_mic_time_host.c'


def test_time_module_pic_and_no_write_contract():
    s=SRC.read_text()
    code,entry=k6mod.build(str(SRC))
    assert k6mod.module_name(code)=='MIC TIME' and len(code)<5000
    assert k6mod.bin_entry(code)==entry
    assert 'a->say("MAB1:CLR")' in s
    assert 'raw[0]=2' in s
    assert 'a->say(line)' in s
    assert s.index('a->tx_off()')<s.index('emit_result(a,raw)')
    assert 'mic_stream(0,r,sizeof(*r))' in s and 'mic_stream(1,0,0)' in s
    assert 'if(r->lost)' in s and 'if(samples!=8000u||bin!=12u)' in s
    assert not re.search(r'\ba->(?:fd_write|tx_pa|fsk_async|fl_prog|fl_erase)\s*\(',s)
    assert 'Hold PTT ~1.5 sec' in s


def test_log_pcm_compander_monotonic():
    # Same log format used in C compress_pcm. Verify every possible byte.
    vals=[decompress_pcm(code) for code in range(192)]
    assert vals[:16]==list(range(16))
    assert vals==sorted(vals)
    for v in (0,1,15,16,31,32,55,100,1300,4000,8000,32767):
        code=0 if v==0 else next((x for x in range(192) if decompress_pcm(x)>v),192)-1
        assert decompress_pcm(code)<=v
        assert v-decompress_pcm(code)<=max(1,v//16+4)


@pytest.fixture
def c_host(tmp_path):
    exe=tmp_path/'mic_time'
    subprocess.run(['clang','-O2','-std=c11',str(FIXTURE),'-lm','-o',str(exe)],
                   check=True,capture_output=True,text=True)
    return exe


def run(c_host,scenario):
    r=subprocess.run([str(c_host),scenario],capture_output=True,text=True,check=True,timeout=15)
    assert 'PCM TIME CAPTURE + PA RESTORE PASS' in r.stdout
    encoded=next(x.removeprefix('encoded=') for x in r.stdout.splitlines() if x.startswith('encoded='))
    return decode_mic_result(b'\1'+encoded[5:].encode())


def test_early_tone_ends_after_120ms(c_host):
    doc=run(c_host,'burst')
    assert doc['version']==2
    assert len(doc['windows'])==12
    assert [w['end_ms'] for w in doc['windows']]==list(TIME_BIN_END_MS)
    assert all(w['tone_1khz_pcm_peak']>=3500 for w in doc['windows'][:6])
    assert all(w['tone_1khz_pcm_peak']<=1 for w in doc['windows'][6:])
    assert all(w['mean_abs_pcm']<=1 for w in doc['windows'][6:])
    assert doc['samples']==8000 and doc['clipped_samples_total']==0 and doc['vox_max']==542


def test_continuous_tone_and_silence(c_host):
    continuous=run(c_host,'steady')
    assert all(w['tone_1khz_pcm_peak']>=3500 for w in continuous['windows'])
    silence=run(c_host,'quiet')
    assert all(w['tone_1khz_pcm_peak']<=2 for w in silence['windows'])
    assert all(w['mean_abs_pcm']<=2 for w in silence['windows'])


def test_uart_exclusion_never_starts_capture(c_host):
    r=subprocess.run([str(c_host),'uart'],capture_output=True,text=True,check=True,timeout=15)
    assert 'UART exclusion PASS' in r.stdout


def test_crc_rejects_corrupted_time_record(c_host):
    r=subprocess.run([str(c_host),'steady'],capture_output=True,text=True,check=True,timeout=15)
    encoded=next(x.removeprefix('encoded=') for x in r.stdout.splitlines() if x.startswith('encoded='))
    raw=bytearray(base64.b64decode(encoded[5:]))
    raw[4]^=0x10
    with pytest.raises(ValueError,match='checksum'):
        decode_mic_result(b'\1'+base64.b64encode(raw))
