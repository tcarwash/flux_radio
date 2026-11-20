"""Opt-in live-control regression against the compiled firmware (not RF evidence)."""
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink


def test_compiled_live_control(simulated_radio, monkeypatch):
    image = os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image")
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, "time", SimpleNamespace(monotonic=port.monotonic))
    link = LiveLink(port, timeout=3.0)
    before = bytes(port.board.flash_data)
    first = link.status()
    assert first["version"] == 1
    assert link.read(0xb000, 64)[:4] == b"AURA"
    assert link.read(0, 128) == before[:128]
    assert link.status()["vfos"] == first["vfos"]
    target = 146_520_000 if first["vfos"][first["master"]]["hz"] != 146_520_000 else 146_550_000
    tuned = link.tune(target)
    assert tuned["vfos"][tuned["master"]]["hz"] == target
    assert not tuned["transmitting"]
    assert bytes(port.board.flash_data) == before
    port.sleep(6)
    assert link.status()["vfos"][tuned["master"]]["hz"] == target
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)
