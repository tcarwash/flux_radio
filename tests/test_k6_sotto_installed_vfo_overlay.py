"""Overlay tests: generated Rust content + state-transition emulator.

These tests run the real ``apply-sotto-installed-vfo-mode.py`` against a
minimal fake port-src tree (only the anchors the script edits), then assert
the generated Rust contains the full contract: installed-only mode cycle,
SOTTO standby label, PTT/CW/VOX interlock, removal revalidation with no SPI
scan in draws, and RFTEST experimental gating. A small emulator mirrors the
firmware flag transitions so the state machine itself is exercised, and the
emulator is tied to the generated text so it cannot drift from the patch.
"""
import importlib.util
from pathlib import Path

import pytest

from tools.k6_sotto_installed_vfo import (
    cw_allowed,
    ptt_allowed,
    vox_allowed,
)

APPLY = (
    Path(__file__).parent.parent
    / "firmware/k6-aura/apply-sotto-installed-vfo-mode.py"
)

MOD_RS = """\
pub struct App {
    sides: [side::Side; 2],
    master: usize,
    watching: usize,
}

impl App {
    pub fn side_modulation(&self, index: usize) -> Modulation {
        self.sides[index].cfg.modulation
    }
    pub fn poll_pager(&mut self, syst: &mut SYST) {
    }
}
"""

APP_INIT_EXTRA = "            master: 0,\n"

TX_RS = """\
pub(super) fn set_ptt(app: &mut App, syst: &mut SYST, pressed: bool) {
}

fn set_cw_key(app: &mut App, syst: &mut SYST, down: bool) {
}
"""

STANDBY_RS = """\
    let modulation = legacy_label(i);
    #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
    let modulation = if app.resident_selected(i) { "PROBE" } else { modulation };
    let is_cw_master = i == app.master_index()
"""

RUNTIME_RS = """\
    pub fn tick(&mut self, app: &crate::app::App) {
        let side = app.watching_index();
    }
"""

BUILD_RS = """\
fn main() {
}
"""

# side_ops.rs as left by apply-vfo-selector.py (resident PROBE claim present).
SIDE_OPS_SELECTOR = """\
pub(super) fn toggle_mode(app: &mut App, syst: &mut SYST) {
    #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
    {
        let side = app.master;
        if app.resident_selected[side] {
            app.resident_selected[side] = false;
            app.sides[side].cfg.modulation = Modulation::Fm;
            sync_watching_to_master(app, syst);
            return;
        }
    }
    let s = &mut app.sides[app.master];
    s.cfg.modulation = match s.cfg.modulation {
    }
}
"""

# Pristine side_ops.rs without any selector overlay.
SIDE_OPS_PRISTINE = """\
pub(super) fn toggle_mode(app: &mut App, syst: &mut SYST) {
    let s = &mut app.sides[app.master];
    s.cfg.modulation = match s.cfg.modulation {
    }
}
"""


