"""Compiled-FW virtual PA10/PTT and PA15/power-switch guard acceptance.

The K6 simulator validates logical firmware refusals, not RF isolation or
physical electrical behavior. No radio, UART port or transmitter is touched.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink
from k6_install import InstallError
from k6_user_store_migrate import compact, BASE, END
from modem import k6mod


@pytest.fixture
def live_test_radio(simulated_radio, monkeypatch):
    image = os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("Set K6_SIM_LIVE_IMAGE to the RX-only power-guard candidate")
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    module = k6mod.image(b"\x2a\x20\x70\x47", 0, 123, None, level=25, lib=26)
    port.board.flash_data[BASE:END] = compact({26: module})[0]
    monkeypatch.setattr(k6_live, "time",
                        SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    link = LiveLink(port, timeout=3, initial_seq=0)
    assert link.status()["standby_idle"]
    assert "coop_cancel" in link.capabilities()["features"]
    return port, link


def test_held_pa10_ptt_refuses_cooperative_run(live_test_radio):
    port, link = live_test_radio
    before = bytes(port.board.flash_data)
    port.board.set_gpio("A", 10, 0)
    port.advance(3_000_000)
    status = link.status()
    assert status["transmitting"] and not status["standby_idle"]
    with pytest.raises(InstallError, match="Radio busy"):
        link.run_module_cooperative(26, timeout=2, allow_higher_api=True)
    assert bytes(port.board.flash_data) == before
    assert port.board.fault is None
    port.board.set_gpio("A", 10, 1)
    port.advance(3_000_000)
    returned = link.status()
    assert returned["standby_idle"] and not returned["transmitting"]


def test_pa15_power_off_is_not_reported_as_safe_idle(live_test_radio):
    port, link = live_test_radio
    before = bytes(port.board.flash_data)
    port.board.set_gpio("A", 15, 1)
    port.advance(600_000)
    status = link.status()
    assert not status["standby_idle"], "power-off must not appear as safe idle RX"
    assert bytes(port.board.flash_data) == before
    assert port.board.fault is None
