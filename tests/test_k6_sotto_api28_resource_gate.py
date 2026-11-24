"""Opt-in linked Cortex-M0 resource callback via real K6 module runner."""
from pathlib import Path
import os
import pytest

from tools.k6_sotto_api28_resource_gate import run

ROOT=Path(__file__).resolve().parents[1]

def test_arm_api28_read_only_codebook_and_deck():
    image=os.environ.get("K6_SIM_SOTTO_RX31_IMAGE")
    elf=os.environ.get("K6_SIM_SOTTO_RX31_ELF")
    if not image or not elf or os.environ.get("K6_SIM_SOTTO_API28_RESOURCES")!="1":
        pytest.skip("Opt-in matching API31 image/ELF with K6_SOTTO_RESOURCES=1 required")
    r=run(Path(elf),Path(image),ROOT)
    assert r["result"]=="PASS"
    assert r["real_ARM_module_run"] and r["actual_ARM_resource_callbacks"]
    assert r["module_return"]==7
    assert r["codebook_bytes"]==30752 and r["deck_bytes"]==4182
    assert r["resource_flash_read_only"] and r["bounds_check_exercised"]
    assert not r["physical_hardware_used"] and r["packed_KUL2_tested"]
