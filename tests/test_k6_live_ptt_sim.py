"""PTT hardware-state regression against a compiled virtual K6 (never radiates)."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink
from k6_install import InstallError


@pytest.fixture
def ptt_radio(simulated_radio, monkeypatch):
    image = os.environ.get('K6_SIM_PTT_IMAGE')
    if not image:
        pytest.skip('K6_SIM_PTT_IMAGE required for compiled virtual PTT tests')
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, 'time', SimpleNamespace(monotonic=port.monotonic, sleep=port.sleep))
    link = LiveLink(port, timeout=3.0, initial_seq=0)
    assert 'ptt' in link.capabilities()['features']
    assert not link.status()['transmitting']
    return port, link


def test_ptt_lease_auto_expires_without_host(ptt_radio):
    port, link = ptt_radio
    token=b'ptt-1234'
    before=bytes(port.board.flash_data)
    link.ptt_hold(token)
    assert link.status()['transmitting'], 'SIM did not enter TX with explicit V2 lease'
    port.sleep(1.2)
    assert not link.status()['transmitting'], 'firmware did not release on lease expiry'
    assert bytes(port.board.flash_data)==before


def test_ptt_explicit_release_and_invalid_owner(ptt_radio):
    port, link = ptt_radio
    token=b'ptt-1234'
    link.ptt_hold(token)
    assert link.status()['transmitting']
    with pytest.raises(InstallError):
        link.ptt_release(b'another1')
    assert link.status()['transmitting']
    link.ptt_release(token)
    assert not link.status()['transmitting']
    assert not port.board.fault


def test_ptt_disallows_second_owner_and_abnormal_mode(ptt_radio):
    port, link = ptt_radio
    link.set_mode('AM',False)
    with pytest.raises(InstallError):
        link.ptt_hold(b'ptt-1234')
    assert not link.status()['transmitting']
    link.set_mode('FM',False)
    token=b'ptt-1234'
    link.ptt_hold(token)
    with pytest.raises(InstallError):
        link.ptt_hold(b'another1')
    link.ptt_release(token)
    assert not link.status()['transmitting']