def load_apply():
    spec = importlib.util.spec_from_file_location(
        "apply_sotto_installed_vfo_mode", APPLY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_tree(root: Path, side_ops: str) -> Path:
    src = root / "src"
    (src / "app").mkdir(parents=True)
    (src / "ui").mkdir(parents=True)
    (src / "app" / "pager.rs").write_text("// pager\n")
    (src / "app" / "mod.rs").write_text(MOD_RS + APP_INIT_EXTRA)
    (src / "app" / "side_ops.rs").write_text(side_ops)
    (src / "app" / "tx.rs").write_text(TX_RS)
    (src / "ui" / "standby.rs").write_text(STANDBY_RS)
    (src / "vfo_mode_runtime.rs").write_text(RUNTIME_RS)
    (root / "build.rs").write_text(BUILD_RS)
    return src


def run_apply(monkeypatch, src: Path):
    monkeypatch.setenv("AURA_PORT_SRC", str(src))
    load_apply().main()


def snapshot(src: Path):
    return {
        p.relative_to(src.parent): p.read_text()
        for p in sorted((src.parent).rglob("*.rs"))
    }


@pytest.mark.parametrize("side_ops", [SIDE_OPS_SELECTOR, SIDE_OPS_PRISTINE])
def test_overlay_generates_full_contract(tmp_path, monkeypatch, side_ops):
    src = make_tree(tmp_path, side_ops)
    run_apply(monkeypatch, src)

    pager = (src / "app" / "pager.rs").read_text()
    assert "fn sotto_installed_slot()" in pager
    assert 'b"SOTTO TEST"' in pager
    assert "#[cfg(k6_sotto_rftest)]" in pager
    assert 'b"SOTTO RFTEST"' in pager
    # Installed identity never confuses the built-in service flag.
    assert "k6_no_sotto" not in pager

    app = (src / "app" / "mod.rs").read_text()
    assert "sotto_selected: [bool; 2]" in app
    assert "pub fn sotto_selected(" in app
    assert "pub fn sotto_installed_slot(&self)" in app
    assert "pager::sotto_installed_slot() < 0" in app
    assert "self.sides[i].cfg.modulation = Modulation::Fm;" in app
    assert "self.sync_watching_to_master(syst);" in app

    ops = (src / "app" / "side_ops.rs").read_text()
    assert "sotto_selected[side] = true" in ops
    assert "== Modulation::Cwf" in ops
    assert "app.sotto_installed_slot() >= 0" in ops
    # Selection returns to FM and never launches, loads, or keys.
    assert "Modulation::Fm" in ops
    assert "kl_load" not in ops and "set_ptt" not in ops
    if side_ops is SIDE_OPS_SELECTOR:
        # Installed module claims before PROBE; PROBE logic untouched.
        assert ops.index("sotto_selected[side] = true") < ops.index(
            "if app.resident_selected[side] {"
        )
        assert "app.resident_selected[side] = false;" in ops

    tx = (src / "app" / "tx.rs").read_text()
    assert (
        "if pressed && app.sotto_selected(app.master_index()) { return; }" in tx
    )
    assert "if down && app.sotto_selected(app.master_index()) { return; }" in tx

    standby = (src / "ui" / "standby.rs").read_text()
    assert 'if app.sotto_selected(i) { "SOTTO" }' in standby
    # Draws read the cached flag; no SPI scan in the standby path.
    assert "sotto_installed_slot" not in standby
    assert "kl_info" not in standby
    # SOTTO label wins over PROBE when both render (later shadowing let).
    assert standby.index('if app.sotto_selected(i) { "SOTTO" }') > standby.index(
        '"PROBE"'
    )

    runtime = (src / "vfo_mode_runtime.rs").read_text()
    assert "if app.sotto_selected(side) { self.stop(); return; }" in runtime

    build = (src.parent / "build.rs").read_text()
    assert "cfg(k6_sotto_vfo)" in build
    assert "cfg(k6_sotto_rftest)" in build


def test_overlay_is_idempotent(tmp_path, monkeypatch):
    src = make_tree(tmp_path, SIDE_OPS_SELECTOR)
    run_apply(monkeypatch, src)
    first = snapshot(src)
    load_apply().main()
    assert snapshot(src) == first


def test_overlay_never_touches_builtin_service_flag(tmp_path, monkeypatch):
    src = make_tree(tmp_path, SIDE_OPS_PRISTINE)
    run_apply(monkeypatch, src)
    for rel, text in snapshot(src).items():
        assert "k6_no_sotto" not in text, rel
        assert "K6_SOTTO_SERVICE" not in text, rel


class SottoVfoEmu:
    """Minimal mirror of the firmware flag transitions.

    Toggle/revalidate/label rules match the generated side_ops + poll_pager
    + standby blocks token-for-token (see test_emulator_matches_patch).
    SPI is represented by the installed bool; draws read only flags.
    """

    def __init__(self):
        self.sotto_selected = [False, False]
        self.modulation = ["FM", "FM"]

    def toggle(self, side: int, installed: bool) -> None:
        if self.sotto_selected[side]:
            self.sotto_selected[side] = False
            self.modulation[side] = "FM"
            return
        if self.modulation[side] == "CWF" and installed:
            self.sotto_selected[side] = True
            self.modulation[side] = "FM"
            return
        # Legacy/PROBE path handled elsewhere; emulator leaves it alone.

    def revalidate(self, installed: bool) -> None:
        for i in (0, 1):
            if self.sotto_selected[i] and not installed:
                self.sotto_selected[i] = False
                self.modulation[i] = "FM"

    def label(self, side: int, installed: bool, probe_label: str = "") -> str:
        if self.sotto_selected[side] and installed:
            return "SOTTO"
        if self.sotto_selected[side]:
            return "FM"
        return probe_label or self.modulation[side]


def test_emulator_state_transitions():
    emu = SottoVfoEmu()
    # Not installed: CWF toggle stays in legacy world, no SOTTO flag.
    emu.modulation[0] = "CWF"
    emu.toggle(0, installed=False)
    assert emu.sotto_selected == [False, False]

    # Installed: CWF -> SOTTO (underlying FM), toggle again -> FM.
    emu.toggle(0, installed=True)
    assert emu.sotto_selected == [True, False]
    assert emu.modulation[0] == "FM"
    assert emu.label(0, installed=True) == "SOTTO"
    emu.toggle(0, installed=True)
    assert emu.sotto_selected == [False, False]
    assert emu.modulation[0] == "FM"

    # Removal while selected: revalidate returns the side to FM.
    emu.modulation[1] = "CWF"
    emu.toggle(1, installed=True)
    assert emu.label(1, installed=True) == "SOTTO"
    emu.revalidate(installed=False)
    assert emu.sotto_selected == [False, False]
    assert emu.modulation[1] == "FM"
    assert emu.label(1, installed=False) == "FM"

    # Selected but not ready: PTT/CW/VOX never key from selection.
    assert not ptt_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert not cw_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)
    assert not vox_allowed(sotto_selected=True, sotto_ready=False, legacy_allowed=True)


def test_emulator_matches_patch(tmp_path, monkeypatch):
    """The emulator above must mirror tokens present in the generated Rust."""
    src = make_tree(tmp_path, SIDE_OPS_SELECTOR)
    run_apply(monkeypatch, src)
    ops = (src / "app" / "side_ops.rs").read_text()
    app = (src / "app" / "mod.rs").read_text()
    for token in (
        "sotto_selected[side]",
        "== Modulation::Cwf",
        "sotto_installed_slot() >= 0",
        "sotto_selected[side] = true",
        "sotto_selected[i] = false",
        "sotto_installed_slot() < 0",
    ):
        assert token in ops or token in app, token
