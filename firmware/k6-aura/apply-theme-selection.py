#!/usr/bin/env python3
"""Designer selection; no arbitrary SPI writes. Preserve the 212-byte settings ABI."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
def patch(file,old,new):
    p=root/file;s=p.read_text()
    if (new and new in s) or (not new and old not in s):return
    if s.count(old)!=1:raise SystemExit(f'{file}: missing theme selection anchor {old!r}')
    p.write_text(s.replace(old,new,1))
# Settings byte 161 already uses the low two bits for action beep.
# Upper six bits: 0 legacy/auto (bench only), 1 factory, 2..33 library slots 0..31.
# Preserve this field on every firmware variant; never alter other settings bytes.
patch('flash_map.rs','    pub action_beep: bool,','    pub action_beep: bool,\n    pub theme_selection: u8,')
patch('flash_map.rs','        action_beep: true,','        action_beep: true,\n        theme_selection: 0,')
patch('flash_map.rs','            action_beep: buf[161] != 1,','            action_beep: buf[161] & 3 != 1,\n            theme_selection: if buf[161] == 255 {0} else {(buf[161] >> 2).min(33)},')
patch('flash_map.rs','        buf[161] = if self.action_beep { 2 } else { 1 };','        buf[161] = (self.theme_selection.min(33)<<2) | if self.action_beep {2} else {1};')
patch('app/settings.rs','    Contrast,','    Contrast,\n    #[cfg(k6_designer)]\n    Theme,')
patch('app/settings.rs','&[SettingItem::Abr, SettingItem::AutoLk, SettingItem::Contrast]','&[SettingItem::Abr, SettingItem::AutoLk, SettingItem::Contrast,\n                  #[cfg(k6_designer)] SettingItem::Theme]')
patch('app/settings.rs','            SettingItem::Contrast => "CONTR",','            SettingItem::Contrast => "CONTR",\n            #[cfg(k6_designer)] SettingItem::Theme => "THEME",')
patch('app/settings_ops.rs','        SettingItem::Contrast => app.settings.contrast as i32,','        SettingItem::Contrast => app.settings.contrast as i32,\n        #[cfg(k6_designer)] SettingItem::Theme => app.settings.theme_selection as i32,')
patch('app/mod.rs','pub fn settings_value_at(&self, index: usize, w: &mut dyn core::fmt::Write) -> bool {','pub fn settings_value_at(&mut self, index: usize, w: &mut dyn core::fmt::Write) -> bool {')
patch('app/settings_ops.rs','pub fn value_text_for(app: &App, index: usize, item: SettingItem, w: &mut dyn Write) {','pub fn value_text_for(app: &mut App, index: usize, item: SettingItem, w: &mut dyn Write) {')
patch('app/settings_ops.rs','''        #[cfg(k6_designer)]
        SettingItem::Theme => {
            let mut next=cur;
            for _ in 0..34 {
                next=wrap_step(next,up,1,33);
                if next==1 {break;}
                let slot=(next-2) as u32;
                let mut magic=[0u8;4];
                if app.designer_read(0x110000+slot*0x4000,&mut magic) && magic==*b"K6T\\x06"{break;}
            }
            next
        },
''','')
patch('ui/settings.rs',"struct SettingsSource<'a>(&'a app::App);","struct SettingsSource<'a>(&'a mut app::App);")
patch('ui/settings.rs',"pub fn draw_settings<D>(lcd: &mut D, app: &app::App)","pub fn draw_settings<D>(lcd: &mut D, app: &mut app::App)")
patch('ui/settings.rs','    let mut source = SettingsSource(app);','    let title = app.settings_title();\n    let index = app.settings_index();\n    let arrows = app.settings_show_arrows();\n    let mut source = SettingsSource(app);')
patch('ui/settings.rs','        app.settings_title(),','        title,')
patch('ui/settings.rs','        app.settings_index(),','        index,')
patch('ui/settings.rs','        app.settings_show_arrows(),','        arrows,')
patch('ui/mod.rs','app::Mode::Settings => draw_settings(display.as_draw_target(), &*app),','app::Mode::Settings => draw_settings(display.as_draw_target(), &mut *app),')
# Normalize all previous versions of the generated Theme adjustment arm.
# Later packed-store patches rewrite designer_read to designer_slot_read.
import re
p=root/'app/settings_ops.rs';s=p.read_text()
s=re.sub(r'        #\[cfg\(k6_designer\)\]\n        SettingItem::Theme => \{\n            let mut next=cur;.*?\n        \},\n','',s,flags=re.S)
p.write_text(s)
patch('app/settings_ops.rs','        SettingItem::Contrast => clamp_step(cur, up, 0, 4),','''        SettingItem::Contrast => clamp_step(cur, up, 0, 4),
        #[cfg(k6_designer)]
        SettingItem::Theme => {
            let mut next=cur;
            for _ in 0..34 {
                next=wrap_step(next,up,1,33);
                if next==1 {break;}
                let slot=(next-2) as u32;
                if crate::ui::theme_reader::open(&mut |off,dst|{
                    app.designer_read(0x110000+slot*0x4000+u32::from(off),dst)
                }).is_some(){break;}
            }
            next
        },''')
patch('app/settings_ops.rs','        SettingItem::ActionBeep => app.settings.action_beep = v != 0,','        SettingItem::ActionBeep => app.settings.action_beep = v != 0,\n        #[cfg(k6_designer)] SettingItem::Theme => app.settings.theme_selection = v.clamp(0,33) as u8,')
patch('app/settings_ops.rs','''        #[cfg(k6_designer)]
        SettingItem::Theme => {
            let v=app.settings.theme_selection;
            if v<=1 {let _=write!(w,"FACTORY");}
            else {let _=write!(w,"SLOT {}",v-2);}
        }
''','')
patch('app/settings_ops.rs','        SettingItem::ChipId => {','''        #[cfg(k6_designer)]
        SettingItem::Theme => {
            let v=app.settings.theme_selection;
            if v<=1 {let _=write!(w,"FACTORY");}
            else {
                let slot=u32::from(v-2);
                let base=0x110000+slot*0x4000;
                if let Some(theme)=crate::ui::theme_reader::open(&mut |off,dst|{
                    app.designer_read(base+u32::from(off),dst)
                }) {
                    let mut name=[0u8;14];
                    if theme.name_len>0 && app.designer_read(base+u32::from(theme.name_offset),&mut name[..usize::from(theme.name_len)]) {
                        if let Ok(label)=core::str::from_utf8(&name[..usize::from(theme.name_len)]) {
                            let _=w.write_str(label);
                        } else {let _=write!(w,"SLOT {}",v-2);}
                    } else {let _=write!(w,"SLOT {}",v-2);}
                } else {let _=write!(w,"FACTORY");}
            }
        }
        SettingItem::ChipId => {''')
