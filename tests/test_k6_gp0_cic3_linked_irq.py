"""Opt-in linked ARM CIC3 filter ISR regression with private matching ELF+image.

Never accesses a radio or SDR. Requires:
K6_GP0_CIC3_IMAGE_DIR=/path/to/recovery/cic3-matched-build pytest ...
"""
import os
from pathlib import Path
import pytest
from tools.k6_gp0_linked_irq_profile import profile

IMAGE_DIR = os.environ.get("K6_GP0_CIC3_IMAGE_DIR")


@pytest.fixture(scope="module")
def linked_cic3():
    if not IMAGE_DIR:
        pytest.skip("private linked CIC3 ELF/BIN not requested")
    root=Path(IMAGE_DIR)
    elf=root/"flux-cic3.elf"
    image=root/"flux-cic3.bin"
    if not (elf.is_file() and image.is_file()):
        pytest.skip("exact CIC3 firmware artifacts missing")
    return elf,image


@pytest.mark.parametrize("pattern",["tone","silence"])
def test_real_linked_thumb_cic3_96_half_irqs_are_bounded(linked_cic3,pattern):
    elf,image=linked_cic3
    result=profile(elf,image,halves=96,groups_pattern=pattern)
    assert result["real_linked_rust_isr"] and result["not_cycle_accurate"]
    assert result["pcm_samples"]==70
    assert result["ring_lost"]==0
    assert result["ring_offset"]==70
    assert result["irq_instructions_mean"] < 1200
    assert result["irq_instructions_max"] < 1250
    assert result["irq_instructions_total"] > 96000


def test_stale_elf_is_rejected_before_execution(linked_cic3,tmp_path):
    elf,image=linked_cic3
    mutated=tmp_path/"wrong.bin"
    raw=bytearray(image.read_bytes())
    raw[500]^=1
    mutated.write_bytes(raw)
    with pytest.raises(ValueError,match="ELF and linked MCU image differ"):
        profile(elf,mutated,halves=8)
