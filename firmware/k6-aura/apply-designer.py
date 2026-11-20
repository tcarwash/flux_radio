#!/usr/bin/env python3
"""Read-only Designer bench prototype; no storage allocation or writes."""
import os
from pathlib import Path
here = Path(__file__).parent
src = here / 'build/aura-fd6818b-port/src'
slot = os.environ.get('K6_DESIGNER_TEST_SLOT', '')
if slot and (not slot.isdecimal() or not 0 <= int(slot) < 32 or 9 <= int(slot) <= 15):
    raise SystemExit('Designer test slot must be 0..8 or 16..31')
slot = int(slot) if slot else 255
(src/'ui/theme_reader.rs').write_text((here/'theme-reader.rs').read_text())
(src/'ui/designer.rs').write_text((here/'designer-render.rs').read_text().replace('TEST_SLOT', str(slot)))
p = src/'ui/mod.rs'
# Normalize declarations, including generated trees from older patches.
import re
s = p.read_text()
s = re.sub(r'\n#\[cfg\(k6_designer\)\]\n(?:pub\(crate\) )?mod (?:designer|theme_reader);', '', s)
s = re.sub(r'\n#\[cfg\(k6_designer\)\]\npub\(crate\) use designer::boot_logo as designer_boot_logo;', '', s)
anchor = 'mod standby;'
extra = '\n#[cfg(k6_designer)]\npub(crate) mod theme_reader;\n#[cfg(k6_designer)]\npub(crate) mod designer;'
if s.count(anchor)!=1: raise SystemExit('UI module anchor changed')
s = s.replace(anchor,anchor+extra,1)
old = '        #[cfg(feature = "blowfang-host")]\n        _ => draw_standby(display.as_draw_target(), &*app),'
previous = '''        #[cfg(feature = "blowfang-host")]
        _ => {
            draw_standby(display.as_draw_target(), &*app);
            #[cfg(k6_designer)]
            if !designer::draw(display.as_draw_target(), app) {
                draw_standby(display.as_draw_target(), &*app);
            }
        },'''
new = '''        #[cfg(feature = "blowfang-host")]
        _ => {
            #[cfg(k6_designer)]
            let themed = designer::draw(display.as_draw_target(), app);
            #[cfg(not(k6_designer))]
            let themed = false;
            if !themed { draw_standby(display.as_draw_target(), &*app); }
        },'''
if new not in s:
    if previous in s: s=s.replace(previous,new,1)
    elif s.count(old)==1: s=s.replace(old,new,1)
    else: raise SystemExit('standby dispatch anchor changed')
p.write_text(s)
p=src/'app/mod.rs'
s=p.read_text()
old='    pub fn master_index(&self) -> usize {'
new='''    #[cfg(k6_designer)]
    pub(crate) fn designer_read(&mut self, addr: u32, dst: &mut [u8]) -> bool {
        self.storage.norflash.read_bytes_checked(addr, dst)
    }

'''+old
if 'fn designer_read(' not in s:
    if s.count(old)!=1: raise SystemExit('app anchor changed')
    s=s.replace(old,new)
p.write_text(s)

# The helper uses the existing native data rendering, not a duplicate formatter.
# Factory glyph/style patches still target the original functions.
p = src/'ui/standby.rs'
s = p.read_text()
start = '// K6 Designer native content begin\n'
end = '// K6 Designer native content end\n'
if start in s:
    a,b=s.index(start),s.index(end)
    s=s[:a]+s[b+len(end):]
helper = (here/'designer-content.rs').read_text()
# Remove pre-alpha group renderer hooks from reused generated trees.
if "fn draw_vfo_band_at<D>" in s:
    a=s.index("fn draw_vfo_band<D:");b=s.index("fn draw_vfo_band_at<D>",a)
    s=s[:a]+s[b:]
    s=s.replace("fn draw_vfo_band_at<D>(lcd: &mut D, app: &app::App, i: usize, top: i32, c: &[u8;10], style: Option<(u8,u8)>)","fn draw_vfo_band<D>(lcd: &mut D, app: &app::App, i: usize, top: i32)")
    s=s.replace("Point::new(i32::from(c[2]), top + i32::from(c[3]) + FONT_5X8.baseline as i32)","Point::new(12, top + FONT_5X8.baseline as i32)")
    s=s.replace("Point::new(i32::from(c[4]), top + i32::from(c[5]) + FONT_5X8.baseline as i32)","Point::new(2, top + 8 + FONT_5X8.baseline as i32)")
    s=s.replace("draw_mode_line_at(lcd, app, i, top + i32::from(c[9])+5,i32::from(c[8]))","draw_mode_line_at(lcd, app, i, top + BAND_HEIGHT - 1,2)")
    a=s.index("#[cfg(k6_designer)]\n                if let Some((font,inset))=style")
    b=s.index("if !custom { draw_frequency(lcd, freq_hz, top + PROFONT_14_POINT.baseline as i32); }",a)
    s=s[:a]+"draw_frequency(lcd, freq_hz, top + PROFONT_14_POINT.baseline as i32);"+s[b+len("if !custom { draw_frequency(lcd, freq_hz, top + PROFONT_14_POINT.baseline as i32); }"):]
