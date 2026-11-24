#!/usr/bin/env python3
"""Opt-in selectable SOTTO TX mode, physical PTT queues installed API29 module."""
from pathlib import Path
src=Path(__file__).parent/'build/aura-fd6818b-port/src'
gate='#[cfg(k6_sotto_vfo)]'
def edit(file,old,new):
 p=src/file;s=p.read_text()
 if new in s:return
 if s.count(old)!=1:raise RuntimeError(f"{file}: expected one anchor {old!r}, found {s.count(old)}")
 p.write_text(s.replace(old,new,1))
edit('app/mod.rs','    sides: [side::Side; 2],',
    '    sides: [side::Side; 2],\n    '+gate+'\n    sotto_selected: [bool; 2],')
edit('app/mod.rs','            sides,\n            settings,',
    '            sides,\n            '+gate+'\n            sotto_selected: [false; 2],\n            settings,')
edit('app/mod.rs','    pub fn side_modulation(&self, index: usize) -> Modulation {',
    '''    '''+gate+'''
    pub fn sotto_selected(&self, index:usize)->bool {
        index<2 && self.sotto_selected[index] && !self.side_is_channel_mode(index)
    }
    '''+gate+'''
    pub fn sotto_mode_ptt_press(&mut self)->bool{
        if self.mode!=Mode::Standby || !self.sotto_selected(self.master) ||
            self.transmitting || self.settings.tx_forbid ||
            (self.settings.busy_lock && self.radio.rssi_open()) {return false;}
        pager::sotto_queue_ptt(self)
    }
    pub fn side_modulation(&self, index: usize) -> Modulation {''')
edit('app/side_ops.rs','    let s = &mut app.sides[app.master];\n    s.cfg.modulation = match s.cfg.modulation {',
    '''    '''+gate+'''
    {
        let side=app.master;
        if app.sotto_selected[side]{
            app.sotto_selected[side]=false;
            app.sides[side].cfg.modulation=Modulation::Fm;
            sync_watching_to_master(app,syst);
            return;
        }
        if !app.side_is_channel_mode(side) &&
            app.sides[side].cfg.modulation==Modulation::Cwf &&
            super::pager::sotto_module_slot(app).is_some() {
            app.sotto_selected[side]=true;
            app.sides[side].cfg.modulation=Modulation::Fm;
            sync_watching_to_master(app,syst);
            return;
        }
    }
    let s = &mut app.sides[app.master];
    s.cfg.modulation = match s.cfg.modulation {''')
edit('app/tx.rs','pub(super) fn set_ptt(app: &mut App, syst: &mut SYST, pressed: bool) {',
    'pub(super) fn set_ptt(app: &mut App, syst: &mut SYST, pressed: bool) {\n    '+gate+'\n    if app.sotto_selected(app.master_index()) {return;}')
edit('ui/standby.rs','    let is_cw_master = i == app.master_index()',
    '    '+gate+'\n    let modulation = if app.sotto_selected(i) { "SOTTO" } else { modulation };\n    let is_cw_master = i == app.master_index()')
edit('main.rs','                if !remote_was_active || !level { app.set_ptt(&mut cp.SYST, !level); }',
    '''                '''+gate+'''
                let sotto_selected = app.sotto_selected(app.master_index());
                #[cfg(not(k6_sotto_vfo))]
                let sotto_selected = false;
                #[cfg(k6_sotto_vfo)]
                if sotto_selected && !level && !remote_was_active {
                    app.sotto_mode_ptt_press();
                }
                if !sotto_selected && (!remote_was_active || !level) {
                    app.set_ptt(&mut cp.SYST, !level);
                }''')
p=src/'app/pager.rs';s=p.read_text()
anchor='// KUL2 logical lookup\n#[cfg(feature="spilib")]\npub(crate) fn library_location(nor:'
impl='''// SOTTO_VFO_BEGIN
// Preflight only; the existing verified kl_load path performs the complete
// envelope authentication before allowing a module to execute.
#[cfg(k6_sotto_vfo)]
pub(super) fn sotto_module_slot(app:&mut App)->Option<u8>{
    for slot in (16u32..32).chain(0..9) {
        let Some((addr,extent))=library_location(&mut app.storage.norflash,slot) else{continue;};
        if extent<56||extent>10240{continue;}
        let mut h=[0u8;32];
        if !app.storage.norflash.read_bytes_checked(addr,&mut h){continue;}
        let code=u16::from_le_bytes([h[4],h[5]]) as u32;
        if &h[0..4]==b"K6M\\x01" && code+24==extent &&
           h[12]==29 && h[13]==1 && h[15]==slot as u8 &&
           &h[16..20]==b"K6NM" && &h[20..28]==b"SOTTO TX" {
            return Some(slot as u8);
        }
    }
    None
}
#[cfg(k6_sotto_vfo)]
pub(super) fn sotto_queue_ptt(app:&mut App)->bool{
    if unsafe{PENDING_LIB_SLOT}!=255 || app.mode!=Mode::Standby ||
       app.transmitting||app.pager.play_pending||stream_on(){return false;}
    let Some(slot)=sotto_module_slot(app) else {return false;};
    app.watching=app.master;
    queue_lib_run(slot);
    true
}
// SOTTO_VFO_END

'''
if '// SOTTO_VFO_BEGIN' not in s:
 if s.count(anchor)!=1:raise RuntimeError('pager anchor not unique')
 p.write_text(s.replace(anchor,impl+anchor,1))
p=src.parent/'build.rs';s=p.read_text()
anchor='fn main() {'
ins='fn main() {\n    println!("cargo:rustc-check-cfg=cfg(k6_sotto_vfo)");'
if 'cargo:rustc-check-cfg=cfg(k6_sotto_vfo)' not in s:
 assert s.count(anchor)==1
 p.write_text(s.replace(anchor,ins,1))
