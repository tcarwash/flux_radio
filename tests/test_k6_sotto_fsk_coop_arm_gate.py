"""Explicit opt-in: exact compiled TX-only ARM host + independent IQ RX."""
import os
from pathlib import Path
import pytest
from tools.k6_sotto_fsk_coop_arm_gate import run

ROOT=Path(__file__).resolve().parents[1]


def test_real_linked_cooperative_host_preload_and_virtual_iq():
    elf=os.environ.get('K6_SIM_SOTTO_COOP_ELF')
    image=os.environ.get('K6_SIM_SOTTO_COOP_IMAGE')
    if os.environ.get('K6_SIM_SOTTO_COOP')!='1' or not elf or not image:
        pytest.skip('Matched opt-in compiled cooperative firmware ELF/image required')
    result=run(Path(elf),Path(image),ROOT)
    assert result['result']=='PASS'
    assert result['linked_arm_host'] and result['api_level']==30
    assert result['firmware_bytes']<=122880
    assert result['hardware_used'] is False and result['rf_simulated_only'] is True
    cases={x['case']:x for x in result['cases']}
    assert set(cases)=={'success','bad_guard','abort_early','timeout'}
    assert cases['success']['module_return']==9
    assert cases['success']['words_written']==33
    assert cases['success']['fifo_batch_sizes']==[8,8,8,8,1]
    assert cases['success']['rf_bursts']==1
    assert cases['success']['independent_iq_packet_exact']
    assert cases['success']['milliseconds_from_fifo_guard_to_rf']>=20
    for name,ret in [('bad_guard',-20),('abort_early',8),('timeout',-12)]:
        assert cases[name]['module_return']==ret
        assert cases[name]['words_written']==0
        assert cases[name]['rf_bursts']==0
    assert all(x['tx_ended_inactive'] for x in cases.values())
