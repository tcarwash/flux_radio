"""Compiled firmware emulator: screen capture and virtual UI key ABI."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink

ROOT=Path(__file__).resolve().parents[1]


def test_compiled_live_screen_and_key(simulated_radio,monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image or os.environ.get("K6_SIM_REMOTE_UI")!="1":
        pytest.skip("Requires explicit K6_REMOTE_UI=1 compiled image")
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(
        monotonic=port.monotonic,sleep=port.sleep))
    link=LiveLink(port,timeout=4.0,initial_seq=0)
    features=set(link.capabilities()["features"])
    assert "screenshot" in features and "virtual_keys" in features
    initial=link.status()
    assert not initial["transmitting"]
    frame=link.screenshot()
    assert isinstance(frame,bytes) and len(frame)==1024
    assert link.virtual_key("MENU")["queued"]
    port.sleep(.20)
    after=link.status()
    assert not after["transmitting"]
    frame2=link.screenshot()
    assert len(frame2)==1024
    # The compiled emulator's LCD model is not necessarily a visible panel.
    # Verify the full protocol and no RF or VFO changes instead of claiming
    # that a particular menu layout is physically displayed by the emulator.
    assert [v["hz"] for v in after["vfos"]]==[v["hz"] for v in initial["vfos"]]
