#!/usr/bin/env python3
"""Opt-in size experiment; do not use as a release replacement for FM."""
from pathlib import Path
import re
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
for name in ['app/keys.rs','app/launcher.rs','app/keyfn.rs','ui/mod.rs','main.rs']:
 p=root/name;s=p.read_text()
 # Remove our prior annotations before reapplying; underlying patchers own the original cfg.
 s=re.sub(r'(?m)^\s*#\[cfg\(not\(k6_no_native_fm\)\)\]\n','',s)
 s=re.sub(r'(?m)^\s*#\[cfg\(all\(k6_no_native_fm,not\(k6_no_scan\)\)\)\]\n\s*KeyFunction::FmRadio => \{\},\n','',s)
 pat=r'(?m)^(\s*)#\[cfg\(not\(k6_no_scan\)\)\]\n(\s*)([^\n]+)'
 def gate(m):
  line=m[3]
  if any(x in line for x in ['FmRadio','Mode::Fm','poll_fm']):
   return m[1]+'#[cfg(not(k6_no_scan))]\n'+m[2]+'#[cfg(not(k6_no_native_fm))]\n'+m[2]+line
  return m[0]
 s=re.sub(pat,gate,s)
 if name=='app/keyfn.rs':
  a='        #[cfg(k6_no_scan)]\n        KeyFunction::FmRadio => {},'
  if a not in s:raise SystemExit('missing FM fallback')
  s=s.replace(a,a+'\n        #[cfg(all(k6_no_native_fm,not(k6_no_scan)))]\n        KeyFunction::FmRadio => {},')
 p.write_text(s)
