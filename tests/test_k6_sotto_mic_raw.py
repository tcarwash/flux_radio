"""Host and source tests for firmware-side 48-bit PA11 density capture."""
from pathlib import Path
import base64
import re
import subprocess
import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result,RAW_BIN_END_MS

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_raw.c'
FIXTURE=ROOT/'tests/data/k6/sotto_mic_raw_host.c'


def test_raw_module_arm_compilation_and_rfic_no_write():
    code,entry=k6mod.build(str(SRC))
    assert len(code)<4500 and k6mod.module_name(code)=='MIC RAW'
    assert k6mod.bin_entry(code)==entry
    s=SRC.read_text()
    assert 'raw[0]=3' in s
    assert all(f'a->mic_stream({op},0,0)' in s for op in (4,5,6))
    assert not re.search(r'\ba->(?:fd_write|tx_pa|fl_prog|fl_erase|fsk_async)\s*\(',s)
    assert 'a->say("MAB1:CLR")' in s
    assert s.index('a->tx_off()')<s.index('emit_result(a,raw)')


def test_firmware_raw_counters_are_pre_dc_and_no_gpio_route_changes():
    rust=(ROOT/'firmware/k6-aura/sotto-api30-pcm-capture.rs').read_text()
    inc=(ROOT/'firmware/k6-aura/sotto-api30-mic.rs.inc').read_text()
    assert rust.index('ones+=b;')<rust.index('let d=i2.wrapping_sub(CIC[2])')
    assert rust.index('RAW_ONES=RAW_ONES.wrapping_add')<rust.index('let delta=')
    assert all(f'if op=={op}' in inc for op in (4,5,6))
    assert 'const TURNS:u32=48;' in rust
    assert 'if RAW_LAST_ONES<=TURNS' in rust
    assert 'wr(A+24,PIN)' in rust and 'wr(A+24,PIN<<16)' in rust


@pytest.fixture
def host(tmp_path):
    exe=tmp_path/'pa11_raw'
    subprocess.run(['clang','-O2','-std=c11',str(FIXTURE),'-lm','-o',str(exe)],
                   check=True,capture_output=True,text=True)
    return exe


def run(host,scenario):
    result=subprocess.run([str(host),scenario],capture_output=True,text=True,check=True,timeout=15)
    assert 'RAW PA11 CAPTURE + PA RESTORE PASS' in result.stdout
    encoded=next(x.removeprefix('encoded=') for x in result.stdout.splitlines() if x.startswith('encoded='))
    assert len(encoded)==49
    return decode_mic_result(b'\1'+encoded[5:].encode())


def test_startup_only_raw_density_variation_drops_and_pcm_drops(host):
    result=run(host,'burst')
    assert result['version']==3 and result['samples']==8000
    assert result['isr_peak_cycles']==5500 and result['isr_deadline_cycles']==12000
    w=result['windows']
    assert [x['end_ms'] for x in w]==list(RAW_BIN_END_MS)
    assert w[0]['raw_density_variation']>0.05
    assert w[1]['mean_abs_pcm']>2000
    assert w[-1]['raw_density_variation']<0.05
    assert w[-1]['mean_abs_pcm']<=1


def test_continuous_raw_variation_and_quiet(host):
    live=run(host,'steady')
    assert all(w['raw_density_variation']>0.05 for w in live['windows'])
    assert all(w['mean_abs_pcm']>2000 for w in live['windows'])
    quiet=run(host,'quiet')
    assert all(w['mean_abs_pcm']<=2 for w in quiet['windows'])
    assert all(w['raw_density_variation']<0.05 for w in quiet['windows'])


def test_uart_exclusion_does_not_key_rf(host):
    x=subprocess.run([str(host),'uart'],capture_output=True,text=True,check=True,timeout=15)
    assert 'UART exclusion PASS' in x.stdout


def test_corrupted_raw_mailbox_is_rejected(host):
    x=subprocess.run([str(host),'steady'],capture_output=True,text=True,check=True,timeout=15)
    encoded=next(v.removeprefix('encoded=') for v in x.stdout.splitlines() if v.startswith('encoded='))
    data=bytearray(base64.b64decode(encoded[5:]));data[8]^=8
    with pytest.raises(ValueError,match='checksum'):
        decode_mic_result(b'\1'+base64.b64encode(data))
