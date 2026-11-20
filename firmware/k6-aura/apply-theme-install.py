#!/usr/bin/env python3
"""Authenticated cable-only, append-to-empty-slot K6T install."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
p=root/'app/pager.rs';s=p.read_text()
anchor='/// The cable session (cps::run_cable_session) starts:'
helper='''#[cfg(k6_designer)]
#[inline(never)]
fn cable_install_theme(app:&mut App,slot:u8,bytes:usize)->i32 {
    if app.is_transmitting() || !(slot<=8 || (16..32).contains(&slot)) ||
        bytes>8192 || bytes<12 {return -2;}
    let data=unsafe{core::slice::from_raw_parts(arena::base().add(16),bytes)};
    // Verify inner CRC and all offsets before touching SPI. The outer
    // signed envelope has already passed km_packet, including serial.
    if data.len()!=12+usize::from(u16::from_le_bytes([data[4],data[5]])) ||
        crate::ui::theme_reader::open(&mut |off,dst|{
            if let Some(src)=data.get(usize::from(off)..usize::from(off)+dst.len()){
                dst.copy_from_slice(src);true
            }else{false}
        }).is_none(){return -2;}
    let base=0x110000+u32::from(slot)*0x4000;
    let nor=&mut app.storage.norflash;
    let mut back=[0u8;64];
    // Refuse every occupied slot, including interrupted installs. Never erase
    // or overwrite a module, another theme, or unknown data.
    for off in (0..0x4000u32).step_by(64){
        if !nor.read_bytes_checked(base+off,&mut back) || back.iter().any(|&b|b!=255){return -7;}
    }
    // Commit magic last. A power failure can only leave an unselectable slot.
    let mut off=12usize;
    while off<bytes{
        let n=(256-off%256).min(bytes-off);
        nor.write_bytes(base+off as u32,&data[off..off+n]);
        off+=n;
    }
    for (i,chunk) in data[12..].chunks(64).enumerate(){
        if !nor.read_bytes_checked(base+12+i as u32*64,&mut back[..chunk.len()]) ||
            &back[..chunk.len()]!=chunk{return -5;}
    }
    nor.write_bytes(base,&data[..12]);
    if !nor.read_bytes_checked(base,&mut back[..12]) || back[..12]!=data[..12]{return -5;}
    bytes as i32
}

'''
# Separate typed removal from module removal. A valid package must exist on
# the radio, not just a matching magic. Clear an active selection before erase.
remove_helper="""#[cfg(k6_designer)]
#[inline(never)]
fn cable_remove_theme(app:&mut App,slot:u8)->i32 {
    if app.is_transmitting() || !(slot<=8 || (16..32).contains(&slot)){return -2;}
    let base=0x110000+u32::from(slot)*0x4000;
    if crate::ui::theme_reader::open(&mut |off,dst|{
        app.storage.norflash.read_bytes_checked(base+u32::from(off),dst)
    }).is_none(){return -2;}
    if app.settings.theme_selection==slot+2 || app.settings.theme_selection==0 {
        app.settings.theme_selection=1;
        app.save_settings();
    }
    let nor=&mut app.storage.norflash;
    let mut back=[0u8;64];
    for off in (0..0x4000u32).step_by(4096){nor.erase_sector(base+off);}
    for off in (0..0x4000u32).step_by(64){
        if !nor.read_bytes_checked(base+off,&mut back) || back.iter().any(|&b|b!=255){return -5;}
    }
    0
}

"""
if 'fn cable_remove_theme(' not in s:
    if s.count(anchor)!=1:raise SystemExit('theme removal helper anchor missing')
    s=s.replace(anchor,remove_helper+anchor,1)
if 'fn cable_install_theme(' not in s:
    if s.count(anchor)!=1:raise SystemExit('cable install helper anchor missing')
    s=s.replace(anchor,helper+anchor,1)
a='        if flags & KM_F_REMOVE != 0 {'
b='''        if flags & 0x10 != 0 {
            #[cfg(k6_designer)]
            {put=if flags==0x15 {cable_install_theme(app,unsafe{*arena::base().add(15)},code_len)}else{-2};}
            #[cfg(not(k6_designer))]
            {put=-2;}
        } else if flags & KM_F_REMOVE != 0 {'''
if b not in s and "K6THDEL" not in s:
    if s.count(a)!=1:raise SystemExit('cable flags anchor missing')
    s=s.replace(a,b,1)
old='            {put=if flags==0x15 {cable_install_theme(app,unsafe{*arena::base().add(15)},code_len)}else{-2};}'
new='            {let slot=unsafe{*arena::base().add(15)};\n             put=if flags==0x15 {cable_install_theme(app,slot,code_len)}\n                 else if flags==0x1d && code_len==7 &&\n                     unsafe{core::slice::from_raw_parts(arena::base().add(16),7)}==b"K6THDEL" {\n                     cable_remove_theme(app,slot)\n                 }else{-2};}'
if new not in s:
    if s.count(old)!=1:raise SystemExit('theme removal dispatch anchor missing')
    s=s.replace(old,new,1)
p.write_text(s)
p=root/'cps/mod.rs';s=p.read_text()
a='                d[4] = 2;'
b='                d[4] = if cfg!(k6_designer) {4} else {2};'
if '                d[4] = if cfg!(k6_designer) {3} else {2};' in s:
    s=s.replace('                d[4] = if cfg!(k6_designer) {3} else {2};',b,1)
if b not in s and "d[4] = if cfg!(k6_user_store)" not in s:
    if s.count(a)!=1:raise SystemExit('protocol anchor missing')
    s=s.replace(a,b,1)
p.write_text(s)
