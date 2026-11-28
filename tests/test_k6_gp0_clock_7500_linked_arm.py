"""Opt-in exact-linked Cortex-M0 ISR rate regression, offline only.

Provide K6_GP0_CLOCK_PAIR_ROOT pointing to the candidate's ignored firmware
out-clock-7500-baseline and out-clock-7500-calibrated directories. Unlike
pure-Rust decimator tests, this executes both complete MCU ELFs in Unicorn.
No radio/serial/PTT or external RF devices are used.
"""
from pathlib import Path
import os

import pytest

from tools.k6_gp0_linked_irq_profile import profile


@pytest.fixture(scope='module')
def paired_profiles():
    root=os.environ.get('K6_GP0_CLOCK_PAIR_ROOT')
    if not root:
        pytest.skip('explicit paired full-link images required')
    base=Path(root)
    if not base.is_dir():
        pytest.fail(f'paired image root does not exist: {base}')
    out={}
    for mode in ('baseline','calibrated'):
        p=base/f'out-clock-7500-{mode}'
        elf=p/f'flux-gp0-clock-{mode}.elf'
        image=p/f'flux-gp0-clock-{mode}.bin'
        assert elf.is_file() and image.is_file(),f'missing full-link ELF/BIN for {mode}'
        out[mode]=profile(elf,image,halves=96,groups_pattern='tone')
    return out


def test_only_clock_weight_changes_linked_pcm_rate_without_irq_overflow(paired_profiles):
    base=paired_profiles['baseline']
    calibrated=paired_profiles['calibrated']
    assert base['real_linked_rust_isr'] and calibrated['real_linked_rust_isr']
    assert base['firmware_bytes']==calibrated['firmware_bytes']==118408
    assert base['pcm_samples']==base['ring_offset']==70
    assert calibrated['pcm_samples']==calibrated['ring_offset']==66
    assert base['ring_lost']==calibrated['ring_lost']==0
    assert base['ring_produced']==calibrated['ring_produced']==0
    assert base['halves']==calibrated['halves']==96
    assert base['irq_instructions_max']<9000
    assert calibrated['irq_instructions_max']<9000
    assert calibrated['irq_instructions_mean']<=base['irq_instructions_mean']+10
    assert base['not_cycle_accurate'] and calibrated['not_cycle_accurate']
