#!/usr/bin/env python3
"""Opt-in real watchdog for the lean Flux host, not the legacy lean pager."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'hal/txdog.rs';s=p.read_text()
s=s.replace('#[cfg(not(feature = "lean"))]', '#[cfg(any(not(feature = "lean"), k6_lean_txdog))]')
s=s.replace('#[cfg(feature = "lean")]', '#[cfg(all(feature = "lean", not(k6_lean_txdog)))]')
# Do not permit a second key-up to replace an already armed deadline.
anchor='    arm(ARM_TX, true);\n    LIMIT.store'
guard='    if KEYED_AT.load(Ordering::Relaxed) != 0 { return; }\n'
# Normalize older generated trees where the guard accumulated on each build.
while guard+guard in s:
    s=s.replace(guard+guard,guard)
if guard+anchor not in s:
    if s.count(anchor)!=1: raise SystemExit("watchdog key-up anchor changed")
    s=s.replace(anchor,guard+anchor,1)
# Explicit readiness for fail-closed TX in the opt-in build.
if 'pub fn ready()' not in s:s+='\n#[cfg(k6_lean_txdog)]\npub fn ready() -> bool { LIVE.load(Ordering::Relaxed) }\n'
p.write_text(s)
p=root/'main.rs';s=p.read_text();import re
s=re.sub(r'#\[cfg\(not\(feature = "lean"\)\)\](\s+hal::txdog::(?:install|poll)\(\);)',r'#[cfg(any(not(feature = "lean"), k6_lean_txdog))]\1',s);p.write_text(s)
p=root/'device/radio.rs';s=p.read_text()
for name in ['enter_tx','enter_tx_keyed']:
 a=f'pub fn {name}(&mut self, syst: &mut SYST) -> bool {{'
 b=a+'\n        #[cfg(k6_lean_txdog)]\n        if !crate::hal::txdog::ready() { return false; }'
 if b not in s:s=s.replace(a,b,1)
# SSTV is not included in lean host, keep its own contract intact.
p.write_text(s)
