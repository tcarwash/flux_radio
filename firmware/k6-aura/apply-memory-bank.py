#!/usr/bin/env python3
"""Bounded cable channel-bank transactions; no general-purpose flash writes."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'app/side.rs';s=p.read_text();a='        if self.reversed {\n            self.cfg.freq_hz = tx;';b='        if self.vfo_chan == ChVfoMode::Channel && tx==0 {\n            self.cfg.freq_hz=rx;self.cfg.tx_freq_hz=0;\n        } else if self.reversed {\n            self.cfg.freq_hz = tx;';assert a in s or b in s;s=s.replace(a,b);s=s.replace('        if ch.decoder_code & 0xFFFF_FF00 == 0xBF6D_0000 {','        self.cfg.modulation=Modulation::Fm;\n        if ch.decoder_code & 0xFFFF_FF00 == 0xBF6D_0000 {') if 'self.cfg.modulation=Modulation::Fm;' not in s else s;p.write_text(s)
p=root/'app/side_ops.rs';s=p.read_text();a='    s.reversed = !s.reversed;';b='    if s.vfo_chan==ChVfoMode::Channel && s.tx_freq_hz==0{return;}\n'+a
if b not in s:s=s.replace(a,b);p.write_text(s)
p=root/'cps/mod.rs';s=p.read_text();a='        match f.cmd {\n            // Read-only capability query:';b='''        #[cfg(k6_memory_bank)]
        if memory_stage.0 || (0x90..=0x94).contains(&f.cmd) {
            if f.cmd!=frame::CMD_END {
                memory_bank(app,&mut memory_stage,f.cmd,f.addr,&f.data[..f.len]);continue;
            }
        }
''';assert a in s
if b not in s:s=s.replace(a,b+a)
a='    let mut idle_ms: u32 = 0;';# session-specific anchor
start=s.index('fn run_cable_session_inner');i=s.index(a,start);b=a+'\n    #[cfg(k6_memory_bank)]\n    let mut memory_stage=(false,0u16,0u16);'
if b not in s:s=s[:i]+s[i:].replace(a,b,1)
marker='\n#[cfg(k6_memory_bank)]\n#[inline(never)]\nfn memory_bank('
if marker in s:s=s[:s.index(marker)]
s+='''
#[cfg(k6_memory_bank)]
#[inline(never)]
fn memory_bank(app:&mut App,state:&mut (bool,u16,u16),cmd:u8,addr:u16,data:&[u8]) {
    let buf=crate::hal::arena::base();
    let nor=&mut app.storage_mut().norflash;
    // Eight 4 KiB sectors (1024 physical records, 1000 exposed memories).
    // Last 24 records remain opaque and are preserved on every write.
    let r=match cmd {
        0x90 if !state.0 && addr==0 && data.is_empty()=>{cable_send(cmd,addr,b"K6MB\\x01\\x20\\xe8\\x03");return;}
        0x91 if !state.0 && addr<512 && data.is_empty()=>{
            let mut b=[0u8;64];if nor.read_bytes_checked(u32::from(addr)*64,&mut b){cable_send(cmd,addr,&b);return;} -5
        }
        0x92 if addr<8 && data.is_empty()=>{unsafe extern "C" {fn km_reset();}unsafe{km_reset();}*state=(true,addr,0);0}
        0x93 if state.0 && data.len()==64 && addr<64 =>{
            if addr==state.2 {unsafe{core::ptr::copy_nonoverlapping(data.as_ptr(),buf.add(addr as usize*64),64);}state.2+=1;0}
            else if addr+1==state.2 && unsafe{core::slice::from_raw_parts(buf.add(addr as usize*64),64)}==data{0}else{-2}
        }
        0x94 if state.0 && state.1==addr && state.2==64 && data.is_empty()=>{
            let pos=u32::from(addr)*4096;
            let bytes=unsafe{core::slice::from_raw_parts(buf,4096)};
            // Read-only reserved tail: firmware refuses even an accidental change.
            let mut old=[0u8;64];
            if addr==7 && (52..64).any(|i|!nor.read_bytes_checked(pos+i*64,&mut old)||old!=bytes[i as usize*64..i as usize*64+64]){-2}
            else {nor.erase_sector(pos);if system_write_verified(nor,pos,bytes){*state=(false,0,0);0}else{-5}}
        }
        _=>-2
    };cable_send(cmd,addr,&(r as i32).to_le_bytes());
}
''';p.write_text(s)
