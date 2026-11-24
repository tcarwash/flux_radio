"""Focused tests for install-aware SOTTO VFO mode predicate."""
from tools.k6_sotto_installed_vfo import (
    is_sotto_installed_module, sotto_mode_installed, vfo_label,
    on_select_toggle, ptt_allowed,
)


def test_exact_names_only():
    assert is_sotto_installed_module(rc=0, flags=1, name="SOTTO TEST")
    assert is_sotto_installed_module(rc=0, flags=1, name="SOTTO RFTEST")
    assert not is_sotto_installed_module(rc=0, flags=1, name="SOTTO")
    assert not is_sotto_installed_module(rc=0, flags=1, name="SOTTO TEST X")
    assert not is_sotto_installed_module(rc=-1, flags=1, name="SOTTO TEST")
    assert not is_sotto_installed_module(rc=0, flags=0, name="SOTTO TEST")


def test_removal_returns_fm_and_ptt_guarded():
    assert not sotto_mode_installed([{"rc": 0, "flags": 1, "name": "SURF"}])
    assert vfo_label(legacy="FM", sotto_installed=False, sotto_selected=True) == "FM"
    assert on_select_toggle(legacy="FM", sotto_installed=False, sotto_selected=True) == "FM"
    assert not ptt_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert ptt_allowed(sotto_selected=True, sotto_ready=True, legacy_allowed=True)


def test_cycle_preserved():
    assert on_select_toggle(legacy="CWF", sotto_installed=True, sotto_selected=False) == "SOTTO"
    assert on_select_toggle(legacy="CWF", sotto_installed=False, sotto_selected=False) == "FM"
    assert on_select_toggle(legacy="SOTTO", sotto_installed=True, sotto_selected=True) == "FM"
