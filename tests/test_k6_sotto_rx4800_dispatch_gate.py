"""Explicit opt-in full ARM cable-loader -> module-dispatcher -> API31 IQ."""

import os
from pathlib import Path

import pytest

from tools.k6_sotto_rx4800_dispatch_gate import run

ROOT=Path(__file__).resolve().parents[1]


def test_arm_rx31_module_cable_loader_and_normal_dispatcher():
    image=os.environ.get("K6_SIM_SOTTO_RX31_IMAGE")
    elf=os.environ.get("K6_SIM_SOTTO_RX31_ELF")
    if not image or not elf:
        pytest.skip("Paired API31 cable-only development ELF/image explicitly required")
    r=run(Path(elf),Path(image),ROOT)
    assert r["result"]=="PASS"
    assert r["api_level"]==31
    assert r["cable_chunks"]==26 and r["corrupt_cable_crc_rejected"]
    assert r["module_return"]==6
    assert r["firmware_km_run_calls"]==r["firmware_run_module_calls"]==1
    assert r["real_arm_stack_switch"] and r["real_arm_module_parser"]
    assert r["rf_packets_expected"]==r["rf_packets_received"]==6
    assert r["rf_words_received"]==198
    assert r["fifo_overruns"]==r["fifo_underflows"]==0
    assert r["module_context_cleared"]
    assert r["firmware_radio_enter_tx_calls"]==0
    assert r["simulated_only"] and not r["physical_hardware_used"]
    assert not r["production_siphash_tested"]
    assert not r["full_uart_cps_session_tested"]
