#!/usr/bin/env python3
"""Cable-only bounded user bank restoration; directory magic is committed last."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/cps/mod.rs';s=p.read_text()
a='        match f.cmd {\n            // Read-only capability query:'
b='''        #[cfg(k6_user_store)]
        if (user_restore.0 && user_restore.1!=129) || (0x86..=0x89).contains(&f.cmd) {
            if f.cmd != frame::CMD_END && f.cmd != frame::CMD_READ_FLASH_RAW {
                let r=user_restore_command(app,&mut user_restore,f.cmd,f.addr,&f.data[..f.len]);
                cable_send(f.cmd,f.addr,&r.to_le_bytes());
                continue;
            }
        }
'''
if b not in s:
 assert s.count(a)==1;s=s.replace(a,b+a)
a='    let mut card_commit_reply = [1u8, 0xFF, 0xFF, 0, 0];\n';b='    #[cfg(k6_user_store)]\n    let mut user_restore=(false,0u16,0u16);\n'
if b not in s:s=s.replace(a,a+b)
s+='''
#[cfg(k6_user_store)]
#[inline(never)]
fn user_restore_command(app:&mut App,state:&mut (bool,u16,u16),cmd:u8,addr:u16,data:&[u8])->i32 {
    unsafe extern "C" {static k6_build_id:[u8;8];fn km_cable_begin();fn ku_staged_valid(p:*const u8)->i32;}
    let nor=&mut app.storage_mut().norflash;
    match cmd {
        0x86 if addr==0 && data==unsafe{&*core::ptr::addr_of!(k6_build_id)} => {*state=(true,0,0);unsafe{km_cable_begin()};0}
        0x87 if state.0 && addr<128 && addr<=state.1 && state.2==0 && data.is_empty()=>{
            if addr<state.1{return 0;}
            nor.erase_sector(0x110000+u32::from(addr)*4096);state.1+=1;0
        }
        0x88 if state.0 && state.1==128 && addr<8192 && data.len()==64 => {
            // Transfer payload first, then the two directory bodies. No magic writes here.
            let order=if addr>=128{addr-128}else{8064+addr};
            if state.1!=128{return -2;}
            if order<state.2 {return 0;} // host verifies complete block readback before activation
            if (addr==0||addr==64)&&data[..4]!=[255;4]{return -2;}
            let pos=0x110000+u32::from(addr)*64;
            if !system_write_verified(nor,pos,data){return -5;}
            state.2=order+1;0
        }
        0x89 if state.0 && state.1==129 && addr==0 && data.is_empty()=>0,
        0x89 if state.0 && state.1==128 && state.2>8128 && addr==0 && data.is_empty()=>{
            let mut d=[0u8;1036];
            for base in [0x110000,0x111000] {
                if !nor.read_bytes_checked(base,&mut d) || d[..4]!=[255;4]{return -5;}
                d[..4].copy_from_slice(b"KUL2");
                if unsafe{ku_staged_valid(d.as_ptr())}!=1{return -2;}
            }
            if !system_write_verified(nor,0x110000,b"KUL2") || !system_write_verified(nor,0x111000,b"KUL2"){return -5;}
            *state=(true,129,0);0
        }
        _=>-2
    }
}
'''
p.write_text(s)
