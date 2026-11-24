#!/usr/bin/env python3
"""Experimental API29 live PCM transport (Data pack, explicit opt-in)."""
from pathlib import Path
root=Path(__file__).parent
src=root/'build/aura-fd6818b-port/src'
def edit(p,old,new):
    s=p.read_text()
    if new in s:return
    if s.count(old)!=1:raise ValueError((str(p),old))
    p.write_text(s.replace(old,new,1))
p=src/'hal/mod.rs'
if 'pub mod pcm_capture;' not in p.read_text():
    p.write_text(p.read_text()+'\n#[cfg(k6_live_voice)]\npub mod pcm_capture;\n')
(src/'hal/pcm_capture.rs').write_text((root/'pcm-capture.rs').read_text())
p=src/'hal/txdog.rs'
if 'fn pcm_vector(' not in p.read_text():
    p.write_text(p.read_text()+'''
// Called with interrupts masked. Only borrow TIM17 while the existing
// watchdog mapping is already armed for TX; do not touch SYSCFG or TIM14.
#[cfg(k6_live_voice)]
pub fn pcm_vector(on:bool,handler:u32)->bool{
    let a=SRAM0+4*(16+22);
    if on{
        if !LIVE.load(Ordering::Relaxed)||ARMED.load(Ordering::Relaxed)&ARM_TX==0||
            rd(SYSCFG_CFGR1)&3!=3||rd(a)!=rd(BOOT_VEC+4*(16+22)){return false;}
        wr(a,handler);
        if rd(4*(16+22))!=handler{wr(a,rd(BOOT_VEC+4*(16+22)));return false;}
        true
    }else{wr(a,rd(BOOT_VEC+4*(16+22)));true}
}
''')
edit(p,'pub fn unkeyed() {\n    KEYED_AT.store','pub fn unkeyed() {\n    #[cfg(k6_live_voice)]\n    super::pcm_capture::stop();\n    KEYED_AT.store')
p=src/'app/pager.rs'
edit(p,'        pub fsk_async: unsafe extern "C" fn(u32,*const u16,u32) -> i32,',
    '        pub fsk_async: unsafe extern "C" fn(u32,*const u16,u32) -> i32,\n        pub mic_stream: unsafe extern "C" fn(u32,*mut u8,u32)->i32,')
edit(p,'    fsk_async: mod_fsk_async,','    fsk_async: mod_fsk_async,\n    mic_stream: mod_mic_stream,')
edit(p,'level: if cfg!(k6_async_fsk) { 28 }','level: if cfg!(k6_live_voice) { 29 } else if cfg!(k6_async_fsk) { 28 }')
edit(p,'matches!(*arena::base().add(12),27|28)','matches!(*arena::base().add(12),27|28|29)')
edit(p,'*arena::base().add(12)!=28','!matches!(*arena::base().add(12),28|29)')
# Authorize bounded reads only in API29 TX. Existing API27 and 28
# permission unchanged. Open still forbidden while keyed.
edit(p,'if !full_module_stack() || (*addr_of!(MOD_CTX)).tx || buf.is_null() {return -1;}',
    '''if !full_module_stack() || buf.is_null() {return -1;}
    if (*addr_of!(MOD_CTX)).tx && !(cfg!(k6_live_voice) && *arena::base().add(12)==29 && n<=128) {return -1;}''')
# A PA-off TX lease is mandatory for level29: no analog carrier blip on entry.
p2=src/'device/radio.rs'
edit(p2,'pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {',
    '''pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {
        self.enter_tx_with_pa(syst,true)
    }
    pub fn enter_tx_with_pa(&mut self, syst: &mut SYST, pa:bool) -> bool {''')
s=p2.read_text()
a=s.index('pub fn enter_tx_with_pa');b=s.index('pub fn enter_tx_keyed',a)
t=s[a:b].replace('self.fd6818.pa_enable(syst, self.cfg.power);','if pa {self.fd6818.pa_enable(syst, self.cfg.power);} else {self.fd6818.pa_off(syst);}')
p2.write_text(s[:a]+t+s[b:])
edit(p,'    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);',
    """    #[cfg(k6_live_voice)]
    if unsafe{*arena::base().add(12)==29} {
        let (app,_)=unsafe{mod_ctx()};
        let ga=unsafe{&*kd32f328_pac::Gpioa::ptr()};
        if unsafe{LIVE_ACTIVE.is_some()} || crate::board::read_ptt(ga) ||
            app.settings.tx_forbid || (app.settings.busy_lock && app.radio.rssi_open()) ||
            !BandLock::from_u8(app.settings.band_lock).tx_ranges().allows(hz) {return -1;}
    }
    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);""")
