#!/usr/bin/env python3
"""Load the receive-only acquisition engine into the non-DMA tail of the arena."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/cps/mod.rs'
s=p.read_text()
marker='\n#[cfg(k6_iq_page)]\n#[inline(never)]\nfn iq_page_capture('
if marker in s:s=s[:s.index(marker)]
s+='''
#[cfg(k6_iq_page)]
#[inline(never)]
fn iq_page_capture(app:&mut App,ms:u32,mode:u32,ratio:u32,mix:u32,stats:&mut [u32;24])->i32 {
    const SCRATCH:usize=11000;
    let arena=crate::hal::arena::base();
    let cap=crate::hal::arena::ARENA_BYTES-SCRATCH;
    let nor=&mut app.storage_mut().norflash;
    unsafe extern "C" {fn ks_entry(h:*const u8,e:*const u8,build:*const u8,cap:u32)->i32;fn ks_validate(slot:u32,dst:*mut u8,total:u32,id:u32,functions:u32,build:*const u8)->i32;static k6_build_id:[u8;8];fn km_reset();}
    unsafe {
        km_reset();
        let dst=arena.add(SCRATCH);let build=core::ptr::addr_of!(k6_build_id).cast();
        let mut h=[0u8;12];let mut e=[0u8;8];
        if !nor.read_bytes_checked(0x19c000,&mut h)||!nor.read_bytes_checked(0x19c010+19*8,&mut e){return -10;}
        let n=ks_entry(h.as_ptr(),e.as_ptr(),build,cap as u32);
        if n<0||!nor.read_bytes_checked(u32::from_le_bytes(e[..4].try_into().unwrap()),core::slice::from_raw_parts_mut(dst,n as usize))||ks_validate(0,dst,n as u32,19,1,build)<0{return -10;}
        let f:unsafe extern "C" fn(*mut u8,u32,u32,u32,u32,u32,*mut u32)->i32=core::mem::transmute(core::ptr::read_unaligned(dst.add(24).cast::<u32>()));
        let result=f(arena,SCRATCH as u32,ms,mode,ratio,mix,stats.as_mut_ptr());
        core::ptr::write_bytes(dst,0,n as usize);
        result
    }
}
'''
p.write_text(s)
