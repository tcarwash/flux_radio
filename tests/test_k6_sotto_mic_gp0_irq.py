"""API30 standalone PIC MIC IRQ module with native simulated ISR/foreground."""
import base64
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
from tools.k6_mic_result import decode_mic_result
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_gp0_irq.c'
HOST=ROOT/'tests/data/k6/sotto_mic_gp0_irq_host.c'

@pytest.fixture
def exe(tmp_path):
    p=tmp_path/'mic_irq'
    subprocess.run(['clang','-std=c11','-O2',str(HOST),'-lm','-o',str(p)],
                   check=True,text=True,capture_output=True)
    return p


def run(exe,mode):
    p=subprocess.run([str(exe),mode],text=True,capture_output=True,timeout=30,check=True)
    encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines() if line.startswith('encoded='))
    assert len(encoded)==49
    return decode_mic_result(b'\x01'+encoded[5:].encode()),p.stdout


def test_actual_cortex_m0_pic_module_compiles_and_has_no_static_data():
    b,entry=k6mod.build(str(SRC))
    assert entry==16 and k6mod.module_name(b)=='MIC IRQ'
    assert len(b)<6000
    s=SRC.read_text()
    assert 'a->tx_on(2500u)' in s
    assert 'a->mic_stream(7,0,0)' in s
    assert 'a->tx_pa(' not in s
    assert 'gp0_irq_arm(' in s and 'gp0_irq_disarm(' in s
    assert s.index('wr(TIM17,1u);') < s.index('ret=gp0_irq_arm(')
    assert s.index('gp0_irq_disarm(irq);armed=0;')<s.index('maxvox=a->fd_read')


def test_continuous_input_one_two_off_and_encoded_telemetry(exe):
    for mode in ('1k','2k','silent'):
        d,t=run(exe,mode)
        assert 'MODULE PASS' in t
        assert d['version']==10 and d['measurement']=='gp0_dma_irq_8khz_pcm_stream'
        assert 3500<d['pcm_produced_samples']<10000
        assert d['pcm_blocks_consumed']>=35
        assert d['pcm_consumed_samples']==d['pcm_blocks_consumed']*80
        assert d['dma_half_interrupts']>3500
        assert d['peak_isr_timer_ticks']<9000
        assert d['missed_dma_halves']==0 and d['irq_error']==0
        assert d['peak_pcm_backlog_blocks']<=4
        assert d['vox_max']==(170 if mode=='silent' else 600)


def test_physical_cable_exclusion(exe):
    p=subprocess.run([str(exe),'uart'],text=True,capture_output=True,check=True)
    assert 'UART EXCLUSION PASS' in p.stdout


def test_old_firmware_or_unmapped_vector_fail_closed(exe):
    for mode,err in [('oldfirmware',6),('novector',26),('stall',27)]:
        d,t=run(exe,mode)
        assert d['version']==11 and d['error_code']==err and not d['completed']
        assert 'MODULE PASS' in t


def test_mailbox_crc_including_abort(exe):
    for mode in ('1k','novector'):
        p=subprocess.run([str(exe),mode],text=True,capture_output=True,check=True)
        encoded=next(line.removeprefix('encoded=') for line in p.stdout.splitlines() if line.startswith('encoded='))
        raw=bytearray(base64.b64decode(encoded[5:]));raw[4]^=1
        with pytest.raises(ValueError,match='checksum'):
            decode_mic_result(b'\x01'+base64.b64encode(raw))
