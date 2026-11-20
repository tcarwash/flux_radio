#!/usr/bin/env python3
"""Level 24: bounded raw 2FSK, preserving older table offsets."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'app/pager.rs';s=p.read_text()
import sys,re
if '--reset-level' in sys.argv:
 s=re.sub(r'level: if cfg!\(all\(k6_config_fsk,.*?\n', 'level: if cfg!(feature = "fd6818b") { 23 } else { 9 },\n',s)
 p.write_text(s);sys.exit(0)
if 'pub fsk_configured:' not in s:
 s=s.replace('        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,','        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,\n        pub fsk_configured: unsafe extern "C" fn(u32, *mut u16, u32, u32) -> i32,')
 s=s.replace('    aprs_position_set: mod_aprs_position_set,','    aprs_position_set: mod_aprs_position_set,\n    fsk_configured: mod_fsk_configured,')
 s=s.replace('level: if cfg!(feature = "fd6818b") { 23 } else { 9 },','level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} else { if cfg!(feature = "fd6818b") { 23 } else { 9 } },')
 s+='''
// op=0 RX start, 1 RX read (0 not ready, n words received), 2 TX (0 success).
// config: low 16 bits baud (1200/2400), bits 16..23 gain (16..96), high byte zero.
#[cfg(all(k6_config_fsk,not(k6_no_fsk_rx),feature="fd6818b"))]
unsafe extern "C" fn mod_fsk_configured(op:u32, ptr:*mut u16, n:u32, config:u32)->i32 {
    if n==0 || n>128 || config>>24!=0 || op>2 || (op!=0 && (ptr.is_null() || ptr as usize & 1 != 0)) {return -1;}
    let baud=config as u16; let gain=(config>>16) as u8;
    if !matches!(baud,1200|2400) || !(16..=96).contains(&gain) {return -1;}
    mod_tx_guard();
    let c=unsafe{&*addr_of!(MOD_CTX)};
    if c.app.is_null() || c.tx!=(op==2) {return -1;}
    // Ensure buffers belong to the module arena, not the resident application or MMIO.
    if op!=0 && ((ptr as usize)<arena::base() as usize || (ptr as usize).saturating_add(n as usize*2)>arena::end()) {return -1;}
    let (app,syst)=unsafe{mod_ctx()}; let fd=&mut app.radio.fd6818;
    if op==1 {
        if fd.reg_read(syst,0x5d)>>8 != (n*2-1) as u16 {return -1;}
        if !fd.fsk_rx_ready(syst) {return 0;}
        for i in 0..n as usize {unsafe{ptr.add(i).write(fd.reg_read(syst,0x5f));}}
        fd.reg_write(syst,0x02,0);return n as i32;
    }
    let base=0x00f8;
    for (r,v) in [(0x59,base),(0x02,0),(0x3f,if op==2{0x8000}else{0x2000}),(0x58,if baud==2400 && op==0{0x00e9}else{0x00e1}),(0x70,0x80|gain as u16),(0x72,if baud==2400{0x60ca}else{0x3065}),(0x5a,0x076b),(0x5b,0x3e36),(0x5c,0x5625),(0x5d,((n*2-1)<<8) as u16)] {fd.reg_write(syst,r,v);}
    if op==0 {fd.reg_write(syst,0x59,base|0x4000);fd.reg_write(syst,0x59,base|0x1000);return 0;}
    fd.reg_write(syst,0x59,base|0x8000);fd.reg_write(syst,0x59,base);
    for i in 0..n as usize {fd.reg_write(syst,0x5f,unsafe{ptr.add(i).read()});}
    fd.reg_write(syst,0x59,base|0x0800);
    let mut ok=false;
    let polls=if baud==1200 {400}else{220};
    for _ in 0..polls {crate::hal::delay::ms(syst,5);if fd.reg_read(syst,0x0c)&1!=0 {ok=true;break;}}
    fd.exit_fsk_mode(syst);
    mod_tx_guard();
    if !ok {unsafe{mod_tx_off()};return -2;}
    0
}
#[cfg(not(all(k6_config_fsk,not(k6_no_fsk_rx),feature="fd6818b")))]
unsafe extern "C" fn mod_fsk_configured(_:u32,_:*mut u16,_:u32,_:u32)->i32 {-1}
'''
 p.write_text(s)
s=p.read_text().replace('level: if cfg!(feature = "fd6818b") { 23 } else { 9 },','level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} else if cfg!(feature = "fd6818b") { 23 } else { 9 },',1)
p.write_text(s)
p=root/'cps/mod.rs';s=p.read_text();import re
s=re.sub(r"(b'P', 2, \d+, )23, 0,",r'\1if cfg!(all(k6_config_fsk,not(k6_no_fsk_rx))) {24} else {23}, 0,',s);p.write_text(s)
