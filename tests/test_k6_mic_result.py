"""End-to-end MIC A/B report transport and checksummed serial decoding."""
import base64
import struct
import subprocess
from pathlib import Path

import pytest
from tools.k6_mic_result import crc16_xmodem,decode_mic_result

ROOT=Path(__file__).resolve().parents[1]


def test_python_decoder_roundtrip_and_corruption():
    rows=[(1337,100,678,536,4880),(0,0,1,549,4800),(424,1,500,576,4960)]
    data=bytearray([1]);
    for row in rows:data.extend(struct.pack('<HHHHH',*row))
    data.extend(struct.pack('<H',crc16_xmodem(data)))
    assert len(data)==33
    encoded=base64.b64encode(data)
    assert len(encoded)==44
    result=decode_mic_result(b'\x01'+encoded)
    assert result['available'] and len(result['stages'])==3
    assert result['stages'][0]['tone_1khz_pcm_peak']==1337
    assert result['stages'][1]['mean_abs_pcm']==1
    assert result['stages'][2]['register_written'] is True
    assert decode_mic_result(b'\0')['available'] is False
    with pytest.raises(ValueError):decode_mic_result(b'\1'+encoded[:-1])
    altered=bytearray(data);altered[7]^=1
    with pytest.raises(ValueError,match='checksum'):decode_mic_result(b'\1'+base64.b64encode(altered))


def test_radio_module_uses_one_crc_checked_boot_mailbox():
    s=(ROOT/'c/k6/mod/sotto_mic_ab.c').read_text()
    f=(ROOT/'firmware/k6-aura/live-serial.rs').read_text()
    hook=(ROOT/'firmware/k6-aura/apply-mic-result-serial.py').read_text()
    assert 'a->say("MAB1:CLR")' in s
    assert 'a->say(line);' in s
    assert 'report_saved(a,results)' in s
    assert s.index('a->tx_off()')<s.index('report_saved(a,results)')
    assert 'MIC_RESULT:u8=0x86' in f
    assert 'static mut MIC_RESULT_BASE64:[u8;44]' in f
    assert 'MIC_RESULT if req.arg==0' in f
    assert 'if !idle {reply(req,&[BUSY]);return;}' in f
    assert 'cache_mic_result(t.as_bytes())' in hook


def test_actual_c_module_report_decodes_without_hardcoded_payload(tmp_path):
    """Run actual compiled C module against fake radio and parse its emitted say."""
    exe=tmp_path/'mic_ab_host'
    fixture=ROOT/'tests/data/k6/sotto_mic_ab_host.c'
    subprocess.run(['clang','-O2','-std=c11',str(fixture),'-lm','-o',str(exe)],
                   check=True,capture_output=True,text=True)
    ran=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=15)
    assert 'SWEEP + CLEANUP PASS' in ran.stdout
    line=next(x.removeprefix('encoded=') for x in ran.stdout.splitlines() if x.startswith('encoded='))
    assert line.startswith('MAB1:') and len(line)==49
    record=decode_mic_result(b'\x01'+line[5:].encode())
    assert record['available']
    assert [r['tone_1khz_pcm_peak'] for r in record['stages']]==[4599,4599,800]
    assert [r['clipped_samples'] for r in record['stages']]==[0,0,0]
    assert all(r['samples']>=2400 for r in record['stages'])