edit(p,'let ok = app.radio.enter_tx(syst);\n    app.radio.set_tx_allowed(BandLock::from_u8(app.settings.band_lock).tx_ranges());\n    if !ok {\n        rl!("K6 MOD tx refused hz={}", hz);',
    '''let ok = app.radio.enter_tx_with_pa(syst, !(cfg!(k6_live_voice) && unsafe{*arena::base().add(12)==29}));
    app.radio.set_tx_allowed(BandLock::from_u8(app.settings.band_lock).tx_ranges());
    if !ok {
        rl!("K6 MOD tx refused hz={}", hz);''')
# Abort and cleanup before watchdog remap. Restore mic-specific RF registers
# while PA off and lease still owned; stop timer even if lease already gone.
edit(p,'    unsafe { async_fsk_cleanup(); }','''    unsafe { async_fsk_cleanup(); }
    #[cfg(k6_live_voice)]
    unsafe { mic_stop(); }''')
body=r'''
// LIVE_VOICE_BEGIN
#[cfg(k6_live_voice)]
static mut MIC_ROUTE:bool=false;
#[cfg(k6_live_voice)]
static mut MIC_SAVED:[u16;4]=[0;4];
#[cfg(k6_live_voice)]
unsafe fn mic_stop(){
    crate::hal::pcm_capture::stop();
    if !MIC_ROUTE{return;}
    MIC_ROUTE=false;
    if !(*addr_of!(MOD_CTX)).tx{return;}
    // Don't call mod_tx_guard recursively from mod_tx_off().
    let (app,syst)=mod_ctx();
    app.radio.cw_carrier_off(syst);
    for (i,r) in [0x30u8,0x47,0x48,0x7d].iter().enumerate(){
        app.radio.fd6818.reg_write(syst,*r,MIC_SAVED[i]);
    }
}
#[cfg(k6_live_voice)]
unsafe extern "C" fn mod_mic_stream(op:u32,ptr:*mut u8,n:u32)->i32{
    if !full_module_stack() || *arena::base().add(12)!=29{return -1;}
    if op==3{
        if LIVE_ACTIVE.is_some() || mod_power_off_requested() || mod_cancel_poll()==1{return -1;}
        let ga=&*kd32f328_pac::Gpioa::ptr();
        return (!crate::board::read_ptt(ga)) as i32;
    }
    mod_tx_guard();
    if op==1 {crate::hal::pcm_capture::stop();return 0;}
    if op==2{return crate::hal::pcm_capture::peak() as i32;}
    if op!=0||MIC_ROUTE||!(*addr_of!(MOD_CTX)).tx||n!=644||
        ptr.is_null()||ptr as usize&3!=0||
        (ptr as usize)<(*addr_of!(MOD_CTX)).scratch||
        (ptr as usize).saturating_add(n as usize)>scratch_end()||mod_fd_read(0)!=0x4829
        ||mod_tx_pa(0)!=0{return -1;}
    for (i,r) in [0x30u8,0x47,0x48,0x7d].iter().enumerate(){MIC_SAVED[i]=mod_fd_read(*r);}
    MIC_ROUTE=true;
    let (app,syst)=mod_ctx();
    let fd=&mut app.radio.fd6818;
    fd.reg_write(syst,0x47,(MIC_SAVED[1]&!0x0f40)|0x0400);
    fd.reg_write(syst,0x48,0x82df);
    fd.reg_write(syst,0x7d,(MIC_SAVED[3]&!0x001f)|31);
    fd.reg_write(syst,0x30,MIC_SAVED[0]|0x0204);
    if fd.reg_read(syst,0x30)!=(MIC_SAVED[0]|0x0204)||!crate::hal::pcm_capture::start(ptr){
        mic_stop();return -2;
    }
    0
}
#[cfg(not(k6_live_voice))]
unsafe extern "C" fn mod_mic_stream(_:u32,_:*mut u8,_:u32)->i32{-1}
// LIVE_VOICE_END
'''
s=p.read_text()
if '// LIVE_VOICE_BEGIN' in s:
    a=s.index('// LIVE_VOICE_BEGIN');b=s.index('// LIVE_VOICE_END',a)+len('// LIVE_VOICE_END')
    s=s[:a]+body.strip()+s[b:]
else:s+=body
p.write_text(s)
