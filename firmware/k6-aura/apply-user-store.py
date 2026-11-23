#!/usr/bin/env python3
"""Redirect all native library address users; opt-in, never autoformat."""
from pathlib import Path
import os
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'app/pager.rs';s=p.read_text()
marker='// KUL2 logical lookup\n'
if marker not in s:
 s+='''
// KUL2 logical lookup
#[cfg(feature="spilib")]
pub(crate) fn library_location(nor:&mut crate::drivers::norflash::NorFlash<'static>,slot:u32)->Option<(u32,u32)>{
    unsafe extern "C" {fn kl_locate(slot:i32,fl:*const ffi::KlFlash,addr:*mut u32,len:*mut u32)->i32;}
    let mut a=0;let mut n=0;
    unsafe{CB_NOR=nor as *mut _;if kl_locate(slot as i32,&LIB_FL,&mut a,&mut n)==0{Some((a,n))}else{None}}
}
#[cfg(feature="spilib")]
fn library_location_current(slot:i32)->Option<u32>{
    unsafe extern "C" {fn kl_locate(slot:i32,fl:*const ffi::KlFlash,addr:*mut u32,len:*mut u32)->i32;}
    let mut a=0;let mut n=0;unsafe{if kl_locate(slot,&LIB_FL,&mut a,&mut n)==0{Some(a)}else{None}}
}
#[cfg(all(k6_user_store,k6_designer))]
fn packed_theme(app:&mut App,slot:u8,data:Option<&[u8]>)->i32{
    unsafe extern "C" {fn ku_theme_put(fl:*const ffi::KlFlash,id:u32,p:*const u8,n:u32)->i32;fn ku_theme_remove(fl:*const ffi::KlFlash,id:u32)->i32;}
    unsafe{CB_NOR=&mut app.storage.norflash;LIB_WRITE=!app.is_transmitting();let r=if let Some(b)=data{ku_theme_put(&LIB_FL,slot as u32,b.as_ptr(),b.len() as u32)}else{ku_theme_remove(&LIB_FL,slot as u32)};LIB_WRITE=false;r}
}
'''
# Guard native branches with cfg blocks, instead of unreachable code paths.
a='    let base=0x110000+u32::from(slot)*0x4000;'
# Non-Designer lab profiles deliberately exclude the legacy theme installer.
# Patch theme code only when both typed handlers actually exist; user-store
# logical module lookup and cable protocol still need to be patched below.
has_theme_helpers=('fn cable_install_theme(' in s, 'fn cable_remove_theme(' in s)
if has_theme_helpers[0] != has_theme_helpers[1]:
    raise SystemExit('incomplete theme patch: refusing to guess library layout')
if not any(has_theme_helpers):
    if os.environ.get('K6_APC_TX') != '1' or os.environ.get('K6_DESIGNER_SERVICE','0') == '1':
        raise SystemExit('missing theme handlers outside non-Designer APC lab')
else:
    start=s.index('fn cable_install_theme(');end=s.index('fn cable_remove_theme(',start) if 'fn cable_remove_theme(' in s[start:] else s.index('/// The cable session',start)
    # helper order varies; locate respective function closing using next attribute or doc
    for name in ['cable_install_theme','cable_remove_theme']:
     start=s.index('fn '+name+'(');end=s.index('\n}\n',start)
     if 'packed_theme(app,slot,' in s[start:end]:continue
     aidx=s.index(a,start,end)
     if name=='cable_install_theme':
      if 'packed_theme(app,slot,Some(data))' not in s[start:end]:s=s[:aidx]+'''    #[cfg(k6_user_store)] {return packed_theme(app,slot,Some(data));}
        #[cfg(not(k6_user_store))] {
    '''+s[aidx:end]+'\n    }'+s[end:]
     else:
      if 'packed_theme(app,slot,None)' not in s[start:end]:
       s=s[:aidx]+s[aidx:end].replace(a,'''    let Some((base,_))=library_location(&mut app.storage.norflash,slot as u32) else{return -2;};''').replace('    let nor=&mut app.storage.norflash;', '''    #[cfg(k6_user_store)] {return packed_theme(app,slot,None);}
        #[cfg(not(k6_user_store))] {
        let nor=&mut app.storage.norflash;''')+'\n    }'+s[end:]
s=s.replace('let deck = KL_LO + slot as u32 * 0x4000 + 16;','let Some(base)=library_location_current(slot) else{continue;};let deck=base+16;')
p.write_text(s)
p=root/'app/pager/explore.rs';s=p.read_text().replace('let base = 0x11_0000 + 0x4000 * SLOT[p as usize] as u32 + KM_HDR as u32;','let base = super::super::library_location_current(SLOT[p as usize]).unwrap_or(0) + KM_HDR as u32;');p.write_text(s)
p=root/'app/mod.rs';s=p.read_text();a='    pub(crate) fn designer_read(&mut self, addr: u32, dst: &mut [u8]) -> bool {'
if 'fn designer_location(' not in s:s=s.replace(a,'''    pub(crate) fn designer_location(&mut self,slot:u32)->Option<(u32,u32)>{pager::library_location(&mut self.storage.norflash,slot)}
    #[cfg(k6_designer)]
    pub(crate) fn designer_slot_read(&mut self,slot:u32,off:u16,dst:&mut [u8])->bool{let Some((a,n))=self.designer_location(slot) else{return false;};u32::from(off)+dst.len() as u32<=n && self.designer_read(a+u32::from(off),dst)}
    #[cfg(k6_designer)]
'''+a);p.write_text(s)
p=root/'app/settings_ops.rs';s=p.read_text().replace('app.designer_read(0x110000+slot*0x4000+u32::from(off),dst)','app.designer_slot_read(slot,off,dst)').replace('let base=0x110000+slot*0x4000;','').replace('app.designer_read(base+u32::from(off),dst)','app.designer_slot_read(slot,off,dst)').replace('app.designer_read(base+u32::from(theme.name_offset),','app.designer_slot_read(slot,theme.name_offset,');p.write_text(s)
p=root/'ui/designer.rs';s=p.read_text().replace('app.designer_read(0x110000 + slot * 0x4000 + u32::from(off), dst)','app.designer_slot_read(slot,off,dst)').replace('let base = 0x110000 + slot*0x4000;','let Some((base,len))=crate::app::pager::library_location(&mut storage.norflash,slot) else{return;};').replace('storage.norflash.read_bytes_checked(base+u32::from(off),dst)','u32::from(off)+dst.len() as u32<=len && storage.norflash.read_bytes_checked(base+u32::from(off),dst)');p.write_text(s)
# Expose pager within crate for splash reader.
p=root/'app/mod.rs';s=p.read_text().replace('pub pub(crate) mod pager;', 'pub mod pager;');p.write_text(s)
# INFO protocol 5 signals logical catalog. Keep 32 logical IDs for first integration.
p=root/'cps/mod.rs';s=p.read_text().replace('d[4] = if cfg!(k6_designer) {4} else {2};','d[4] = if cfg!(k6_user_store) {5} else if cfg!(k6_designer) {4} else {2};');p.write_text(s)
