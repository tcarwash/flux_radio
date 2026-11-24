"""Install-aware SOTTO as a selectable VFO mode (offline, no RF).

A VFO side renders ``SOTTO`` as selectable if and only if a valid installed
SOTTO modem module is present in the packed library. Selection never keys,
launches, or transmits; PTT stays guarded until a runtime module is actually
packet/voice ready. Removing the module returns the side to FM.

Identity rule (stable, exact, no prefix matching):
  kl_info rc == 0, flags == 0x01 (executable library entry), and the full
  NUL-trimmed module name exactly equals a known Sotto modem name.
Known names: "SOTTO TEST", "SOTTO RFTEST". The firmware's optional built-in
Sotto decoder service (k6_no_sotto / K6_SOTTO_SERVICE) is NOT the same as a
loadable installed modem and never satisfies this predicate.
"""
from __future__ import annotations

LIB_FLAG = 0x01
SOTTO_INSTALLED_NAMES = ("SOTTO TEST", "SOTTO RFTEST")
LEGACY_CYCLE = ("FM", "AM", "USB", "CW", "CWF")


def is_sotto_installed_module(*, rc: int, flags: int, name: str) -> bool:
    """Nonmutating predicate for one packed-library slot's metadata.

    Pure function for the future KISS registry: no I/O, no state.
    Only exact full-name equality counts; prefixes never match.
    """
    if rc != 0:
        return False
    if flags != LIB_FLAG:
        return False
    return name in SOTTO_INSTALLED_NAMES


def sotto_mode_installed(slots) -> bool:
    """Nonmutating predicate over an iterable of slot metadata mappings.

    Each entry must provide ``rc``, ``flags``, ``name``. Returns True if any
    slot holds a valid installed Sotto modem module.
    """
    for s in slots:
        if isinstance(s, dict):
            rc, flags, name = s.get("rc"), s.get("flags"), s.get("name")
        else:
            rc, flags, name = s.rc, s.flags, s.name
        if is_sotto_installed_module(rc=rc, flags=flags, name=name):
            return True
    return False


def vfo_label(*, legacy: str, sotto_installed: bool, sotto_selected: bool) -> str:
    """Standby label: legacy mode, or SOTTO when installed and selected."""
    if sotto_selected and sotto_installed:
        return "SOTTO"
    return legacy


def on_select_toggle(*, legacy: str, sotto_installed: bool, sotto_selected: bool) -> str:
    """Mode key with SOTTO appended after CWF; never launches or keys."""
    if sotto_selected and not sotto_installed:
        return "FM"
    if legacy == "CWF" and not sotto_selected:
        if sotto_installed:
            return "SOTTO"
        return "FM"
    if legacy == "SOTTO":
        return "FM"
    try:
        i = LEGACY_CYCLE.index(legacy)
    except ValueError:
        return "FM"
    return LEGACY_CYCLE[(i + 1) % len(LEGACY_CYCLE)]


def ptt_allowed(*, sotto_selected: bool, sotto_ready: bool, legacy_allowed: bool) -> bool:
    """PTT guard: a selected-but-not-ready SOTTO side never transmits."""
    if sotto_selected and not sotto_ready:
        return False
    return legacy_allowed
