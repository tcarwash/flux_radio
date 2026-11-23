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
    assert not status["transmitting"], "power-off must never leave RF active"
    after = bytes(port.board.flash_data)
    # With an erased simulated SPI flash, the ordinary graceful
    # shutdown may persist the 8-byte channel/VFO mode state.
    # Explicitly allow ONLY a valid first wear-levelled record in
    # SYSTEMRAN (0xE000), never calibration, settings or module writes.
    # Some firmware builds reach shutdown after this snapshot; in
    # that case there must be no SPI writes at all.
    assert before[:0xE000] == after[:0xE000]
    assert before[0xE024:] == after[0xE024:]
    assert before[0xE001:0xE01A] == after[0xE001:0xE01A]
    if after != before:
        from k6_update import crc16_xmodem
        assert before[0xE000] == 0xFF and after[0xE000] == 0xFE
        assert before[0xE01A:0xE024] == b"\xff" * 10
        payload = after[0xE01A:0xE022]
        assert after[0xE022:0xE024] == crc16_xmodem(payload).to_bytes(2, "little")
    assert port.board.fault is None
