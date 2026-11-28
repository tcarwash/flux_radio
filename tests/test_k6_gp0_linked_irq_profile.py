"""Opt-in exact linked Cortex-M0 GP0 ISR tests; never touch a serial port.

Requires deliberately built matching private ELF/BIN images:
  K6_GP0_PROFILE_ROOT=/path/to/recovery pytest -q tests/test_k6_gp0_linked_irq_profile.py
"""
from pathlib import Path
import json
import os
import pytest
from tools.k6_gp0_linked_irq_profile import profile

RECOVERY=os.environ.get("K6_GP0_PROFILE_ROOT")
PROFILES=("legacy","aa3","fir11","fir11-late")


@pytest.fixture(scope="module")
def images():
    if not RECOVERY:
        pytest.skip("private Cortex-M0 matched images not supplied")
    root=Path(RECOVERY)
    pairs={}
    for v in PROFILES:
        p=root/f"{v}-matched-build"
        elf=p/f"flux-{v}.elf"
        image=p/f"flux-{v}.bin"
        if not elf.is_file() or not image.is_file():
            pytest.skip(f"missing optional matched image: {v}")
        pairs[v]=(elf,image)
    return pairs


@pytest.mark.parametrize("mode",PROFILES)
def test_real_linked_gp0_interrupt_succeeds_with_96_halves(images,mode):
    elf,image=images[mode]
    r=profile(elf,image,halves=96,groups_pattern="tone")
    assert r["real_linked_rust_isr"]
    assert r["not_cycle_accurate"]
    assert r["pcm_samples"]==70
    assert r["ring_lost"]==0
    assert r["ring_produced"]==0
    assert r["ring_offset"]==70
    assert r["irq_instructions_min"]>0
    assert r["irq_instructions_max"]<9000
    assert r["halves"]==96


def test_deferred_fir11_reduces_tone_instruction_count_but_not_below_aa3(images):
    results={}
    for v in PROFILES:
        elf,image=images[v]
        results[v]=profile(elf,image,halves=24,groups_pattern="tone")
    assert results["fir11-late"]["irq_instructions_mean"] < .7*results["fir11"]["irq_instructions_mean"]
    assert results["fir11-late"]["irq_instructions_mean"] > results["aa3"]["irq_instructions_mean"]
    assert results["aa3"]["irq_instructions_mean"] > results["legacy"]["irq_instructions_mean"]


def test_profiler_rejects_stale_elf_image_pair(images):
    elf,_=images["legacy"]
    _,wrong_image=images["fir11-late"]
    with pytest.raises(ValueError,match="ELF and linked MCU image differ"):
        profile(elf,wrong_image,halves=8)
