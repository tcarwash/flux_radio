#!/usr/bin/env python3
"""Exclusive, build-bound programming mode. No normal module/card commands while active."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/cps/mod.rs';s=p.read_text()
anchor='    let mut card_commit_reply = [1u8, 0xFF, 0xFF, 0, 0];\n'
new='''    #[cfg(k6_system_install)]
    let mut sys = SystemInstall { active:false, erased:0, committed:false, len:0, next:0 };
'''
if new not in s:
 import re
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
struct SystemInstall {active:bool, erased:u16, committed:bool, len:u16, next:u16}
#[cfg(k6_system_install)]
impl SystemInstall {
    #[inline(never)]
    fn command(&mut self,app:&mut App,cmd:u8,addr:u16,data:&[u8])->i32 {
        unsafe extern "C" {static k6_build_id:[u8;8]; fn ks_validate(slot:u32,dst:*mut u8,total:u32,id:u32,functions:u32,build:*const u8)->i32; fn km_cable_begin();}
        let build=unsafe{&*core::ptr::addr_of!(k6_build_id)};
        let base=crate::hal::arena::base();
        let nor=&mut app.storage_mut().norflash;
        match cmd {
            0x80 if addr==0 && data==build => {self.active=true;self.erased=0;self.committed=false;self.len=0;self.next=0;unsafe{km_cable_begin()};0}
            // Exactly 100 sectors, sequentially; duplicate erase acknowledgements are harmless.
            0x81 if data.is_empty() && addr<100 && self.len==0 && addr<=self.erased => {
                if addr<self.erased{return 0;}
                let pos=0x19c000+u32::from(addr)*4096;let mut buf=[0u8;64];
                nor.erase_sector(pos);
                for off in (0..4096).step_by(64){if !nor.read_bytes_checked(pos+off,&mut buf)||buf.iter().any(|&b|b!=255){return -5;}}
                self.erased+=1;0
            }
            // Only the known demo page is authorized until real feature IDs are registered.
            0x82 if self.erased==100 && addr==0 && data.len()==2 && !self.committed => {
                let n=u16::from_le_bytes([data[0],data[1]]);
                if n<46 || usize::from(n)>8192{return -2;}
                if self.len==n{return 0;} if self.len!=0{return -2;} self.len=n;self.next=0;0
            }
            0x83 if self.len!=0 && !data.is_empty() && data.len()<=64 => {
                let n=data.len() as u16;
                if addr==self.next && n<=self.len-self.next {unsafe{core::ptr::copy_nonoverlapping(data.as_ptr(),base.add(addr as usize),n as usize)};self.next+=n;}
                else if addr.checked_add(n)==Some(self.next) && unsafe{core::slice::from_raw_parts(base.add(addr as usize),n as usize)}==data {}else{return -2;}
                i32::from(self.next)
            }
            0x84 if addr==0 && data.is_empty() && self.len!=0 && self.next==self.len => {
                let n=self.len as usize;
                if self.committed{return n as i32;}
                let r=unsafe{ks_validate(addr as u32,base,n as u32,if cfg!(k6_channel_page){18}else{17},if cfg!(k6_channel_page){2}else{1},build.as_ptr())};if r<0{return r;}
                let bytes=unsafe{core::slice::from_raw_parts(base,n)};
                let pos=0x19c000;
                // Publish header last. A power cut during programming leaves an invalid page.
                nor.write_bytes(pos+16,&bytes[16..]);nor.write_bytes(pos,&bytes[..16]);
                let mut buf=[0u8;64];
                for (i,part) in bytes.chunks(64).enumerate(){if !nor.read_bytes_checked(pos+(i*64) as u32,&mut buf[..part.len()])||&buf[..part.len()]!=part{return -5;}}
                self.committed=true; n as i32
            }
            // No other command may mutate arena or SPI in this session. Re-enter to restart.
            _=>-2
        }
    }
}
'''
if 'struct SystemInstall {' in s:
    s=s[:s.index('#[cfg(k6_system_install)]\nstruct SystemInstall {')].rstrip()+'\n'
s+=helper
p.write_text(s)
