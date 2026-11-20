#!/usr/bin/env python3
"""Avoid generic formatting in settings UI; preserve exact visible text."""
from pathlib import Path
import re
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/settings_ops.rs'
s=p.read_text()
# Old reused checkouts accumulated identical Theme arms as compact-format rewrote
# the anchor used by apply-theme-selection. Keep exactly one matching arm.
pattern=r'        #\[cfg\(k6_designer\)\]\n        SettingItem::Theme => \{\n            let v=app.settings.theme_selection;\n.*?\n        \}\n'
seen=False
def dedup(m):
 global seen
 if seen:return ''
 seen=True;return m.group()
s=re.sub(pattern,dedup,s,flags=re.S)
# All these arguments are already strings. Generic formatting is unnecessary.
s=re.sub(r'write!\(\s*w,\s*"\{\}",\s*((?:if |match )[^;]*?\n\s*\})\s*\)',r'w.write_str(\1)',s)
for a,b in {
 'write!(w, "{}", super::pager::key_module_label(idx).unwrap_or("UNAVAILABLE"))':'w.write_str(super::pager::key_module_label(idx).unwrap_or("UNAVAILABLE"))',
 'write!(w, "{}", keyfn::from_u8(idx).label())':'w.write_str(keyfn::from_u8(idx).label())',
 'write!(w, "{}", BandLock::from_u8(idx).label())':'w.write_str(BandLock::from_u8(idx).label())',
 'write!(w, "{}", current_value(app, item))':'{ signed_field(w,current_value(app,item),false); Ok::<(),core::fmt::Error>(()) }',
 'write!(w, "{}", v)':'{ signed_field(w,v,false); Ok::<(),core::fmt::Error>(()) }',
 'write!(w, "{:+}Hz", hz)':'{ signed_field(w,hz,true); w.write_str("Hz") }',
 'write!(w, "{}MS", v * 100)':'{ signed_field(w,v*100,false); w.write_str("MS") }',
 'write!(w, "{}Hz", RTONE_HZ_DIV_10[idx] as u32 * 10)':'{ crate::ui::decimal_field(w,RTONE_HZ_DIV_10[idx] as u32*10,1,b\'0\'); w.write_str("Hz") }',
 'write!(w, "-{}", current_value(app, item))':'{ let _=w.write_str("-"); signed_field(w,current_value(app,item),false); Ok::<(),core::fmt::Error>(()) }',
 'write!(w, "{}.{:02}k", deci_hz / 100, deci_hz % 100)':'{ decimal_pair(w,(deci_hz/100) as u32,(deci_hz%100) as u32,2); w.write_str("k") }',
 'write!(w, "{}.{:02}V", cv / 100, cv % 100)':'{ decimal_pair(w,(cv/100) as u32,(cv%100) as u32,2); w.write_str("V") }',
 'write!(w, "{}.{}S", deci_s / 10, deci_s % 10)':'{ decimal_pair(w,(deci_s/10) as u32,(deci_s%10) as u32,1); w.write_str("S") }',
 'write!(w, "{}.{}Hz", hz / 10, hz % 10)':'{ decimal_pair(w,(hz/10) as u32,(hz%10) as u32,1); w.write_str("Hz") }',
 'write!(w, "{}.{:04}", hz / 1_000_000, (hz % 1_000_000) / 100)':'{ decimal_pair(w,hz/1_000_000,(hz%1_000_000)/100,4); Ok::<(),core::fmt::Error>(()) }',
 'write!(w, "{}.{:03}", mhz, khz)':'{ decimal_pair(w,mhz,khz,3); Ok::<(),core::fmt::Error>(()) }',
}.items():
 if a in s:s=s.replace(a,b)
 elif b not in s:raise SystemExit('missing settings format: '+a)
if 'fn signed_field(' not in s:
 s+='''
#[inline(never)]
fn signed_field(w:&mut dyn Write,n:i32,plus:bool) {
    if n<0 {let _=w.write_str("-");} else if plus {let _=w.write_str("+");}
    crate::ui::decimal_field(w,n.unsigned_abs(),1,b'0');
}
#[inline(never)]
fn decimal_pair(w:&mut dyn Write,a:u32,b:u32,digits:usize) {
    crate::ui::decimal_field(w,a,1,b'0');let _=w.write_str(".");crate::ui::decimal_field(w,b,digits,b'0');
}
'''
p.write_text(s)
