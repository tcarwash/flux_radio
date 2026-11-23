"""Zero-radio FD6818B almost-empty IRQ qualifications and blind RF decisions."""
from pathlib import Path
import numpy as np
import pytest
from modem import k6mod
from tools.k6_fsk_bchip_ae_analyze import (expected_packet,score_iq,parse_ae_report,assess_ae,extract_module_lines,GUARD)
from tools.k6_fsk_bchip_ae_bench import preflight
from tools.k6_fsk_sotto_sync_rx import FRAME_MARK

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/fsk_bchip_ae_qual.c'
ARMED=ROOT/'c/k6/mod/fsk_bchip_ae_qual_armed.c'


def synth(payloads, *,noise=.025,fs=128000,seed=9):
    """Synthetic FM IQ at public PHY, no payload-aided acquisition."""
    baud=4800
    streams=[np.unpackbits(np.frombuffer(FRAME_MARK+p,dtype=np.uint8)) for p in payloads]
    interval=len(streams[0])/baud+.035
    n=round((.25+len(streams)*interval+.15)*fs)
    freq=np.zeros(n)
    for k,bits in enumerate(streams):
        i=round((.25+k*interval)*fs)
        indexes=np.floor(np.arange(n-i)/fs*baud).astype(np.int32)
        size=int(np.count_nonzero(indexes<len(bits)))
        freq[i:i+size]=np.where(bits[indexes[:size]]==1,1450.,-1450.)
    iq=np.exp(2j*np.pi*np.cumsum(freq)/fs)
    if noise:
        rng=np.random.default_rng(seed)
        iq+=noise*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    return iq


def irq_lines(*, first24=83,first8=137,mode='bit'):
    lines=[]
    for k,(threshold,first,done) in enumerate(((24,first24,196),(8,first8,197))):
        b=first if mode=='bit' else 0xffff
        e=first if mode=='evt' else 0xffff
        raw=0x4000 if mode=='bit' else 0x000e
        lines.extend([f'AE{k} th={threshold:04X} bit={b:04X} evt={e:04X}',
            f'AE{k} done={done:04X} irq={raw:04X} last=0000',
            f'AE{k} polls=009B status=0001'])
    return lines


def test_compiles_inert_and_enabled_preflight_in_offline_mode():
    inert,entry=k6mod.build(str(SOURCE))
    assert entry==0 and 0<len(inert)<512
    result=preflight()
    assert result['code_bytes']==1666
    assert len(result['code_sha256'])==64
    assert k6mod.source_level(str(ARMED))==3
    assert 'TO_BE_PINNED' not in (ROOT/'tools/k6_fsk_bchip_ae_bench.py').read_text()


def test_flags_do_not_allow_unsupervised_enabled_source(tmp_path):
    for ack1,ack2 in [(0,0),(0,1),(1,0)]:
        file=tmp_path/'flags.c'
        file.write_text(f'#define FSK_AE_QUAL_ENABLE 1\n'
                        f'#define FSK_AE_QUAL_FIXTURE_ACK {ack1}\n'
                        f'#define FSK_AE_QUAL_WATCHDOG_ACK {ack2}\n'
                        f'#include "{SOURCE}"\n')
        with pytest.raises(Exception):k6mod.build(str(file))


def test_threshold_masks_and_no_mid_tx_write_source_review():
    source=SOURCE.read_text()
    saved=0x3044
    assert ((saved&~0x03f8)|(24<<3))==0x30c4
    assert ((saved&~0x03f8)|(8<<3))==0x3044
    assert '(original_5e&~(uint16_t)0x03f8u)|(threshold<<3)' in source
    assert 'W(R_WORK,IRQ_TX_DONE|IRQ_AE_BIT)' in source
    assert 'if(wr(a,R_IRQ,0u)!=0)return -13;' in source
    assert source.index('if(wr(a,R_IRQ,0u)!=0)return -13;')<source.index('irq=a->fd_read(R_IRQ);')
    assert 'W(R_LENGTH,0x6100u)' in source
    assert source.index('W(R_FIFO,i==48u?0xa55au:fsk_lab_words[i]);')<source.index('W(R_CTRL,0x08f8u);')
    assert source.count('W(R_FIFO,')==1
    assert source.count('send_one(api,k,saved[N_SAVED-1],&trace[k])')==1
    assert 'FSK_LEASE_MS 1900u' in source
    assert not any(x in source for x in ('fl_prog(', 'fl_erase(', 'cal_write('))


