// SOTTO_API30_MIC_BEGIN
// Experimental PA11 sigma-delta capture at 8ksps via TIM17 interrupt.
// This is firmware *service plumbing*, NOT qualified 20ms on-hardware audio.
#[cfg(k6_sotto_mic)]
static mut SOTTO_MIC_ROUTE: bool=false;
#[cfg(k6_sotto_mic)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut SOTTO_GP0_ROUTE: bool=false;
#[cfg(k6_sotto_mic)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut SOTTO_GP0_SAVED:[u16;2]=[0;2];
#[cfg(k6_sotto_mic)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut SOTTO_MIC_SAVED:[u16;4]=[0;4];
#[cfg(k6_sotto_mic)]
const SOTTO_MIC_REGS:[u8;4]=[0x30,0x47,0x48,0x7d];

// Called by explicit stop and all TX ownership exits; avoid tx guard
// recursion while the transmitter's watchdog is dekeying the radio.
#[cfg(k6_sotto_mic)]
unsafe fn sotto_mic_stop(){
    crate::hal::gp0_capture::stop();
    crate::hal::pcm_capture::stop();
    if SOTTO_GP0_ROUTE {
        SOTTO_GP0_ROUTE=false;
        let c=&*addr_of!(MOD_CTX);
        if !c.app.is_null() && c.tx {
            let (app,syst)=mod_ctx();
            app.radio.fd6818.reg_write(syst,0x34,SOTTO_GP0_SAVED[1]);
            app.radio.fd6818.reg_write(syst,0x33,SOTTO_GP0_SAVED[0]);
        }
    }
    if !SOTTO_MIC_ROUTE{return;}
    SOTTO_MIC_ROUTE=false;
    let c=&*addr_of!(MOD_CTX);
    if c.app.is_null()||!c.tx{return;}
    let (app,syst)=mod_ctx();
    app.radio.cw_carrier_off(syst);
    for i in 0..4 {app.radio.fd6818.reg_write(syst,SOTTO_MIC_REGS[i],SOTTO_MIC_SAVED[i]);}
}
#[cfg(k6_sotto_mic)]
unsafe extern "C" fn mod_sotto_mic_stream(op:u32,ptr:*mut u8,n:u32)->i32{
    let c=&*addr_of!(MOD_CTX);
    if c.app.is_null() || *arena::base().add(12)<30{return -1;}
    // Op3 is a read-only physical PTT test. The headset's microphone
    // and the external UART share a cable, so NEVER synthesize PTT here.
    if op==3{
        #[cfg(all(k6_live_serial,feature="spilib"))]
        if LIVE_ACTIVE.is_some(){return -1;} // plugged-in live UART owns port
        if mod_power_off_requested() || mod_cancel_poll()==1{return -1;}
        let ga=&*kd32f328_pac::Gpioa::ptr();
        return (!crate::board::read_ptt(ga)) as i32;
    }
    mod_tx_guard();
    if op==1{sotto_mic_stop();return 0;}
    if op==2{return crate::hal::pcm_capture::peak() as i32;}
    // Op8 is unchanged. An explicit, opt-in op9 captures 4096 exact
    // pre-decimator popcounts, writing after the normal ring into the
    // caller's checked arena. Op9 is never enabled in ordinary firmware.
    // No raw GPIO, SPI contents, or key material ever leave the device.
    if op==8 || (cfg!(k6_sotto_gp0_popcount_air) && op==9) {
        let popcount_mode=op==9;
        #[cfg(all(k6_live_serial,feature="spilib"))]
        if LIVE_ACTIVE.is_some(){return -1;}
        let start=ptr as usize;
        if !SOTTO_MIC_ROUTE || SOTTO_GP0_ROUTE || !c.tx ||
           (!popcount_mode && n!=87260) || (popcount_mode && n!=4096)
            || c.stack_from_arena || c.tlm_cap!=0
            || ptr.is_null() || start&3!=0 || start<c.scratch
            || start.saturating_add(644+(if popcount_mode{4096}else{0}))>scratch_end()
            || mod_fd_read(0)!=0x4829 || mod_tx_pa(0)!=0
            || !crate::hal::txdog::ready() {return -1;}
        SOTTO_GP0_SAVED=[mod_fd_read(0x33),mod_fd_read(0x34)];
        let (app,syst)=mod_ctx();
        app.radio.fd6818.reg_write(syst,0x33,SOTTO_GP0_SAVED[0]&!0x0100);
        app.radio.fd6818.reg_write(syst,0x34,(SOTTO_GP0_SAVED[1]&!0x000f)|12);
        if mod_fd_read(0x34)&0x000f!=12 {
            app.radio.fd6818.reg_write(syst,0x34,SOTTO_GP0_SAVED[1]);
            app.radio.fd6818.reg_write(syst,0x33,SOTTO_GP0_SAVED[0]);
            return -3;
        }
        if !crate::hal::gp0_capture::start(ptr,if popcount_mode{87261}else{n}) {
            app.radio.fd6818.reg_write(syst,0x34,SOTTO_GP0_SAVED[1]);
            app.radio.fd6818.reg_write(syst,0x33,SOTTO_GP0_SAVED[0]);
            return -2;
        }
        SOTTO_GP0_ROUTE=true;
        return 0;
    }
    // Cumulative raw 1-bit sampler counters. Read-only while the microphone
    // lease is live; stop/reset uses the existing audited cleanup path.
    if op==4{return if SOTTO_MIC_ROUTE{crate::hal::pcm_capture::raw_ones() as i32}else{-1};}
    if op==5{return if SOTTO_MIC_ROUTE{crate::hal::pcm_capture::raw_edges() as i32}else{-1};}
    if op==6{return if SOTTO_MIC_ROUTE{crate::hal::pcm_capture::raw_variation() as i32}else{-1};}
    #[cfg(all(k6_live_serial,feature="spilib"))]
    if LIVE_ACTIVE.is_some(){return -1;}
    // Op7 grants only the RFIC microphone route, not the PA11 sampler.
    // This is required when the module needs TIM17 + DMA1 for a GP0/PB1
    // experiment. Stop/abort remains the standard op1/tx_off cleanup.
    let route_only=op==7;
    if (op!=0 && !route_only) || SOTTO_MIC_ROUTE || !c.tx
        || (!route_only && (n!=644 || ptr.is_null() || (ptr as usize)&3!=0
        || (ptr as usize)<c.scratch
        || (ptr as usize).saturating_add(n as usize)>scratch_end()))
        || mod_fd_read(0)!=0x4829 || mod_tx_pa(0)!=0
        || !crate::hal::txdog::ready() {
        return -1;
    }
    for i in 0..4{SOTTO_MIC_SAVED[i]=mod_fd_read(SOTTO_MIC_REGS[i]);}
    SOTTO_MIC_ROUTE=true;
    let (app,syst)=mod_ctx();
    let fd=&mut app.radio.fd6818;
    fd.reg_write(syst,0x47,(SOTTO_MIC_SAVED[1]&!0x0f40)|0x0400);
    fd.reg_write(syst,0x48,0x82df);
    fd.reg_write(syst,0x7d,(SOTTO_MIC_SAVED[3]&!0x001f)|31);
    fd.reg_write(syst,0x30,SOTTO_MIC_SAVED[0]|0x0204);
    if fd.reg_read(syst,0x30)!=(SOTTO_MIC_SAVED[0]|0x0204) {
        sotto_mic_stop();return -3; // RF route did not stick
    }
    if !route_only && !crate::hal::pcm_capture::start(ptr) {
        sotto_mic_stop();return -2; // TIM17/vector or GPIO resource unavailable
    }
    0
}
// SOTTO_API30_MIC_END
