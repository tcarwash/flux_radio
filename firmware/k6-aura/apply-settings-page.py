#!/usr/bin/env python3
"""Extract scalar formatting into a build-bound system page (ID 20)."""
from pathlib import Path
import sys,re
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from k6_settings_page import NAMES
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/settings_ops.rs';s=p.read_text()
marker='\n// Flux settings page dispatch\n'
if marker in s: s=s[:s.index(marker)]
# Retain native arms under cfg for non-page builds, replace pure scalar cases.
start=s.index('pub fn value_text_for(');end=s.index('\n#[inline(never)]\nfn signed_field',start);part=s[start:end]
# Remove previous generated cfg wrappers before reapplying; source patches can run repeatedly.
part=part.replace('        #[cfg(not(k6_settings_page))]\n        SettingItem::','        SettingItem::')
# Each listed arm is entirely scalar, except edit-aware cases which keep native paths.
scalar='Tdr BusyLock TxForbid Beep ActionBeep Tail BootSnd Vox BootMode VoxDly Rptrl Roge AutoLk Rtone Wn TxPr Scrm ScanMd Rit Save Abr ChDisp BkIn AprsPath AprsDevInfo AprsBatVolt AprsSsid AprsPower Step'.split()
for m in list(re.finditer(r'^        SettingItem::(\w+)(?:\s*\n        \| SettingItem::\w+)*\s*=>\s*\{',part,re.M))[::-1]:
    if m.group(1) in scalar:part=part[:m.start()]+'        #[cfg(not(k6_settings_page))]\n'+part[m.start():]
# The fallback handles all scalar arms when page support is enabled.
needle='        _ => {\n            let _ = { signed_field(w,current_value(app,item),false); Ok::<(),core::fmt::Error>(()) };\n        }'
replacement='''        _ => {
            #[cfg(k6_settings_page)] { page_value(app,item,w); }
            #[cfg(not(k6_settings_page))] { let _ = { signed_field(w,current_value(app,item),false); Ok::<(),core::fmt::Error>(()) }; }
        }'''
if replacement not in part:
    assert needle in part;part=part.replace(needle,replacement)
s=s[:start]+part+s[end:]
# Explicit stable IDs, never expose Rust App layout to page code.
ids='\n'.join(('        #[cfg(k6_designer)]\n' if n=='Theme' else '')+f'        SettingItem::{n} => {i},' for i,n in enumerate(NAMES))
s+=marker+'''#[cfg(k6_settings_page)]
#[inline(never)]
fn page_value(app:&mut App,item:SettingItem,w:&mut dyn Write) {
    let field=match item {
'''+ids+'''
    };
    let v=current_value(app,item);
    let aux=match item {
        SettingItem::Step=>STEP_LIST_DECI_HZ[v.clamp(0,STEP_LIST_DECI_HZ.len() as i32-1) as usize] as u32,
        SettingItem::Rtone=>RTONE_HZ_DIV_10[v.clamp(0,3) as usize] as u32*10,
        SettingItem::TxPr=>u32::from(cfg!(k6_el_power)),
        _=>0,
    };
    let Some(dst)=super::chanmgr::load_system_page(app,20,1) else {let _=w.write_str("PAGE MISSING");return;};
    let mut out=[0u8;32];
    unsafe {
        let f:unsafe extern "C" fn(u32,i32,u32,*mut u8)->i32=core::mem::transmute(core::ptr::read_unaligned(dst.add(24).cast::<u32>()));
        let n=f(field,v,aux,out.as_mut_ptr());
        if (0..=32).contains(&n){if let Ok(t)=core::str::from_utf8(&out[..n as usize]){let _=w.write_str(t);}}
    }
}
'''
p.write_text(s)
# Share the channel page loader instead of duplicating the validation machinery.
p=p.with_name('chanmgr.rs');s=p.read_text();a='fn load_channel_page(app:&mut App)->Option<*mut u8>{';b='''fn load_channel_page(app:&mut App)->Option<*mut u8>{load_system_page(app,18,2)}
#[cfg(k6_channel_page)]
pub(super) fn load_system_page(app:&mut App,id:u32,functions:u32)->Option<*mut u8>{''';assert a in s;s=s.replace(a,b,1).replace('0x19c010+18*8','0x19c010+id*8').replace('n as u32,18,2,build','n as u32,id,functions,build');p.write_text(s)
