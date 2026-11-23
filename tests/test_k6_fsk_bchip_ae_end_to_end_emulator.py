"""Target-machine code -> modeled RFIC -> synthetic IQ -> independent RX.

The Cortex-M0 machine code MUST execute in Unicorn before IQ is generated.
The RFIC model is not validated B silicon, and the RF IQ is not captured RF.
All scoring uses a public preamble/sync and fixed known 4800-baud receiver;
expected transmitter data is used only AFTER the SDR-like bit decisions.
"""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'tools'))
from test_k6_fsk_bchip_ae_emulator import emulate
from test_k6_fsk_bchip_ae_qual import synth
from k6_fsk_bchip_ae_analyze import score_iq,assess_ae


def _preload_wire(frame):
    assert len(frame)==49
    return b''.join(x.to_bytes(2,'little') for x in frame)


def _modeled_prefix_wire(frame):
    return b'\xf8\x00'+_preload_wire(frame)[:96]


@pytest.mark.parametrize('first_prefix,second_prefix',[(False,False),(False,True),(True,False),(True,True)])
@pytest.mark.parametrize('encoding',['bit14','event14'])
def test_compiled_cortex_code_to_synthetic_iq_to_independent_48_word_receiver(
    first_prefix,second_prefix,encoding
):
    pytest.importorskip('unicorn')
    ret,telemetry,after,writes,tx,chip,baseline=emulate(encoding=encoding)
    assert ret==98 and len(chip.frames)==2
    assert assess_ae(telemetry)['provisional_b_ae_present']
    pre=(first_prefix,second_prefix)
    rf_bytes=[_modeled_prefix_wire(frame) if flag else _preload_wire(frame)
              for frame,flag in zip(chip.frames,pre)]
    receiver=score_iq(synth(rf_bytes,seed=101))
    assert receiver['result']=='RF_PAYLOAD_PASS',receiver
    assert receiver['frames_found']==2
    assert [x['control_prefix_offset_bytes'] for x in receiver['frames']]==[
        2 if flag else 0 for flag in pre]
    assert receiver['zero_payload_bit_errors_if_pass']
    assert not chip.unsafe_mid_tx_fifo_writes
    assert tx['log'][-1][0]=='tx_off'


def test_single_bit_flip_between_emulated_register_fifo_and_iq_fails_receiver():
    pytest.importorskip('unicorn')
    ret,telemetry,after,writes,tx,chip,baseline=emulate(encoding='bit14')
    assert ret==98 and assess_ae(telemetry)['provisional_b_ae_present']
    a,b=map(_preload_wire,chip.frames)
    bad=bytearray(b);bad[31]^=0x08
    report=score_iq(synth([a,bytes(bad)],noise=.01,seed=88))
    assert report['result']=='RF_PAYLOAD_FAIL'
    assert report['frames_found']==2
    assert [x['exact_48_word_data'] for x in report['frames']]==[True,False]


def test_missing_ae_event_with_clean_emulated_payload_is_not_a_joint_pass():
    pytest.importorskip('unicorn')
    ret,telemetry,after,writes,tx,chip,baseline=emulate(ae=False)
    assert ret==98 and len(chip.frames)==2
    clean=score_iq(synth([_preload_wire(x) for x in chip.frames]))
    assert clean['result']=='RF_PAYLOAD_PASS'
    assert not assess_ae(telemetry)['provisional_b_ae_present']
    assert clean['result']=='RF_PAYLOAD_PASS' and not assess_ae(telemetry)['provisional_b_ae_present']
