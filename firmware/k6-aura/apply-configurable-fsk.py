#!/usr/bin/env python3
"""Level 24: bounded raw 2FSK, preserving older table offsets."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'app/pager.rs';s=p.read_text()
import sys,re
if '--reset-level' in sys.argv:
 s=re.sub(r'level: if cfg!\(k6_(?:async_fsk|live_voice)\).*?\n', 'level: if cfg!(feature = "fd6818b") { 23 } else { 9 },\n',s)
 s=re.sub(r'level: if cfg!\(all\((?:k6_config_fsk|k6_live_serial),.*?\n', 'level: if cfg!(feature = "fd6818b") { 23 } else { 9 },\n',s)
 p.write_text(s);sys.exit(0)
if 'pub fsk_configured:' not in s:
 s=s.replace('        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,','        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,\n        pub fsk_configured: unsafe extern "C" fn(u32, *mut u16, u32, u32) -> i32,')
 s=s.replace('    aprs_position_set: mod_aprs_position_set,','    aprs_position_set: mod_aprs_position_set,\n    fsk_configured: mod_fsk_configured,')
 s=s.replace('level: if cfg!(feature = "fd6818b") { 23 } else { 9 },','level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} else { if cfg!(feature = "fd6818b") { 23 } else { 9 } },')
# Refresh the generated implementation on repeated builds, preserving later overlays.
body='''
// op=0 RX start, 1 RX read (0 not ready, n words received), 2 TX (0 success).
// config: low 16 bits baud (1200/2400, or the physically tested 4800 TX register shape), bits 16..23 gain, high byte zero.
#[cfg(all(k6_config_fsk,not(k6_no_fsk_rx),feature="fd6818b"))]
unsafe extern "C" fn mod_fsk_configured(op:u32, ptr:*mut u16, n:u32, config:u32)->i32 {
    if n==0 || n>128 || config>>24!=0 || op>2 || (op!=0 && (ptr.is_null() || ptr as usize & 1 != 0)) {return -1;}
    let baud=config as u16; let gain=(config>>16) as u8;
    let fast = baud == 4800;
    // The 4800-baud B-chip evidence covers full preloading of 33 words
    // with gain 21, not arbitrary lengths, refill or 4800 RX.
    if fast { if op != 2 || n != 33 || gain != 21 {return -1;} }
    else if !matches!(baud,1200|2400) || !(16..=96).contains(&gain) {return -1;}
    mod_tx_guard();
    let c=unsafe{&*addr_of!(MOD_CTX)};
    if c.app.is_null() || c.tx!=(op==2) {return -1;}
    // Ensure buffers belong to the module arena, not the resident application or MMIO.
    if op!=0 && ((ptr as usize)<arena::base() as usize || (ptr as usize).saturating_add(n as usize*2)>arena::end()) {return -1;}
    if fast { return unsafe {mod_fsk_tx4800(ptr)}; }
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
// A bounded, payload-agnostic 33-word native TX frame. The caller owns
// tx_on()/tx_off() and the packet layout, including any sacrificial guard.
// Return with PA OFF; never acknowledge a stale IRQ or a lost TX lease.
#[cfg(all(k6_config_fsk,not(k6_no_fsk_rx),feature="fd6818b"))]
unsafe fn mod_fsk_tx4800(ptr:*mut u16)->i32 {
    const REGS:[u8;9]=[0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d];
    const VALS:[u16;9]=[0x8000,0x00f8,0x00e1,0x0095,0xc194,0x076b,0x3e36,0x5625,0x4100];
    // A rejected request must not leave a previously granted TX lease keyed.
    if unsafe{mod_tx_pa(0)}!=0 || unsafe{mod_fd_read(0)}!=0x4829 ||
       unsafe{mod_key_down()}!=0 || unsafe{mod_cancel_poll()}==1 {
        unsafe{mod_tx_off()};return -1;
    }
    let mut old=[0u16;9];
    for i in 0..9 {old[i]=unsafe{mod_fd_read(REGS[i])};}
    if old[0]&0xa000!=0 || old[1]&0x0800!=0 {unsafe{mod_tx_off()};return -1;}
    let result=(|| {
        let wr=|r,v| {
            mod_tx_guard();
            if !unsafe{(*addr_of!(MOD_CTX)).tx} {return false;}
            let (app,syst)=unsafe{mod_ctx()};
            // These fixed, private RFIC addresses are not caller-controlled.
            // Do not call mod_fd_write after a second internal lease check.
            app.radio.fd6818.reg_write(syst,r,v);
            true
        };
        if !wr(0x59,0x00f8) || !wr(0x02,0) {return -2;}
        for i in 0..9 {if !wr(REGS[i],VALS[i]) {return -2;}}
        if !wr(0x59,0x80f8) || !wr(0x59,0x00f8) {return -2;}
        for i in 0..33 {
            if unsafe{mod_key_down()}!=0 || unsafe{mod_cancel_poll()}==1 ||
               !wr(0x5f,unsafe{ptr.add(i).read()}) {return -2;}
        }
        if unsafe{mod_fd_read(0x0c)}&1!=0 || unsafe{mod_key_down()}!=0 ||
           unsafe{mod_cancel_poll()}==1 || unsafe{mod_tx_pa(1)}!=0 {return -2;}
        if !wr(0x59,0x08f8) {return -2;}
        for _ in 0..140 {
            mod_tx_guard();
            if !unsafe{(*addr_of!(MOD_CTX)).tx} || unsafe{mod_key_down()}!=0 ||
               unsafe{mod_cancel_poll()}==1 || unsafe{mod_fd_read(0x30)}&2==0 {return -2;}
            if unsafe{mod_fd_read(0x0c)}&1!=0 {return 0;}
            unsafe{mod_delay_us(5000);}
        }
        -2
    })();
    // mod_tx_guard may already have reconfigured the RFIC for receive.
    // Never restore TX-era register snapshots into a dropped lease.
    mod_tx_guard();
    if !unsafe{(*addr_of!(MOD_CTX)).tx} {return -2;}
    if unsafe{mod_tx_pa(0)}!=0 || result!=0 {unsafe{mod_tx_off()};return -2;}
    let (app,syst)=unsafe{mod_ctx()};
    for (r,v) in [(0x59,0x00f8),(0x3f,0),(0x02,0)] {
        app.radio.fd6818.reg_write(syst,r,v);
    }
    for i in (0..9).rev() {app.radio.fd6818.reg_write(syst,REGS[i],old[i]);}
    mod_tx_guard();
    if !unsafe{(*addr_of!(MOD_CTX)).tx} {return -2;}
    0
}
#[cfg(not(all(k6_config_fsk,not(k6_no_fsk_rx),feature="fd6818b")))]
unsafe extern "C" fn mod_fsk_configured(_:u32,_:*mut u16,_:u32,_:u32)->i32 {-1}
'''
start=s.find('\n// op=0 RX start, 1 RX read')
if start>=0:
 end=s.index('unsafe extern "C" fn mod_fsk_configured(_:u32,_:*mut u16,_:u32,_:u32)->i32 {-1}',start)+len('unsafe extern "C" fn mod_fsk_configured(_:u32,_:*mut u16,_:u32,_:u32)->i32 {-1}')
 s=s[:start]+body.rstrip()+s[end:]
else:s+=body
p.write_text(s)
s=p.read_text().replace('level: if cfg!(feature = "fd6818b") { 23 } else { 9 },','level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} else if cfg!(feature = "fd6818b") { 23 } else { 9 },',1)
p.write_text(s)
p=root/'cps/mod.rs';s=p.read_text();import re
s=re.sub(r"(b'P', 2, \d+, )23, 0,",r'\1if cfg!(all(k6_config_fsk,not(k6_no_fsk_rx))) {24} else {23}, 0,',s);p.write_text(s)
