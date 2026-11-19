#!/usr/bin/env python3
"""Brand the FD6818B host UI as Flux and expose build metadata in System > About."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent / "build/aura-fd6818b-port/src"
FEATURES = os.environ.get("K6_AURA_FEATURES", "blowfang-host,fd6818b,lean,spilib,txdog-quiet")
SCAN = os.environ.get("K6_SCAN_SERVICE", "1") != "0"
FSK_RX = os.environ.get("K6_FSK_RX_SERVICE", "0") != "0"
FLASH_USED = int(os.environ.get("K6_FLASH_USED_BYTES", "0"))


def edit(path, transform):
    p = ROOT / path
    old = p.read_text()
    new = transform(old)
    if old != new:
        p.write_text(new)


def replace_once(text, old, new, label):
    if new in text:
        return text
    if text.count(old) != 1:
        raise SystemExit(f"Flux branding: expected one {label} anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


def boot(text):
    text = replace_once(text, 'draw_firmware_banner(lcd, "UV-K6x Aura", 40);',
                        'draw_firmware_banner(lcd, "FLUX RADIO", 40);', "boot banner")
    marker = '/// Boot display mode 1: "VOLTAGE" / actual battery voltage, f4hwn-style.\n'
    if "Flux Vector glyph v1" not in text:
        fn = '''/// Flux startup splash with a small one-bit logo and optional user boot text.
pub fn draw_flux_splash<D>(lcd: &mut D, settings: &Settings)
where D: DrawTarget<Color = BinaryColor>, {
    use embedded_graphics::primitives::PrimitiveStyle;
    clear(lcd);
    // 16x16 monochrome Flux Vector glyph v1 (row-major bitmap), rendered directly to pixels.
    const LOGO: [u16; 16] = [
        0b0000000000000000,
        0b0000011111111110,
        0b0000011111111110,
        0b0000111101111110,
        0b0000111011111100,
        0b0001110111000000,
        0b0001110111111000,
        0b0001100000110000,
        0b0011000001110000,
        0b0011100011100000,
        0b0011100100000000,
        0b0111001000000000,
        0b0111010000000000,
        0b1110100000000000,
        0b1111000000000000,
        0b0000000000000000,
    ];
    let logo_pixels = LOGO.iter().enumerate().flat_map(|(y, row)| {
        (0..16usize).filter_map(move |x| {
            if row & (1 << (15 - x)) != 0 {
                Some(Pixel(Point::new(20 + x as i32, 8 + y as i32), BinaryColor::On))
            } else { None }
        })
    });
    lcd.draw_iter(logo_pixels).ok();
    Rectangle::new(Point::new(44, 5), Size::new(70, 23))
        .into_styled(PrimitiveStyle::with_stroke(BinaryColor::On, 1)).draw(lcd).ok();
    draw_centered_big(lcd, "FLUX", 22);
    draw_centered_small(lcd, "AG7SU  /  K6", 42);
    let a = ascii_line(&settings.boot_text_line1);
    let b = ascii_line(&settings.boot_text_line2);
    if !a.is_empty() && !b.is_empty() {
        draw_centered_small(lcd, a, 52);
        draw_centered_small(lcd, b, 61);
    } else if !a.is_empty() {
        draw_centered_small(lcd, a, 57);
        draw_centered_small(lcd, FIRMWARE_VERSION, 63);
    } else if !b.is_empty() {
        draw_centered_small(lcd, b, 57);
        draw_centered_small(lcd, FIRMWARE_VERSION, 63);
    } else {
        draw_centered_small(lcd, FIRMWARE_VERSION, 58);
    }
}

'''
        if 'pub fn draw_flux_splash' in text:
            start = text.index('pub fn draw_flux_splash')
            doc = text.rfind('/// Flux startup splash', 0, start)
            end = text.index(marker, start)
            text = text[:doc] + fn + text[end:]
        else:
            text = replace_once(text, marker, fn + marker, "splash insertion")
    logo_anchor = "fn put<D>(lcd: &mut D, font: &MonoFont, x: i32, y: i32, text: &str, c: BinaryColor)" + chr(10)
    if "pub fn draw_logo_with_text" not in text:
        logo_fn = '''#[cfg(not(feature = "pager-only"))]
pub fn draw_logo_with_text<D>(lcd: &mut D, page_buf: &[u8; crate::flash_map::BOOT_LOGO_SIZE], settings: &Settings)
where D: DrawTarget<Color = BinaryColor>, {
    draw_logo(lcd, page_buf);
    let a = ascii_line(&settings.boot_text_line1);
    let b = ascii_line(&settings.boot_text_line2);
    if a.is_empty() && b.is_empty() { return; }
    lcd.fill_solid(&Rectangle::new(Point::new(0, 42), Size::new(128, 22)), BinaryColor::Off).ok();
    if !a.is_empty() && !b.is_empty() {
        draw_centered_small(lcd, a, 53);
        draw_centered_small(lcd, b, 63);
    } else {
        draw_centered_small(lcd, if a.is_empty() { b } else { a }, 58);
    }
}

'''
        text = replace_once(text, logo_anchor, logo_fn + logo_anchor, "logo text overlay")
    return text


def startup(text):
    if "// Flux raster boot" in text:
        return text
    old = '''        let boot_mode = app.settings().boot_display_mode;
        screen!(display, |lcd| ui::boot::draw_flux_splash(lcd, app.settings()));'''
    new = '''        let boot_mode = app.settings().boot_display_mode;
        match boot_mode {
            1 => screen!(display, |lcd| ui::boot::draw_voltage(lcd, app.battery_voltage_cv())),
            2 => screen!(display, |lcd| ui::boot::draw_message(lcd, app.settings())),
            3 => {
                let boot_settings = *app.settings();
                screen!(display, |lcd| show_boot_logo(lcd, app.storage_mut(), &boot_settings));
            }
            _ => {}
        }'''
    if old not in text and new not in text:
        old = """        let boot_mode = app.settings().boot_display_mode;
        match boot_mode {
            1 => {
                screen!(display, |lcd| ui::boot::draw_voltage(lcd, app.battery_voltage_cv()));
            }
            2 => {
                screen!(display, |lcd| ui::boot::draw_message(lcd, app.settings()));
            }
            3 => {
                screen!(display, |lcd| show_boot_logo(lcd, app.storage_mut()));
            }
            _ => {}
        }"""
    text = replace_once(text, old, new, "boot display selection")
    old_logo = '''fn show_boot_logo<D>(lcd: &mut D, storage: &mut Storage)
where
    D: embedded_graphics::draw_target::DrawTarget<Color = embedded_graphics::pixelcolor::BinaryColor>,
{
    let logo = storage.read_boot_logo();
    ui::boot::draw_logo(lcd, &logo);
}'''
    new_logo = '''fn show_boot_logo<D>(lcd: &mut D, storage: &mut Storage, settings: &flash_map::Settings)
where
    D: embedded_graphics::draw_target::DrawTarget<Color = embedded_graphics::pixelcolor::BinaryColor>,
{
    let logo = storage.read_boot_logo();
    if logo.iter().all(|&b| b == 0xff) || logo.iter().all(|&b| b == 0) {
        ui::boot::draw_flux_splash(lcd, settings);
    } else {
        ui::boot::draw_logo_with_text(lcd, &logo, settings);
    }
}'''
    # Keep the optional hook stable across every branding/size pass.
    hook = "\n        #[cfg(k6_designer)]\n        ui::designer_boot_logo(lcd,storage,settings.theme_selection);"
    text = text.replace("\n        #[cfg(k6_designer)]\n        ui::designer_boot_logo(lcd,storage);","").replace(hook, "").replace("\n        #[cfg(k6_designer)]\n        ui::designer_boot_logo(lcd,storage,settings.theme_selection);","")
    text = replace_once(text, old_logo, new_logo, "stored-logo renderer")
    return text.replace("        ui::boot::draw_flux_splash(lcd, settings);",
                        "        ui::boot::draw_flux_splash(lcd, settings);" + hook, 1)


def settings(text):
    return text.replace('SettingItem::Info => "VER",', 'SettingItem::Info => "ABOUT",')


def main_module(text):
    if 'mod flux_info;' not in text:
        text = replace_once(text, 'mod flash_map;\n', 'mod flash_map;\nmod flux_info;\n', 'metadata module declaration')
    return text


def keys(text):
    return text.replace("app.settings_ui.info_page ^= 1;", "app.settings_ui.info_page = (app.settings_ui.info_page + 1) % 3;")

def app_accessors(text):
    if "pub fn settings_about_page" in text:
        return text
    anchor = "    pub fn settings_index(&self) -> usize {\n"
    addition = '''    pub fn settings_about_page(&self) -> u8 { self.settings_ui.info_page }

    pub fn settings_is_about(&self) -> bool {
        self.settings_ui.group == Some(settings::SettingsGroup::System)
            && self.current_setting_item() == settings::SettingItem::Info
    }

'''
    return replace_once(text, anchor, addition + anchor, "About accessors")


def ui_settings(text):
    if "fn draw_flux_about" not in text:
        marker = "pub fn draw_settings<D>(lcd: &mut D, app: &app::App)\n"
        if marker not in text:
            marker = "pub fn draw_settings<D>(lcd: &mut D, app: &mut app::App)\n"
        fn = '''fn draw_flux_about<D>(lcd: &mut D, app: &app::App)
where D: DrawTarget<Color = BinaryColor>, {
    use embedded_graphics::mono_font::{ascii::FONT_6X10, MonoTextStyle};
    use embedded_graphics::primitives::{Line, PrimitiveStyle, Rectangle};
    use embedded_graphics::text::Text;
    use embedded_graphics::Drawable;
    Rectangle::new(Point::new(0, 0), Size::new(128, 64))
        .into_styled(PrimitiveStyle::with_fill(BinaryColor::Off)).draw(lcd).ok();
    Text::new("FLUX / ABOUT", Point::new(3, 10), MonoTextStyle::new(&FONT_6X10, BinaryColor::On)).draw(lcd).ok();
    Line::new(Point::new(0, 13), Point::new(127, 13)).into_styled(PrimitiveStyle::with_stroke(BinaryColor::On, 1)).draw(lcd).ok();
    let p = app.settings_about_page();
    let lines: [&str; 5] = match p {
        0 => ["BAOFENG K6", "FD6818B RADIO", "FLUX FIRMWARE", "AG7SU / BUILD", "LOCAL CATALOG"],
        1 => ["FEATURE SET", crate::flux_info::PACKS, "CATALOG MODULES", "SCAN: FM / EXPLORE", "UP/DN: NEXT PAGE"],
        _ => ["FLASH BUDGET", "APP LIMIT 120 KB", crate::flux_info::FLASH_USED, crate::flux_info::FLASH_FREE, "UP/DN: NEXT PAGE"],
    };
    for (i, line) in lines.iter().enumerate() {
        Text::new(line, Point::new(3, 24 + i as i32 * 9), MonoTextStyle::new(&FONT_6X10, BinaryColor::On)).draw(lcd).ok();
    }
}

'''
        text = replace_once(text, marker, fn + marker, "About renderer")
    return replace_once(text, '    let mut source = SettingsSource(app);\n',
                        '    if app.settings_is_about() { draw_flux_about(lcd, app); return; }\n    let mut source = SettingsSource(app);\n',
                        "About dispatch")


def settings_ops(text):
    if "crate::flux_info::FLASH_FREE_BYTES" in text and "crate::flux_info::PACKS" in text:
        return text
    old = """        SettingItem::Info => {
            let _ = if app.settings_ui.info_page == 0 {
                write!(w, "{}", FIRMWARE_VERSION)
            } else {
                write!(w, "UV-K6x")
            };
        }"""
    new = """        SettingItem::Info => {
            match app.settings_ui.info_page {
                0 => { let _ = write!(w, "FLUX {}", FIRMWARE_VERSION); }
                1 => { let _ = write!(w, "{}", crate::flux_info::PACKS); }
                _ => { let _ = write!(w, "{}KB FREE", crate::flux_info::FLASH_FREE_BYTES / 1024); }
            }
        }"""
    if old in text:
        return text.replace(old, new, 1)
    # Existing generated worktrees may have an earlier Flux variant from an interrupted build.
    arm = "        SettingItem::Info => {"
    next_arm = "\n\n        SettingItem::ChipId => {"
    if text.count(arm) == 1 and next_arm in text:
        a = text.index(arm)
        b = text.index(next_arm, a)
        return text[:a] + new + text[b:]
    raise SystemExit("Flux branding: could not patch System/About settings arm")


def info_module():
    capacity = 122880
    used = FLASH_USED
    free = max(0, capacity - used)
    packs = ["BASE", "CATALOG"]
    if SCAN: packs.append("SCAN/FM/EXPLORE")
    if FSK_RX: packs.append("DATA")
    if os.environ.get("K6_OTA_SERVICE","0") == "1": packs.append("OTA")
    if "sotto" in FEATURES: packs.append("SOTTO")
    (ROOT / "flux_info.rs").write_text(f'''// Generated from the linked image and its selected feature profile.
pub const FLASH_USED_BYTES: usize = {used};
pub const FLASH_FREE_BYTES: usize = {free};
pub const FLASH_USED: &str = "{used} B USED";
pub const FLASH_FREE: &str = "{free} B FREE";
pub const PACKS: &str = "{" | ".join(packs)}";
''')


edit("ui/boot.rs", boot)
edit("main.rs", startup)
edit("main.rs", main_module)
edit("app/settings.rs", settings)
edit("app/mod.rs", app_accessors)
edit("app/keys.rs", keys)
edit("app/settings_ops.rs", settings_ops)
edit("ui/settings.rs", ui_settings)
info_module()
