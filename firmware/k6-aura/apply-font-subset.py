#!/usr/bin/env python3
"""Preserve all ASCII ProFont14 pixels while dropping unused Latin-1 atlas rows."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from k6_toolchain import resolve_cargo
src=Path(__file__).resolve().parent/'build/aura-fd6818b-port/src/ui'
import json, subprocess
work=src.parent.parent
try:
 cargo = resolve_cargo()
 meta=json.loads(subprocess.check_output([cargo,'metadata','--format-version','1','--manifest-path',str(work/'Cargo.toml')], cwd=work))
except (ValueError, OSError, subprocess.CalledProcessError) as exc:
 raise SystemExit(f'Build environment error: font metadata failed: {exc}') from None
fonts=[Path(pkg['manifest_path']).parent/'data/ProFont14Point.raw' for pkg in meta['packages'] if pkg['name']=='profont']
if len(fonts)!=1: raise SystemExit('Expected exactly one pinned ProFont dependency')
raw=fonts[-1].read_bytes()
assert len(raw)==4080
# Three rows of 32 glyphs contain the entire 95-character ASCII range.
(src/'profont14_ascii.raw').write_bytes(raw[:3*17*40])
p=src/'font_subset.rs';p.write_text('''use embedded_graphics::{geometry::Size,image::ImageRaw,mono_font::{mapping::StrGlyphMapping,DecorationDimensions,MonoFont}};
const MAP: StrGlyphMapping = StrGlyphMapping::new("\\0 ~",31);
pub const PROFONT_14_POINT: MonoFont = MonoFont {
 image: ImageRaw::new(include_bytes!("profont14_ascii.raw"),320),
 character_size: Size::new(10,17), character_spacing: 0, baseline: 13,
 underline: DecorationDimensions::new(15,1),strikethrough: DecorationDimensions::new(9,2),glyph_mapping: &MAP,
};
''')
p=src/'mod.rs';s=p.read_text();marker='mod font_subset;';
if marker not in s: p.write_text(s+'\n#[cfg(k6_font_subset)]\n'+marker+'\n')
for name in ['standby.rs','satellite.rs','fm.rs']:
 p=src/name;s=p.read_text();old='use profont::PROFONT_14_POINT;';new='#[cfg(not(k6_font_subset))]\n'+old+'\n#[cfg(k6_font_subset)]\nuse super::font_subset::PROFONT_14_POINT;';
 if new not in s: p.write_text(s.replace(old,new))
p=src/'boot.rs';s=p.read_text();old='const BIG: &MonoFont = &profont::PROFONT_14_POINT;';new='#[cfg(not(k6_font_subset))]\n'+old+'\n#[cfg(all(not(feature = "pager-only"),k6_font_subset))]\nconst BIG: &MonoFont = &super::font_subset::PROFONT_14_POINT;';
if new not in s:p.write_text(s.replace(old,new))
