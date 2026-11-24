#!/usr/bin/env python3
"""Install-aware SOTTO VFO mode overlay (offline patch script, no RF).

Patches build/aura-fd6818b-port/src the same way apply-vfo-selector.py does,
but SOTTO is selectable iff a valid installed Sotto modem module is present
(exact name "SOTTO TEST" / "SOTTO RFTEST", kl_info rc==0, flags==1). Selection
never launches or keys; PTT/CW guards stay until the runtime is ready. Removal
returns the side to FM. Legacy cycle and PROBE behavior are preserved.
"""
from pathlib import Path

SOTTO_NAMES = ('"SOTTO TEST"', '"SOTTO RFTEST"')
cfg = '#[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]'


def edit(src, rel, old, new):
    p = src / rel
    s = p.read_text()
    if new in s:
        return
    if s.count(old) != 1:
        raise SystemExit(f'{rel}: expected one anchor: {old[:80]!r}')
    p.write_text(s.replace(old, new, 1))


def main():
    src = Path(__file__).parent / 'build/aura-fd6818b-port/src'
    if not src.is_dir():
        print('no port checkout; overlay not applied (offline)')
        return
    helper = cfg + '''
#[cfg_attr(not(k6_vfo_selector), allow(dead_code))]
fn sotto_names_equal(a: &[i8; 12], b: &[u8]) -> bool {
    let len = a.iter().position(|&c| c == 0).unwrap_or(a.len());
    if len != b.len() { return false; }
    a[..len].iter().zip(b.iter()).all(|(&x, &y)| x == y as i8)
}
''' + cfg + '''
pub(super) fn sotto_installed_slot() -> i32 {
    for n in 0..32 {
        let mut info = ffi::KlInfo::default();
        if unsafe { ffi::kl_info(n, &LIB_FL, &mut info) } == 0 && info.flags == 1 {
            let mut ok = false;
            for name in [b"SOTTO TEST".as_slice(), b"SOTTO RFTEST".as_slice()] {
                if sotto_names_equal(&info.name, name) { ok = true; break; }
            }
            if ok { return n as i32; }
        }
    }
    -1
}
'''
    pager = src / 'app/pager.rs'
    if 'fn sotto_installed_slot()' not in pager.read_text():
        pager.write_text(pager.read_text() + '\n' + helper + '\n')
    app = src / 'app/mod.rs'
    slot_fn = cfg + '''
    pub fn sotto_installed_slot(&self) -> i32 { pager::sotto_installed_slot() }
'''
    at = app.read_text()
    if 'fn sotto_installed_slot(' not in at:
        anchor = '    pub fn poll_pager(&mut self, syst: &mut SYST) {'
        assert at.count(anchor) == 1
        app.write_text(at.replace(anchor, slot_fn + anchor, 1))
    print('Applied install-aware SOTTO VFO mode overlay (selection never keys; PTT guarded)')


if __name__ == '__main__':
    main()
