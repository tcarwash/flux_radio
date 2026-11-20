#!/usr/bin/env python3
"""Use a build-bound field-render page, preserving existing editor and renderer.
Only called inside keypad/draw stack, page occupies first 8 KiB of 14,976.
Read and validate per draw, never cache a pointer across other arena users.
"""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/chanmgr.rs';s=p.read_text()
# mutable detail_value allows the existing SPI driver to read the page. Copy all
# arguments to the stack before borrowing the App mutably.
a='fn value_text_for(edit: &ChannelEdit, index: usize, field: Field, w: &mut dyn Write) {'
if '#[cfg(not(k6_channel_page))]\n'+a not in s:
 s=s.replace(a,'#[cfg(not(k6_channel_page))]\n'+a)
a='pub(super) fn detail_value(app: &App, index: usize, w: &mut dyn Write) -> bool {'
if '#[cfg(not(k6_channel_page))]\n'+a not in s:s=s.replace(a,'#[cfg(not(k6_channel_page))]\n'+a)
# Replace the generated experimental code on every build, including reused checkouts.
marker='\n#[cfg(k6_channel_page)]\nfn load_channel_page('
old='\n#[cfg(k6_channel_page)]\npub(super) fn detail_value(app: &mut App,index:usize,w:&mut dyn Write)->bool {'
for m in (marker,old):
 if m in s:s=s[:s.index(m)]
for name in ('current_value','adjust','apply'):
 anchor='fn '+name+'('
 if '#[cfg(not(k6_channel_page))]\n'+anchor not in s:s=s.replace(anchor,'#[cfg(not(k6_channel_page))]\n'+anchor,1)
# Native fallback remains available when this experimental cfg is off.
for native,page in [
 ('adjust(&mut edit, field, up);','{page_scalar(dst,&mut edit,field,if up{1}else{2},0);}'),
 ('apply(&mut edit, field, scalar_floor(field));','{page_scalar(dst,&mut edit,field,3,scalar_floor(field));}'),
 ('edit.snapshot = current_value(&edit, field);','{edit.snapshot=page_scalar(dst,&mut edit,field,0,0);}'),
 ('apply(&mut edit, field, snapshot);','{page_scalar(dst,&mut edit,field,3,snapshot);}'),
]:
 replacement='#[cfg(not(k6_channel_page))] {'+native+'}\n                #[cfg(k6_channel_page)] '+page
 s=s.replace('#[cfg(not(k6_channel_page))] '+native,'#[cfg(not(k6_channel_page))] {'+native+'}')
 if replacement not in s:
  assert native in s,native;s=s.replace(native,replacement,1)
# Missing page: refuse entering and abandon any working edit on failure. No saves.
anchor='pub(super) fn enter(app: &mut App) {'
check='\n    #[cfg(k6_channel_page)] if load_channel_page(app).is_none(){return;}'
if anchor+check not in s:s=s.replace(anchor,anchor+check)
anchor='fn dispatch_detail(app: &mut App, ev: KeyEvent, mut edit: ChannelEdit) {'
check='\n    #[cfg(k6_channel_page)] let Some(dst)=load_channel_page(app) else {app.chanmgr.phase=Phase::List(rescan_list(app,None));return;};'
if anchor+check not in s:s=s.replace(anchor,anchor+check)
extra='''
#[cfg(k6_channel_page)]
fn load_channel_page(app:&mut App)->Option<*mut u8>{
    const CAP:usize=8192;
    let dst=crate::hal::arena::base();let nor=&mut app.storage_mut().norflash;
    unsafe extern "C" {fn ks_entry(h:*const u8,e:*const u8,build:*const u8,cap:u32)->i32;fn ks_validate(slot:u32,dst:*mut u8,total:u32,id:u32,functions:u32,build:*const u8)->i32;static k6_build_id:[u8;8];fn km_reset();}
    unsafe {
        km_reset();
        let build=core::ptr::addr_of!(k6_build_id).cast();
        let mut hdr=[0u8;12];let mut entry=[0u8;8];
        if !nor.read_bytes_checked(0x19c000,&mut hdr)||!nor.read_bytes_checked(0x19c010+18*8,&mut entry){return None;}
        let n=ks_entry(hdr.as_ptr(),entry.as_ptr(),build,CAP as u32);
        if n<0||!nor.read_bytes_checked(u32::from_le_bytes(entry[..4].try_into().unwrap()),core::slice::from_raw_parts_mut(dst,n as usize))||ks_validate(0,dst,n as u32,18,2,build)<0{return None;}
    }
    Some(dst)
}
#[cfg(k6_channel_page)]
#[inline(never)]
fn page_args(edit:&ChannelEdit,index:usize)->[u8;39]{
    let mut args=[0u8;39];args[..32].copy_from_slice(&edit.working.to_bytes());args[32]=edit.sftd;
    args[33..37].copy_from_slice(&edit.offset_hz.to_le_bytes());args[37]=u8::from(edit.is_editing(index));args[38]=u8::from(cfg!(k6_el_power));args
}
#[cfg(k6_channel_page)]
#[inline(never)]
fn page_scalar(dst:*mut u8,edit:&mut ChannelEdit,field:Field,op:u32,val:i32)->i32{
    let mut a=page_args(edit,edit.field_index);
    let r=unsafe{let f:unsafe extern "C" fn(*mut u8,u32,u32,i32)->i32=core::mem::transmute(core::ptr::read_unaligned(dst.add(28).cast::<u32>()));f(a.as_mut_ptr(),field as u32,op,val)};
    if op!=0 {edit.working=flash_map::Channel::from_bytes(a[..32].try_into().unwrap());edit.sftd=a[32];if field==Field::Sftd{sync_tx_freq(edit);}}
    r
}
#[cfg(k6_channel_page)]
pub(super) fn detail_value(app: &mut App,index:usize,w:&mut dyn Write)->bool {
    let Phase::Detail(edit)=&app.chanmgr.phase else {return false;};
    let field=field_at(index);
    if edit.is_editing(index) {
        match field {
            Field::Name=>{write_name_plain(&edit.name_edit.buf,w);return true;}
            Field::Freq=>{edit.freq_input.write_display(3,w);return true;}
            Field::Offset=>{edit.offset_input.write_display(3,w);return true;}
            _=>{}
        }
    }
    if field==Field::Save{return false;}
    let args=page_args(edit,index);
    let Some(dst)=load_channel_page(app) else {let _=w.write_str("PAGE MISSING");return true;};
    unsafe {
        let f:unsafe extern "C" fn(*const u8,u32,*mut u8)->i32=core::mem::transmute(core::ptr::read_unaligned(dst.add(24).cast::<u32>()));
        let mut out=[0u8;32];let len=f(args.as_ptr(),index as u32,out.as_mut_ptr());
        if (0..=32).contains(&len){if let Ok(text)=core::str::from_utf8(&out[..len as usize]){let _=w.write_str(text);}}
    }
    true
}
'''
if extra not in s:s+=extra
p.write_text(s)
p=p.parent/'mod.rs';s=p.read_text().replace('pub fn chanmgr_field_value(&self,','pub fn chanmgr_field_value(&mut self,');p.write_text(s)
p=p.parent.parent/'ui/chanmgr.rs';s=p.read_text().replace("struct ChanDetailSource<'a>(&'a app::App);","struct ChanDetailSource<'a>(&'a mut app::App);");p.write_text(s)
