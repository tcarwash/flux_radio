#!/usr/bin/env python3
"""Level 27: balanced-host read-only resources and full arena execution."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/pager.rs'
s=p.read_text().replace('matches!(*arena::base().add(12),27|28|29)','*arena::base().add(12) == 27').replace('matches!(*arena::base().add(12),27|28)','*arena::base().add(12) == 27')
def add(old,new):
 global s
 if new in s:return
 if s.count(old)!=1:raise ValueError(old)
 s=s.replace(old,new,1)
gate='all(k6_live_serial,k6_ram0_balanced,k6_user_store,feature="spilib",feature="lean",feature="blowfang-host")'
add('        pub cancel_poll: unsafe extern "C" fn() -> i32,','        pub cancel_poll: unsafe extern "C" fn() -> i32,\n        pub micin_tx: unsafe extern "C" fn(i32) -> i32,\n        pub resource_open: unsafe extern "C" fn(u32) -> i32,\n        pub resource_read: unsafe extern "C" fn(u32, u32, *mut u8, u32) -> i32,')
add('    cancel_poll: mod_cancel_poll,','    cancel_poll: mod_cancel_poll,\n    micin_tx: mod_micin_unavailable,\n    resource_open: mod_resource_open,\n    resource_read: mod_resource_read,')
# Keep prior expression intact for idempotence of the earlier patch.
add('level: if cfg!(all(k6_live_serial, feature = "spilib")) { 25 }',f'level: if cfg!({gate}) {{ 27 }} else if cfg!(all(k6_live_serial, feature = "spilib")) {{ 25 }}')
anchor='unsafe extern "C" fn mod_scratch(bytes: *mut u32) -> *mut u8 {'
add(anchor,'''// API 27 is an explicit opt-in to main-stack execution; old modules
// keep their isolated stack and callback arena reservations.
unsafe extern "C" fn mod_micin_unavailable(_:i32)->i32 {-1}
fn full_module_stack() -> bool {
    cfg!('''+gate+''') && unsafe {
        !(*addr_of!(MOD_CTX)).app.is_null() && *arena::base().add(12) == 27
    }
}
#[cfg('''+gate+''')]
static mut RESOURCE_DECK: (u32,u32) = (0,0);
#[cfg('''+gate+''')]
unsafe extern "C" fn mod_resource_open(slot:u32)->i32 {
    RESOURCE_DECK=(0,0);
    if !full_module_stack() || (*addr_of!(MOD_CTX)).tx || slot>=32 {return -1;}
    let (app,_)=mod_ctx();
    let Some((base,extent))=library_location(&mut app.storage.norflash,slot) else{return -1;};
    let mut h=[0u8;20];
    if extent<24 || !app.storage.norflash.read_bytes_checked(base,&mut h) {return -1;}
    let n=u16::from_le_bytes([h[4],h[5]]) as u32;
    if &h[..4]!=b"K6M\\x01" || h[13]!=5 || h[15]!=slot as u8 ||
        &h[16..20]!=b"K6SC" || n<4 || n+24>extent {return -1;}
    RESOURCE_DECK=(base+16,n);
    n as i32
}
#[cfg(not('''+gate+'''))]
unsafe extern "C" fn mod_resource_open(_:u32)->i32 {-1}
#[cfg('''+gate+''')]
unsafe extern "C" fn mod_resource_read(kind:u32,off:u32,buf:*mut u8,n:u32)->i32 {
    mod_tx_guard();
    if !full_module_stack() || (*addr_of!(MOD_CTX)).tx || buf.is_null() {return -1;}
    let (base,len)=match kind {0=>(0x100000,30752),1=>RESOURCE_DECK,_=>return -1};
    if base==0 || off>len || n>len-off {return -1;}
    let (app,_)=mod_ctx();
    if app.storage.norflash.read_bytes_checked(base+off,core::slice::from_raw_parts_mut(buf,n as usize)){0}else{-1}
}
#[cfg(not('''+gate+'''))]
unsafe extern "C" fn mod_resource_read(_:u32,_:u32,_:*mut u8,_:u32)->i32 {-1}

'''+anchor)
add('fn scratch_end() -> usize {\n    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {','fn scratch_end() -> usize {\n    if full_module_stack() {return arena::end().saturating_sub(16);}\n    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {')
# Keep LCD buffer intact in main stack mode: never use the arena as callback stack.
add('        unsafe { ffi::km_call_host_stack(mod_lcd_call_host, core::ptr::null_mut(), module_lcd_stack_top()) };','        if full_module_stack() { unsafe {mod_lcd_call_host(core::ptr::null_mut())}; } else {\n            unsafe { ffi::km_call_host_stack(mod_lcd_call_host, core::ptr::null_mut(), module_lcd_stack_top()) };\n        }')
add('    let ret = unsafe { ffi::km_run_on_stack(arena::base(), &MOD_API, module_stack_top()) };','''    let ret = if full_module_stack() {
        #[cfg('''+gate+''')]
        unsafe {RESOURCE_DECK=(0,0);}
        if room<2304 {unsafe {ffi::km_reset()}; -8}
        else {unsafe {ffi::km_run(arena::base(), &MOD_API)}}
    } else {unsafe { ffi::km_run_on_stack(arena::base(), &MOD_API, module_stack_top()) }};''')
# Only restore the page buffer if it was actually used as a stack.
add('    unsafe { restore_module_page_buffer(); }\n    #[cfg(not(k6_no_scan))]','    if !full_module_stack() {unsafe { restore_module_page_buffer(); }}\n    #[cfg(not(k6_no_scan))]')
p.write_text(s)
