"""Opt-in real compiled ARM Sotto microphone -> encoder -> FSK -> virtual IQ.

Requires a byte-matched experimental API28-31 K6_RAM0=1 ELF/image pair;
never accesses a serial device, radio, physical microphone, or SDR.
"""
import os
from pathlib import Path
import pytest
from tools.k6_sotto_live_arm_gate import run
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('blocks',[8,32,80])
def test_live_sotto_real_arm_simulation(blocks):
    if os.environ.get('K6_SIM_SOTTO_LIVE_ARM')!='1':
        pytest.skip('Set K6_SIM_SOTTO_LIVE_ARM=1 with matching ELF/image')
    elf=os.environ.get('K6_SIM_SOTTO_LIVE_ELF')
    image=os.environ.get('K6_SIM_SOTTO_LIVE_IMAGE')
    if not elf or not image:pytest.fail('Missing explicit K6_SIM_SOTTO_LIVE_ELF/IMAGE')
    r=run(Path(elf).resolve(),Path(image).resolve(),ROOT,blocks=blocks,
          max_quanta=24000 if blocks==8 else 90000)
    assert r['result']=='PASS'
    assert r['real_arm_live_codec'] and r['real_arm_mic_isr']
    assert r['pcm']['samples']>=blocks*80
    assert r['pcm']['peak_isr_cycles']<12000
    assert r['pcm']['max_depth']<=4
    assert r['pcm']['lost']==0
    assert r['compiled_module_return']>0
    assert r['rf']['completed']==r['frames']>=3
    assert r['virtual_iq']['frames']==r['frames']
    assert r['virtual_iq']['words']==33*r['frames']
    assert r['compiled_arm_receiver']['packets']==r['frames']
    assert r['compiled_arm_receiver']['fifo_words']==33*r['frames']
    assert r['compiled_arm_receiver']['return_value']==r['frames']
    assert r['compiled_arm_receiver']['dispatcher_calls']==1
    assert r['compiled_arm_receiver']['rx_api_calls']>100
    assert r['compiled_arm_receiver']['overruns']==0
    assert r['compiled_arm_receiver']['underflows']==0
    assert not r['rf']['overflows'] and not r['rf']['rejected']
    assert r['session']['complete'] and not r['session']['invalid']
    assert r['playback']['verified']
    assert r['playback']['codec_frames']==r['compiled_module_return']
    assert r['playback']['pcm_samples']==320*r['compiled_module_return']
    assert r['playback']['speaker_blocks']==2*r['compiled_module_return']
    assert 0<r['playback']['peak_pcm_blocks']<=4
    assert 0<r['playback']['peak_encoded_frames']<=16
    assert r['playback']['peak_amplitude']>0
    assert not r['playback']['radio_speaker_driver_tested']
    assert r['pa_restored'] and r['resources_read_only']
    assert r['physical_rf'] is False and r['physical_mic'] is False