def test_blind_sync_recovers_clean_and_control_prefix_synthetic():
    expected=expected_packet()
    assert len(expected)==96
    payloads=[expected+GUARD.to_bytes(2,'little'), b'\xf8\x00'+expected]
    report=score_iq(synth(payloads))
    assert report['result']=='RF_PAYLOAD_PASS',report
    assert report['frames_found']==2
    assert [x['control_prefix_offset_bytes'] for x in report['frames']]==[0,2]
    assert all(x['sync_bit_errors']==0 for x in report['frames'])


def test_blind_sync_rejects_corrupted_data_even_if_guard_present():
    expected=bytearray(expected_packet())
    damaged=bytearray(expected);damaged[63]^=0x04
    packets=[bytes(expected)+GUARD.to_bytes(2,'little'),b'\xf8\x00'+bytes(damaged)]
    report=score_iq(synth(packets))
    assert report['result']=='RF_PAYLOAD_FAIL'
    assert report['frames_found']==2
    assert [x['strict_pass'] for x in report['frames']]==[True,False]


def test_threshold_shift_event_and_bit_modes_are_observations_only():
    for mode in ('bit','evt'):
        r=assess_ae(irq_lines(mode=mode))
        assert r['provisional_b_ae_present']
        assert r['not_yet_safe_for_refill']
        yes=next(x for x in r['possible_interrupt_encodings'] if x['mode']==mode)
        assert yes['measured_delta_ms']==54
        assert yes['passes_timing_discriminator']
        no=next(x for x in r['possible_interrupt_encodings'] if x['mode']!=mode)
        assert not no['passes_timing_discriminator']
    r=assess_ae(irq_lines(first24=1,first8=2,mode='bit'))
    assert not r['provisional_b_ae_present']


def test_0x0800_irq_trace_does_not_count_as_bchip_AE():
    trace=irq_lines(mode='evt')
    trace=[x.replace('evt=0053','evt=FFFF') for x in trace]
    assert not assess_ae([x.replace('irq=000E','irq=0800') for x in irq_lines(first24=0xffff,first8=0xffff,mode='bit')])['provisional_b_ae_present']
    with pytest.raises(ValueError):parse_ae_report(irq_lines()[:3])
    with pytest.raises(ValueError):parse_ae_report(irq_lines()+['AE1 th=0008'])


def test_no_device_contact_without_ack(tmp_path):
    import subprocess,sys
    r=subprocess.run([sys.executable,'tools/k6_fsk_bchip_ae_bench.py','--prefix',str(tmp_path/'DO_NOT_RUN')],
                     capture_output=True,text=True)
    assert r.returncode!=0
    assert 'dummy load, operator approval and separate queue ACK' in r.stderr
    assert not list(tmp_path.iterdir())


def test_existing_reversible_bench_report_requires_restoration_and_completion():
    m={'hardware_lab_transaction':{'original_module_restored':True,
       'original_radio_frequency_restored':True,
       'transmission_confirmed_by_firmware':True,
       'module_completion':{'returned':True,'result':98,'reports':irq_lines(mode='bit')}}}
    assert assess_ae(extract_module_lines(m))['provisional_b_ae_present']
    m['hardware_lab_transaction']['original_radio_frequency_restored']=False
    with pytest.raises(ValueError,match='No verified completed'):
        extract_module_lines(m)
    m['hardware_lab_transaction']['original_radio_frequency_restored']=True
    m['hardware_lab_transaction']['module_completion']['result']=97
    with pytest.raises(ValueError,match='No verified completed'):
        extract_module_lines(m)
