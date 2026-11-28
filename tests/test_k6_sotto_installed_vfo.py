"""Focused tests for install-aware SOTTO VFO mode predicate."""
from tools.k6_sotto_installed_vfo import (
    cw_allowed,
    is_sotto_installed_module,
    on_select_toggle,
    ptt_allowed,
    sotto_mode_installed,
    vfo_label,
    vox_allowed,
)


def test_exact_names_only():
    assert is_sotto_installed_module(rc=0, flags=1, name="SOTTO TEST")
    assert not is_sotto_installed_module(rc=0, flags=1, name="SOTTO")
    assert not is_sotto_installed_module(rc=0, flags=1, name="SOTTO TEST X")
    assert not is_sotto_installed_module(rc=-1, flags=1, name="SOTTO TEST")
    assert not is_sotto_installed_module(rc=0, flags=0, name="SOTTO TEST")


def test_rftest_is_experimental_only():
    assert not is_sotto_installed_module(rc=0, flags=1, name="SOTTO RFTEST")
    assert is_sotto_installed_module(
        rc=0, flags=1, name="SOTTO RFTEST", allow_experimental=True
    )
    slots = [{"rc": 0, "flags": 1, "name": "SOTTO RFTEST"}]
    assert not sotto_mode_installed(slots)
    assert sotto_mode_installed(slots, allow_experimental=True)


def test_removal_returns_fm_and_ptt_guarded():
    assert not sotto_mode_installed([{"rc": 0, "flags": 1, "name": "SURF"}])
    assert vfo_label(legacy="FM", sotto_installed=False, sotto_selected=True) == "FM"
    assert on_select_toggle(legacy="FM", sotto_installed=False, sotto_selected=True) == "FM"
    assert not ptt_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert ptt_allowed(sotto_selected=True, sotto_ready=True, legacy_allowed=True)


def test_cw_vox_guarded_like_ptt():
    assert not cw_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert cw_allowed(sotto_selected=True, sotto_ready=True, legacy_allowed=True)
    assert cw_allowed(sotto_selected=False, sotto_ready=False, legacy_allowed=True)
    assert not vox_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert vox_allowed(sotto_selected=True, sotto_ready=True, legacy_allowed=True)
    assert vox_allowed(sotto_selected=False, sotto_ready=False, legacy_allowed=False) is False


def test_cycle_preserved():
    assert on_select_toggle(legacy="CWF", sotto_installed=True, sotto_selected=False) == "SOTTO"
    assert on_select_toggle(legacy="CWF", sotto_installed=False, sotto_selected=False) == "FM"
    assert on_select_toggle(legacy="SOTTO", sotto_installed=True, sotto_selected=True) == "FM"
