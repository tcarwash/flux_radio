#!/usr/bin/env python3
"""Exclusive packed system-bank CPS transaction; activate directory last."""
from pathlib import Path
import re
p=Path(__file__).parent/'build/aura-fd6818b-port/src/cps/mod.rs';s=p.read_text()
anchor='    let mut card_commit_reply = [1u8, 0xFF, 0xFF, 0, 0];\n'
new='''    #[cfg(k6_system_install)]
    let mut sys = SystemInstall { active:false, erased:0, id:0, committed:false, len:0, next:0, pos:0x19d000, finished:false };
'''
s=re.sub(r'    #\[cfg\(k6_system_install\)\]\n    let mut sys = SystemInstall \{[^\n]*\};\n','',s)
assert s.count(anchor)==1;s=s.replace(anchor,anchor+new)
anchor='        match f.cmd {\n            // Read-only capability query:'
new='''        #[cfg(k6_system_install)]
        if sys.active || f.cmd == 0x80 {
            if f.cmd != frame::CMD_END {
                let r=sys.command(app,f.cmd,f.addr,&f.data[..f.len]);
                cable_send(f.cmd,f.addr,&r.to_le_bytes());
                continue;
            }
        }
'''
if new not in s:
 assert s.count(anchor)==1;s=s.replace(anchor,new+anchor)
helper='''
#[cfg(k6_system_install)]
struct SystemInstall {active:bool, erased:u8, id:u8, committed:bool, len:u16, next:u16, pos:u32, finished:bool}
#[cfg(k6_system_install)]
impl SystemInstall {
    #[inline(never)]
    fn command(&mut self,app:&mut App,cmd:u8,addr:u16,data:&[u8])->i32 {
        unsafe extern "C" {static k6_build_id:[u8;8];fn ks_validate(slot:u32,dst:*mut u8,total:u32,id:u32,functions:u32,build:*const u8)->i32;fn km_cable_begin();}
        let build=unsafe{&*core::ptr::addr_of!(k6_build_id)};
        let base=crate::hal::arena::base();let nor=&mut app.storage_mut().norflash;
        match cmd {
            0x80 if addr==0 && data==build => {*self=Self{active:true,erased:0,id:0,committed:false,len:0,next:0,pos:0x19d000,finished:false};unsafe{km_cable_begin()};0}
            0x81 if data.is_empty() && addr<100 && self.len==0 && !self.finished && addr<=u16::from(self.erased) => {
                if addr<u16::from(self.erased){return 0;}
                let pos=0x19c000+u32::from(addr)*4096;let mut buf=[0u8;64];nor.erase_sector(pos);
                for off in (0..4096).step_by(64){if !nor.read_bytes_checked(pos+off,&mut buf)||buf.iter().any(|&b|b!=255){return -5;}}
                self.erased+=1;0
            }
            // Stage strictly increasing page IDs. Retry of current stage is harmless.
            0x82 if self.erased==100 && (1..=255).contains(&addr) && data.len()==2 && !self.finished => {
                let n=u16::from_le_bytes([data[0],data[1]]);
                if addr==u16::from(self.id){return if n==self.len{0}else{-2};}
                if addr<u16::from(self.id)||(!self.committed&&self.len!=0)||n<46||n>8192{return -2;}
                let pos=self.pos+if self.committed{(u32::from(self.len)+3)&!3}else{0};
                if pos>=0x200000||u32::from(n)>0x200000-pos{return -2;}
                self.pos=pos;
                self.id=addr as u8;self.len=n;self.next=0;self.committed=false;0
            }
            0x83 if self.len!=0 && !self.committed && !data.is_empty() && data.len()<=64 => {
                let n=data.len() as u16;
                if addr==self.next && n<=self.len-self.next {unsafe{core::ptr::copy_nonoverlapping(data.as_ptr(),base.add(addr as usize),n as usize)};self.next+=n;}
                else if addr.checked_add(n)==Some(self.next) && unsafe{core::slice::from_raw_parts(base.add(addr as usize),n as usize)}==data {}else{return -2;}
                i32::from(self.next)
            }
            0x84 if addr==u16::from(self.id) && data.is_empty() && self.len!=0 && self.next==self.len => {
                let n=self.len as usize;if self.committed{return n as i32;}
                #[cfg(k6_iq_page)]
                let base=if self.id==19 {
                    if n>crate::hal::arena::ARENA_BYTES-11000{return -2;}
                    unsafe{core::ptr::copy(base,base.add(11000),n);base.add(11000)}
                }else{base};
                let bytes=unsafe{core::slice::from_raw_parts(base,n)};
                let functions=bytes[19] as u32;
                let r=unsafe{ks_validate(0,base,n as u32,u32::from(self.id),functions,build.as_ptr())};if r<0{return r;}
                if !system_write_verified(nor,self.pos,bytes){return -5;}
                let mut e=[0u8;8];e[..4].copy_from_slice(&self.pos.to_le_bytes());e[4..].copy_from_slice(&(n as u32).to_le_bytes());
                if !system_write_verified(nor,0x19c010+8*u32::from(self.id),&e){return -5;}
                self.committed=true;n as i32
            }
            // Directory magic is written only after all staged pages are verified.
            0x85 if addr==0 && data.is_empty() && self.erased==100 && (self.len==0||self.committed) => {
                if self.finished{return 0;}
                if !system_write_verified(nor,0x19c004,build)||!system_write_verified(nor,0x19c000,b"KSD1"){return -5;}
                self.finished=true;0
            }
            _=>-2
        }
    }
}
#[cfg(any(k6_system_install,k6_user_store,k6_memory_bank))]
#[inline(never)]
fn system_write_verified(nor:&mut crate::drivers::norflash::NorFlash<'static>,pos:u32,data:&[u8])->bool{
    nor.write_bytes(pos,data);let mut buf=[0u8;64];
    for (i,part) in data.chunks(64).enumerate(){if !nor.read_bytes_checked(pos+(i*64) as u32,&mut buf[..part.len()])||&buf[..part.len()]!=part{return false;}}
    true
}
'''
if 'struct SystemInstall {' in s:s=s[:s.index('#[cfg(k6_system_install)]\nstruct SystemInstall {')].rstrip()+'\n'
s+=helper;p.write_text(s)
