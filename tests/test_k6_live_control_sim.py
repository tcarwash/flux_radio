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


def test_compiled_live_module_run(simulated_radio, monkeypatch):
    image = os.environ.get('K6_SIM_LIVE_IMAGE')
    if not image:
        pytest.skip('set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image')
    from modem import k6mod
    from k6_install import LIB_BASE, LIB_SLOT, InstallError
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, 'time', SimpleNamespace(monotonic=port.monotonic))
    code, entry = k6mod.build(str(Path(__file__).resolve().parents[1] / 'c/k6/mod/hello.c'))
    entries = {}
    for slot, level, page, corrupt in [(24, 1, False, False), (25, 3, False, False), (26, 1, True, False), (27, 1, False, True)]:
        envelope = k6mod.image(code, entry, 100 + slot, None, level=level, lib=slot, page=page)
        if corrupt:
            envelope = envelope[:-1] + bytes([envelope[-1] ^ 1])
        entries[slot] = envelope
    if os.environ.get('K6_SIM_LIVE_PACKED') == '1':
        from k6_user_store_migrate import compact, BASE, END
        port.board.flash_data[BASE:END] = compact(entries)[0]
    else:
        for slot, envelope in entries.items():
            offset = LIB_BASE + slot * LIB_SLOT
            port.board.flash_data[offset:offset+len(envelope)] = envelope
    before = bytes(port.board.flash_data)
    link = LiveLink(port, timeout=3.0, initial_seq=0)
    status = link.status()
    assert status['standby_idle'] and not status['transmitting']
    for slot in (23, 25, 26):
        with pytest.raises(InstallError, match='Unsupported'):
            link.run_module(slot)
    result = link.run_module(24, timeout=5)
    assert result['returned'] and result['result'] == 0x6b, result
    assert result['load'] == len(code)
    assert len(result['reports']) == 2 and result['reports'][0].startswith('hello world from RAM')
    assert 'sum of squares' in result['reports'][1]
    assert link.run_module(25, timeout=5, allow_higher_api=True)['result'] == 0x6b
    bad = link.run_module(27, timeout=5)
    assert not bad['returned'] and bad['load'] < 0
    after = link.status()
    assert after['standby_idle'] and after['vfos'] == status['vfos'] and not after['transmitting']
    assert bytes(port.board.flash_data) == before
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)
