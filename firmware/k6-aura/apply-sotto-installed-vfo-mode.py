#!/usr/bin/env python3
"""Install-aware SOTTO VFO mode overlay (offline patch script, no RF).

Patches build/aura-fd6818b-port/src the same way apply-vfo-selector.py does,
with this contract:

* Mode cycle: SOTTO is appended after CWF only when a valid installed Sotto
  modem module is present (kl_info rc==0, flags==1, exact name "SOTTO TEST").
  "SOTTO RFTEST" counts only under the experimental cfg ``k6_sotto_rftest``
  (build opt-in ``K6_SOTTO_RFTEST=1``) and is never a normal final modem.
  The optional built-in Sotto decoder service (k6_no_sotto /
  K6_SOTTO_SERVICE) is a different thing and never satisfies this predicate;
  no condition here tests k6_no_sotto either way.
* Tie-break: the SOTTO claim is checked before the resident PROBE claim, so
  an installed module wins over the built-in probe representation. When no
  installed module is present the SOTTO block falls through untouched and
  PROBE plus all legacy modes behave exactly as before.
* LCD label: a selected side renders "SOTTO", winning over PROBE; legacy
  labels are otherwise preserved.
* Interlock: PTT, CW and VOX are inhibited while a SOTTO side is selected.
  VOX keys through the central PTT path, so the set_ptt guard covers both;
  CW has its own entry point and is guarded separately. Selection never
  launches, loads, or keys anything: there is no qualified SOTTO TX path yet,
  so the guard holds whenever the flag is set (a future ready signal can
  relax it; the Python model in tools/k6_sotto_installed_vfo.py already
  carries the sotto_ready parameter for that).
* Removal: availability is recomputed on every select-toggle and in
  poll_pager (which clears a stale selection back to FM and re-syncs); the
  standby draw only reads the cached bool flag and never scans SPI.
* Opt-in: build-fd6818b.sh invokes this script only when K6_SOTTO_VFO=1
  (which requires K6_VFO_SELECTOR=1) and adds --cfg k6_sotto_vfo
  (plus --cfg k6_sotto_rftest for K6_SOTTO_RFTEST=1).

Idempotent: re-running leaves the tree unchanged. Safe offline: with no
port checkout it prints a notice and changes nothing.
"""
import os
from pathlib import Path

CFG = ('#[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, k6_sotto_vfo,'
       ' not(k6_no_fsk_rx), feature = "blowfang-host"))]')


def port_src():
    ov = os.environ.get("AURA_PORT_SRC")
    if ov:
        return Path(ov)
    return Path(__file__).parent / 'build/aura-fd6818b-port/src'


def edit(path, old, new, src):
    p = src / path
    s = p.read_text()
    if new in s:
        return
    if s.count(old) != 1:
        raise SystemExit(f'{path}: expected one anchor: {old[:80]!r}')
    p.write_text(s.replace(old, new, 1))


