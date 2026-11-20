#!/usr/bin/env python3
"""Small shared unsigned decimal writer for frequently rendered numbers."""
from pathlib import Path
src=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=src/'ui/mod.rs';s=p.read_text()
if 'fn decimal_field(' not in s:
 s+='''
/// Unsigned decimal, minimum width, ASCII space or zero padding.
#[inline(never)]
pub(crate) fn decimal_field(w: &mut dyn core::fmt::Write, mut n: u32, width: usize, pad: u8) {
    let mut b=[0u8;10]; let mut i=b.len();
    loop { i-=1; b[i]=b'0'+(n%10) as u8; n/=10; if n==0 {break;} }
    while i>0 && b.len()-i<width {i-=1;b[i]=pad;}
    // All bytes initialized above are ASCII.
    let _=w.write_str(unsafe{core::str::from_utf8_unchecked(&b[i..])});
}
#[inline(never)]
pub(crate) fn frequency_field(w: &mut dyn core::fmt::Write, mhz:u32, frac:u32, digits:usize) {
    decimal_field(w,mhz,3,b' '); let _=w.write_str("."); decimal_field(w,frac,digits,b'0');
}
''';p.write_text(s)
p=src/'ui/standby.rs';s=p.read_text();replacements={
'write!(freq_line, "{:3}.{:05}", mhz, frac).ok();':'super::frequency_field(&mut freq_line,mhz,frac,5);',
'write!(big, "{:3}.{:03}", mhz, khz).ok();':'super::frequency_field(&mut big,mhz,khz,3);',
'write!(small, "{:02}", tail).ok();':'super::decimal_field(&mut small,tail,2,b\'0\');',
'write!(line,"{:3}.{:05}",hz/1_000_000,(hz%1_000_000)/10).ok();':'super::frequency_field(line,hz/1_000_000,(hz%1_000_000)/10,5);',
'write!(buf, "{}", app.freq_input_digit(pos)).ok();':'super::decimal_field(&mut buf,app.freq_input_digit(pos) as u32,1,b\'0\');',
}
for a,b in replacements.items():
 if a in s:s=s.replace(a,b)
 elif b not in s:raise SystemExit('format anchor missing: '+a)
p.write_text(s)
p=src/'app/settings_ops.rs';s=p.read_text();import re
# Literal text and direct string writes need no formatting machinery.
s=re.sub(r'write!\(w, "([^"{}\\]*)"\)',r'w.write_str("\1")',s)
s=re.sub(r'write!\(w,\s*"\{\}",\s*(label|s|crate::flux_info::PACKS)\)',r'w.write_str(\1)',s)
p.write_text(s)
