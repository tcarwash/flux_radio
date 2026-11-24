"""Opt-in actual compiled K6 ARM API29 FSK TX and half-duplex SoapyFang IQ."""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from tools.k6_sotto_api29_async_gate import run

ROOT=Path(__file__).resolve().parents[1]


def test_actual_arm_fsk29_success_preload_and_half_duplex_iq():
    image=os.environ.get("K6_SIM_SOTTO_RX31_IMAGE")
    elf=os.environ.get("K6_SIM_SOTTO_RX31_ELF")
    if not image or not elf or os.environ.get("K6_SIM_SOTTO_ASYNC_FSK")!="1":
        pytest.skip("Explicit K6_SOTTO_ASYNC_FSK=1 matched ELF/image required")
    result=run(Path(elf),Path(image),ROOT)
    assert result["result"]=="PASS"
    assert result["arm_module_runner"]
    assert not result["hardware_used"] and result["rf_simulated_only"]
    assert result["api29_implemented"] and not result["api30_mic_implemented"]
    cases={r["case"]:r for r in result["cases"]}
    assert set(cases)=={"success","rx_tx_rx","bad_guard","abort_early","timeout_poll"}
    for name in ("success","rx_tx_rx"):
        r=cases[name]
        assert r["module_return"]==9
        assert r["virtual_fsk"]["completed"]==1
        assert r["virtual_fsk"]["aborted"]==0
        assert r["virtual_fsk"]["overflows"]==r["virtual_fsk"]["rejected"]==0
        assert r["words_written"]==33
        assert r["independent_iq_receive"]["frames"]==1
        assert r["independent_iq_receive"]["match"]
        assert r["independent_iq_receive"]["fsk"]["words"]==33
        assert r["pa_off"]
    assert cases["rx_tx_rx"]["api31_calls"]==3
    assert cases["bad_guard"]["module_return"]==-20
    assert cases["bad_guard"]["air_bursts"]==0
    assert cases["bad_guard"]["words_written"]==0
    assert cases["abort_early"]["module_return"]==8
    assert cases["abort_early"]["virtual_fsk"]["aborted"]==1
    assert cases["abort_early"]["virtual_fsk"]["active"] is False
    assert cases["timeout_poll"]["module_return"]==-12
    assert cases["timeout_poll"]["virtual_fsk"]["completed"]==1
    assert cases["timeout_poll"]["virtual_fsk"]["active"] is False
    assert cases["timeout_poll"]["pa_off"]
