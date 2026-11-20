#!/usr/bin/env python3
"""Opt-in cable-only execution proof. No runtime UI, RF, or SPI write capability."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port'
p=root/'src/cps/mod.rs';s=p.read_text()
anchor='            CMD_CABLE_SERIAL if f.addr == 0 && f.len == 0 => {'
new='''            #[cfg(k6_system_demo)]
            0x7b if f.addr == 0 && f.len == 4 => {
                let value=u32::from_le_bytes([f.data[0],f.data[1],f.data[2],f.data[3]]);
                let result=unsafe { system_demo(app,value) };
                cable_send(0x7b,0,&result.to_le_bytes());
            }
'''
if new not in s:
 assert s.count(anchor)==1;s=s.replace(anchor,new+anchor)
helper='''
// Cable session runs on the reserved main stack, with RF off. No page survives a call.
#[cfg(k6_system_demo)]
unsafe fn system_demo(app:&mut App,value:u32)->i32 {
    #[repr(C)]
    struct Flash { read:unsafe extern "C" fn(u32,*mut u8,u32)->i32, prog:unsafe extern "C" fn(u32,*const u8,u32)->i32, erase:unsafe extern "C" fn(u32)->i32 }
    static mut NOR:*mut crate::drivers::norflash::NorFlash<'static>=core::ptr::null_mut();
    unsafe extern "C" fn read(addr:u32,p:*mut u8,n:u32)->i32 {
        if NOR.is_null() || p.is_null() || addr<0x19c000 || addr>=0x200000 || n>0x200000-addr {return -1;}
        if (*NOR).read_bytes_checked(addr,core::slice::from_raw_parts_mut(p,n as usize)){0}else{-1}
    }
    unsafe extern "C" fn prog(_:u32,_:*const u8,_:u32)->i32{-1}
    unsafe extern "C" fn erase(_:u32)->i32{-1}
    static FL:Flash=Flash{read,prog,erase};
    unsafe extern "C" {fn ks_load(slot:u32,fl:*const Flash,dst:*mut u8,cap:u32,id:u32,functions:u32,build:*const u8)->i32; static k6_build_id:[u8;8]; fn km_cable_begin();}
    // Reset any staged cable module upload before borrowing its buffer.
    km_cable_begin();
    NOR=&mut app.storage_mut().norflash as *mut _;
    let dst=crate::hal::arena::base();
    let r=ks_load(0,&FL,dst,crate::hal::arena::ARENA_BYTES as u32,17,1,core::ptr::addr_of!(k6_build_id).cast());
    NOR=core::ptr::null_mut();
    let result=if r<0 {r} else {let f:unsafe extern "C" fn(u32)->i32=core::mem::transmute(core::ptr::read_unaligned(dst.add(24).cast::<u32>())); f(value)};
    core::ptr::write_bytes(dst,0,crate::hal::arena::ARENA_BYTES);
    km_cable_begin();
    result
}
'''
if 'unsafe fn system_demo(' not in s:s+=helper
p.write_text(s)