if "fn draw_status_bar_children<D>" in s:
    a=s.index("fn draw_status_bar_at<D:");b=s.index("fn draw_status_bar_children<D>",a)
    s=s[:a]+s[b:]
    s=s.replace("fn draw_status_bar_children<D>(lcd: &mut D, app: &app::App, row_y: i32, c: Option<&[u8;30]>)","fn draw_status_bar<D>(lcd: &mut D, app: &app::App)")
    s=s.replace("c.map(|v|i32::from(v[0])).unwrap_or(16)","16").replace("c.map(|v|i32::from(v[2])).unwrap_or(x)","x")
    s=s.replace("c.map(|v|i32::from(v[4])+17).unwrap_or(128 - RIGHT_MARGIN)","128 - RIGHT_MARGIN")
    a=s.index("fn draw_status_bar<D>");b=s.index("fn draw_vfo_band<D>",a)
    v=s[a:b].replace("row_y + FONT_5X8.baseline as i32","FONT_5X8.baseline as i32").replace("Point::new(99, row_y)","Point::new(99, 0)").replace("128 - RIGHT_MARGIN, row_y, app.battery_bars()","128 - RIGHT_MARGIN, 0, app.battery_bars()")
    s=s[:a]+v+s[b:]
    s=s.replace("draw_status_bar_at(lcd, app, 0);","draw_status_bar(lcd, app);")
if "fn draw_mode_line_at<D>" not in s:
    a=s.index("fn draw_mode_line<D>");b=s.index("fn write_spaced_fields",a)
    v=s[a:b].replace("fn draw_mode_line<D>(lcd: &mut D, app: &app::App, i: usize, baseline_y: i32)","fn draw_mode_line_at<D>(lcd: &mut D, app: &app::App, i: usize, baseline_y: i32, x:i32)").replace("Point::new(2, baseline_y)","Point::new(x, baseline_y)")
    s=s[:a]+v+s[b:]
    s=s.replace("draw_mode_line(lcd, app, i, top + BAND_HEIGHT - 1)","draw_mode_line_at(lcd, app, i, top + BAND_HEIGHT - 1,2)")
if "fn mode_fields(" not in s:
    a=s.index("fn draw_mode_line_at<D>")
    b=s.index("    let modulation = match",a)
    c=s.index("    let mut line: TextBuf<32>",b)
    fields=s[b:c]
    s=s[:a]+"pub(super) fn mode_fields(app: &app::App, i: usize) -> [&'static str;8] {\n"+fields+"    [modulation, power, tone, dir, bandwidth, reversed, bk_in, rpt_hint]\n}\n\n"+s[a:b]+s[c:]
    s=s.replace("&[modulation, power, tone, dir, bandwidth, reversed, bk_in, rpt_hint],","&mode_fields(app,i),")
s=s.replace("\nfn mode_fields(", "\npub(super) fn mode_fields(")
a = s.index("pub(crate) fn draw_frequency<D>")
b = s.index("/// Mid-entry VFO frequency:",a)
original = s[a:b]
# Keep the factory entry point and share the actual split-digit renderer.
if "fn draw_frequency_at<D>" not in original:
    shared = original.strip().replace("pub(crate) fn draw_frequency<D>", "#[inline(never)]\nfn draw_frequency_at<D>", 1)
    shared = shared.replace("baseline_y: i32)", "baseline_y: i32, dx: i32)", 1)
    shared = shared.replace("128 - RIGHT_MARGIN - small_width - big_width", "128 - RIGHT_MARGIN - small_width - big_width + dx", 1)
    wrapper = "pub(crate) fn draw_frequency<D>(lcd: &mut D, freq_hz: u32, baseline_y: i32) where D: DrawTarget<Color = BinaryColor> { draw_frequency_at(lcd, freq_hz, baseline_y, 0); }\n\n"
    s = s[:a] + wrapper + shared + "\n\n" + s[b:]
p.write_text(s+'\n'+start+helper+'\n'+end)

p=src/'ui/mod.rs'
s=p.read_text()
anchor='mod designer;'
new=anchor+'\n#[cfg(k6_designer)]\npub(crate) use designer::boot_logo as designer_boot_logo;'
if new not in s:
    s=s.replace(anchor,new,1)
p.write_text(s)
