#!/usr/bin/env python3
"""Update already-patched generated checkouts to the ten-card partition."""
from pathlib import Path
p = Path(__file__).parent / 'build/aura-fd6818b-port/src/cps/mod.rs'
s = p.read_text()
old = 'const CARD_END: u32 = 0x200000;'
new = 'const CARD_END: u32 = 0x19A000;'
if s.count(new) == 1:
    pass
elif s.count(old) == 1:
    p.write_text(s.replace(old, new))
else:
    raise SystemExit('unknown card partition in generated CPS source')
