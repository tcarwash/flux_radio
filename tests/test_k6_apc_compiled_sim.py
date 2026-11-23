"""Compiled APC API27 firmware ABI smoke test; simulator only, no real radio.

An explicitly non-transmitting module checks that APC callbacks reject calls
unless an active, validated low-power TX exists. It also validates the level27
append-only ABI and that the module library and SPI remain byte-identical.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_user_store_migrate import BASE,END,compact
from k6_live import LiveLink
import k6_live
from test_k6_capability_sim import simulated_radio

SRC=ROOT/"c/k6/mod/apc_api27_idle.c"
SLOT=30


def test_level27_idle_callback_refuses_without_tx(simulated_radio, monkeypatch):
    image=os.environ.get("K6_SIM_APC_IMAGE")
    if not image:
        pytest.skip("Set K6_SIM_APC_IMAGE to a separately compiled APC bench image")
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",
                        SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    code,entry=k6mod.build(str(SRC))
    assert k6mod.source_level(str(SRC))==27
    envelope=k6mod.image(code,entry,123,None,level=27,lib=SLOT)
    port.board.flash_data[BASE:END]=compact({SLOT:envelope})[0]
    before=bytes(port.board.flash_data)
    link=LiveLink(port,timeout=3.,initial_seq=0)
    assert link.module_capabilities()["module_api_max"]==27
    initial=link.status()
    assert initial["standby_idle"] and not initial["transmitting"]
    result=link.run_module(SLOT,timeout=12.,allow_higher_api=True)
    assert result["returned"],result
    assert result["result"]==27,result
    assert "APC-27 idle refuses; no TX" in result["reports"],result
    final=link.status()
    assert final["standby_idle"] and not final["transmitting"]
    assert final["vfos"]==initial["vfos"]
    assert port.board.fault is None
    assert bytes(port.board.flash_data)==before
