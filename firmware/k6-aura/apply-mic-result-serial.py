#!/usr/bin/env python3
"""Preserve MIC A/B diagnostics until cable reconnect via a 44-byte SRAM mailbox.

Only Sotto microphone builds use this overlay. The live serial dispatcher and
its serial response are declared in the canonical live-serial.rs source.
"""
from pathlib import Path
import os

here=Path(__file__).resolve().parent
work=Path(os.environ.get('K6_RX4800_WORK',here/'build/aura-fd6818b-port')).resolve()
p=work/'src/app/pager.rs'
s=p.read_text()
needle='''    tlm_add(t.as_bytes());\n}'''
replacement='''    #[cfg(all(k6_sotto_mic,k6_live_serial))]
    crate::cps::live::cache_mic_result(t.as_bytes());
    tlm_add(t.as_bytes());
}'''
if replacement in s: raise SystemExit('MIC result serial overlay already applied')
if s.count(needle)!=1:raise SystemExit(f'MIC result overlay anchor changed {s.count(needle)}')
p.write_text(s.replace(needle,replacement,1))