def main():
    src = port_src()
    if not src.is_dir():
        print('no port checkout; overlay not applied (offline)')
        return

    # 1. Slot scanner: exact installed identity, RFTEST experimental-only.
    pager = src / 'app/pager.rs'
    pt = pager.read_text()
    if 'fn sotto_installed_slot()' not in pt:
        helper = CFG + '''
#[cfg_attr(not(k6_vfo_selector), allow(dead_code))]
fn sotto_names_equal(a: &[u8; 12], b: &[u8]) -> bool {
    let len = a.iter().position(|&c| c == 0).unwrap_or(a.len());
    if len != b.len() { return false; }
    a[..len].iter().zip(b.iter()).all(|(&x, &y)| x == y)
}
''' + CFG + '''
pub(super) fn sotto_installed_slot() -> i32 {
    for n in 0..32 {
        let mut info = ffi::KlInfo::default();
        if unsafe { ffi::kl_info(n, &LIB_FL, &mut info) } == 0 && info.flags == 1 {
            if sotto_names_equal(&info.name, b"SOTTO TEST") {
                return n as i32;
            }
            #[cfg(k6_sotto_rftest)]
            if sotto_names_equal(&info.name, b"SOTTO RFTEST") {
                return n as i32;
            }
        }
    }
    -1
}
'''
        pager.write_text(pt + '\n' + helper + '\n')

    # 2. App state: per-side selection flag + accessors.
    app = src / 'app/mod.rs'
    at = app.read_text()
    if 'sotto_selected: [bool; 2]' not in at:
        edit('app/mod.rs', '    sides: [side::Side; 2],',
             '    sides: [side::Side; 2],\n' + CFG + '\n    sotto_selected: [bool; 2],', src)
        at = (src / 'app/mod.rs').read_text()
    if 'sotto_selected: [false; 2]' not in at:
        edit('app/mod.rs', '            master: 0,',
             '            ' + CFG + '\n            sotto_selected: [false; 2],\n            master: 0,', src)
        at = (src / 'app/mod.rs').read_text()
    if 'pub fn sotto_selected(' not in at:
        edit('app/mod.rs', '    pub fn side_modulation(&self, index: usize) -> Modulation {',
             '    ' + CFG + '''
    pub fn sotto_selected(&self, index: usize) -> bool { self.sotto_selected[index] }
    pub fn sotto_installed_slot(&self) -> i32 { pager::sotto_installed_slot() }
    pub fn side_modulation(&self, index: usize) -> Modulation {''', src)
        at = (src / 'app/mod.rs').read_text()

    # 3. Removal / mode-change revalidation in the poll path (never in draws).
    poll_anchor = '    pub fn poll_pager(&mut self, syst: &mut SYST) {'
    at = (src / 'app/mod.rs').read_text()
    revalidate = ('        ' + CFG + '\n'
                  '        {\n'
                  '            // Installed-module removal: a selected side whose module is gone\n'
                  '            // returns to FM here. Single scan, only while a side is selected;\n'
                  '            // screen draws only read the cached flag, never SPI.\n'
                  '            let mut changed = false;\n'
                  '            for i in 0..2 {\n'
                  '                if self.sotto_selected[i] && pager::sotto_installed_slot() < 0 {\n'
                  '                    self.sotto_selected[i] = false;\n'
                  '                    self.sides[i].cfg.modulation = Modulation::Fm;\n'
                  '                    changed = true;\n'
                  '                }\n'
                  '            }\n'
                  '            if changed {\n'
                  '                self.sync_watching_to_master(syst);\n'
                  '            }\n'
                  '        }\n')
    if 'pager::sotto_installed_slot() < 0' not in at:
        edit('app/mod.rs', poll_anchor, poll_anchor + '\n' + revalidate, src)

    # 4. Select-toggle: SOTTO appended after CWF when installed; stale
    # selection returns to FM. Checked before PROBE so an installed module
    # wins; otherwise falls through to PROBE/legacy untouched.
    side_ops = src / 'app/side_ops.rs'
    st = side_ops.read_text()
    sotto_block = ('    ' + CFG + '\n'
                   '    {\n'
                   '        let side = app.master;\n'
                   '        if app.sotto_selected[side] {\n'
                   '            app.sotto_selected[side] = false;\n'
                   '            app.sides[side].cfg.modulation = Modulation::Fm;\n'
                   '            sync_watching_to_master(app, syst);\n'
                   '            return;\n'
                   '        }\n'
                   '        if !app.side_is_channel_mode(side)\n'
                   '            && app.sides[side].cfg.modulation == Modulation::Cwf\n'
                   '            && app.sotto_installed_slot() >= 0\n'
                   '        {\n'
                   '            app.sotto_selected[side] = true;\n'
                   '            app.sides[side].cfg.modulation = Modulation::Fm;\n'
                   '            sync_watching_to_master(app, syst);\n'
                   '            return;\n'
                   '        }\n'
                   '    }\n')
    if 'sotto_selected[side] = true' not in st:
        resident_toggle = '        if app.resident_selected[side] {'
        if st.count(resident_toggle) == 1:
            # Selector applied: claim SOTTO first, PROBE/legacy fall through.
            side_ops.write_text(st.replace(resident_toggle, sotto_block + resident_toggle, 1))
        else:
            legacy_anchor = ('    let s = &mut app.sides[app.master];\n'
                             '    s.cfg.modulation = match s.cfg.modulation {')
            if st.count(legacy_anchor) != 1:
                raise SystemExit('side_ops.rs: expected selector toggle or legacy match anchor')
            side_ops.write_text(st.replace(legacy_anchor, sotto_block + legacy_anchor, 1))

    # 5. Interlock: no key from selection. set_ptt covers PTT and VOX
    # centrally (VOX asserts through the PTT path); CW is separate.
    edit('app/tx.rs', 'pub(super) fn set_ptt(app: &mut App, syst: &mut SYST, pressed: bool) {',
         'pub(super) fn set_ptt(app: &mut App, syst: &mut SYST, pressed: bool) {\n'
         '    ' + CFG + '\n'
         '    if pressed && app.sotto_selected(app.master_index()) { return; }', src)
    edit('app/tx.rs', 'fn set_cw_key(app: &mut App, syst: &mut SYST, down: bool) {',
         'fn set_cw_key(app: &mut App, syst: &mut SYST, down: bool) {\n'
         '    ' + CFG + '\n'
         '    if down && app.sotto_selected(app.master_index()) { return; }', src)

    # 6. Standby label: SOTTO wins over PROBE; legacy labels preserved.
    standby = src / 'ui/standby.rs'
    bt = standby.read_text()
    if 'app.sotto_selected(i)' not in bt:
        anchor = '    let is_cw_master = i == app.master_index()'
        if bt.count(anchor) != 1:
            raise SystemExit('ui/standby.rs: expected one standby anchor')
        label = ('    ' + CFG + '\n'
                 '    let modulation = if app.sotto_selected(i) { "SOTTO" } else { modulation };\n')
        standby.write_text(bt.replace(anchor, label + anchor, 1))

    # 7. Resident runtime must not run under a SOTTO selection.
    runtime = src / 'vfo_mode_runtime.rs'
    rt = runtime.read_text()
    if 'app.sotto_selected(side)' not in rt:
        anchor = '        let side = app.watching_index();'
        if rt.count(anchor) != 1:
            raise SystemExit('vfo_mode_runtime.rs: expected one watching anchor')
        guard = ('\n        ' + CFG + '\n'
                 '        if app.sotto_selected(side) { self.stop(); return; }')
        runtime.write_text(rt.replace(anchor, anchor + guard, 1))

    # 8. build.rs check-cfg for the new flags.
    build = src.parent / 'build.rs'
    bs = build.read_text()
    for flag in ('k6_sotto_vfo', 'k6_sotto_rftest'):
        check = f'    println!("cargo:rustc-check-cfg=cfg({flag})");'
        if check not in bs:
            if bs.count('fn main() {') != 1:
                raise SystemExit('build.rs: expected one main anchor')
            bs = bs.replace('fn main() {', 'fn main() {\n' + check, 1)
    build.write_text(bs)

    print('Applied install-aware SOTTO VFO mode overlay (selection never keys; PTT/CW/VOX guarded)')


if __name__ == '__main__':
    main()
