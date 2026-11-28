#!/usr/bin/env python3
"""Opt-in GENERATED Aura source patch: select TX band before PA gain.

Read-only unless --apply. The default firmware build NEVER calls this tool.
No direct RF register access, frequency, band lock, watchdog, or modem API
change. This does NOT eliminate API3 CW's startup PA-on window; it only
fixes a source-order hazard where self.band_uhf could be stale at pa_enable.
"""
from __future__ import annotations
import argparse
import difflib
from pathlib import Path

TARGETS=('enter_tx','enter_tx_keyed')
PA_LINE='self.fd6818.pa_enable(syst, self.cfg.power);'
BAND='match Self::band_of(self.cfg.tx_freq_hz) {'
TX='self.fd6818.tx_on(syst);'
LED='self.fd6818.set_tx_led(syst, true);'


def _function_span(src,name):
    import re
    matches=list(re.finditer(r'\bpub\s+fn\s+'+re.escape(name)+r'\s*\(',src))
    if len(matches)!=1:raise ValueError(f'expected one Radio::{name}, found {len(matches)}')
    beg=matches[0].start();start=src.find('{',beg)
    if start<0:raise ValueError(f'function {name} has no body')
    depth=0
    for i in range(start,len(src)):
        if src[i]=='{':depth+=1
        elif src[i]=='}':
            depth-=1
            if depth==0:return beg,i+1
    raise ValueError(f'unclosed Rust Radio::{name}')


def _closing_brace(text,start):
    depth=0
    for i in range(start,len(text)):
        if text[i]=='{':depth+=1
        elif text[i]=='}':
            depth-=1
            if depth==0:return i
    raise ValueError('unclosed TX band match')


def relocate_PA_after_band_route(text):
    """Strict reversible-source edit, fail closed on source drift/ambiguity."""
    count=0
    for name in TARGETS:
        beg,end=_function_span(text,name)
        section=text[beg:end]
        marks=[(tag,section.find(tag)) for tag in (TX,PA_LINE,BAND,LED)]
        if any(section.count(tag)!=1 for tag,_ in marks):
            raise ValueError(f'generated Radio::{name} changed required TX/PA/band/LED calls')
        pos={tag:i for tag,i in marks}
        if not(pos[TX]<pos[BAND]<pos[LED]):
            raise ValueError(f'generated Radio::{name} TX/band/LED order unexpected')
        brace_begin=section.find('{',pos[BAND]);brace_end=_closing_brace(section,brace_begin)
        branch=section[brace_begin:brace_end+1]
        if branch.count('Band::Uhf => self.fd6818.set_tx_band_uhf(syst)')!=1 or branch.count('Band::Vhf => self.fd6818.set_tx_band_vhf(syst)')!=1:
            raise ValueError(f'Radio::{name} UHF/VHF route changed; refusing patch')
        if pos[BAND]<pos[PA_LINE]<brace_end:
            raise ValueError('PA gain called inside band match; cannot patch')
        if brace_end<pos[PA_LINE]<pos[LED]:
            continue  # already patched; idempotent
        if not(pos[TX]<pos[PA_LINE]<pos[BAND]):
            raise ValueError(f'generated Radio::{name} has unrecognized PA call order')
        # Preserve everything outside this method exactly; remove just the PA
        # line, not any RF silicon bit fields. Keep full patch opt-in.
        lines=section.splitlines(keepends=True)
        pa_index=next((i for i,line in enumerate(lines) if PA_LINE in line),None)
        if pa_index is None:raise ValueError('PA gain line missing')
        pa_text=lines.pop(pa_index)
        if pa_text.strip()!=PA_LINE:
            raise ValueError('PA gain statement shares line with another operation')
        section=''.join(lines)
        band_start=section.index(BAND)
        brace_end=_closing_brace(section,section.index('{',band_start))
        # Insert strictly between closing band match and following action;
        # trailing whitespace may be retained but executable order is precise.
        line_end=section.find('\n',brace_end)
        if line_end<0:raise ValueError('band match ends without newline')
        section=section[:line_end+1]+pa_text+section[line_end+1:]
        # Postcheck both semantic ordering and no new PA calls.
        if not(section.index(TX)<section.index(BAND)<section.index(PA_LINE)<section.index(LED)):
            raise ValueError('PA route rewrite failed postcheck')
        text=text[:beg]+section+text[end:]
        count+=1
    return text,count


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--radio',type=Path,required=True,
                   help='EXPLICIT generated Aura device/radio.rs (no other files changed)')
    g=p.add_mutually_exclusive_group()
    g.add_argument('--apply',action='store_true',help='write modified generated source (explicit opt-in)')
    g.add_argument('--check',action='store_true',help='check transform can apply without writing')
    args=p.parse_args(argv)
    try:
        prior=args.radio.read_text()
        revised,count=relocate_PA_after_band_route(prior)
        if args.apply and count:args.radio.write_text(revised)
    except (ValueError,OSError) as exc:p.error(str(exc))
    print(f'CW PA band-order: {count} methods needing reorder; '
          f'mode={"APPLIED" if args.apply else "CHECKED_NO_WRITES"}')
    if not args.apply and not args.check and count:
        print(''.join(difflib.unified_diff(prior.splitlines(True),revised.splitlines(True),
                 fromfile=str(args.radio)+':current',tofile=str(args.radio)+':proposed')))
    return 0

if __name__=='__main__':raise SystemExit(main())
