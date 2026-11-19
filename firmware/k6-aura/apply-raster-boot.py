#!/usr/bin/env python3
"""Compact boot raster, optional Designer-only overlays. Preserve legacy settings bytes."""
from pathlib import Path
import os
here=Path(__file__).resolve().parent
src=here/'build/aura-fd6818b-port/src'
art=Path(os.environ.get('K6_FLUX_ART_DIR',here.parent.parent/'flux-art-kit/flux-arrays'))/'splash.bin'
raw=art.read_bytes()
assert len(raw)==1024
(src/'ui/flux_boot.bin').write_bytes(raw)
p=src/'main.rs';s=p.read_text()
if '// Flux raster boot' not in s:
    a=s.index('        match boot_mode {');b=s.index('        if boot_mode != 0 &&',a)
    s=s[:a]+'        #[cfg(not(k6_raster_boot))]\n'+s[a:b]+'''        // Flux raster boot
        #[cfg(k6_raster_boot)]
        if boot_mode != 0 {
            #[cfg(k6_designer)]
            {
                let settings = *app.settings();
                let voltage = app.battery_voltage_cv();
                screen!(display, |lcd| ui::boot::draw_raster_overlay(lcd, app.storage_mut(), &settings, voltage));
            }
            #[cfg(not(k6_designer))]
            screen!(display, |lcd| ui::boot::draw_raster(lcd));
        }
'''+s[b:];p.write_text(s)
p=src/'ui/boot.rs';s=p.read_text()
if 'pub fn draw_raster<' in s:
    s=s[:s.index('#[cfg(k6_raster_boot)]\npub fn draw_raster<')]
if 'pub fn draw_raster<' not in s:
    s+='''
#[cfg(k6_raster_boot)]
pub fn draw_raster<D: DrawTarget<Color=BinaryColor>>(lcd: &mut D) {
    draw_logo(lcd, include_bytes!("flux_boot.bin"));
}
#[cfg(all(k6_raster_boot,k6_designer))]
#[inline(never)]
fn draw_stored_raster<D: DrawTarget<Color=BinaryColor>>(lcd: &mut D, storage: &mut crate::device::storage::Storage) -> bool {
    // Never place a 1 KiB bitmap on the resident stack. Read one LCD page at a time.
    let mut page=[0u8;128];
    let (mut nonzero,mut nonblank)=(false,false);
    for i in 0..8 { storage.read_boot_logo_chunk(i*128,&mut page); nonzero |= page.iter().any(|&b|b!=0); nonblank |= page.iter().any(|&b|b!=255); }
    if !nonzero || !nonblank {return false;}
    clear(lcd);
    for i in 0..8 {storage.read_boot_logo_chunk(i*128,&mut page); lcd.draw_iter(page.iter().enumerate().flat_map(|(x,&b)| (0..8).filter(move |&bit|b&(1<<bit)!=0).map(move |bit|Pixel(Point::new(x as i32,(i*8+bit) as i32),BinaryColor::On)))).ok();}
    true
}
#[cfg(all(k6_raster_boot,k6_designer))]
#[inline(never)]
pub fn draw_raster_overlay<D: DrawTarget<Color=BinaryColor>>(lcd: &mut D, storage: &mut crate::device::storage::Storage, settings: &Settings, voltage: u16) {
    if settings.boot_display_mode != 3 || !draw_stored_raster(lcd, storage) { draw_raster(lcd); }
    if settings.boot_display_mode == 1 {
        let mut line: TextBuf<16> = TextBuf::new();
        write!(line, "{}.{:02}V", voltage / 100, voltage % 100).ok();
        lcd.fill_solid(&Rectangle::new(Point::new(0,54),Size::new(128,10)),BinaryColor::Off).ok();
        draw_centered_small(lcd,line.as_str(),63);
    } else if settings.boot_display_mode == 2 {
        let a=ascii_line(&settings.boot_text_line1); let b=ascii_line(&settings.boot_text_line2);
        if !a.is_empty() || !b.is_empty() {
            lcd.fill_solid(&Rectangle::new(Point::new(0,44),Size::new(128,20)),BinaryColor::Off).ok();
            draw_centered_small(lcd,a,53); draw_centered_small(lcd,b,63);
        }
    }
}
''';p.write_text(s)
