// SOTTO_API30_MIC_BEGIN
// Experimental PA11 sigma-delta capture at 8ksps via TIM17 interrupt.
// This is firmware *service plumbing*, NOT qualified 20ms on-hardware audio.
#[cfg(k6_sotto_mic)]
static mut SOTTO_MIC_ROUTE: bool=false;
#[cfg(k6_sotto_mic)]
static mut SOTTO_MIC_SAVED:[u16;4]=[0;4];
#[cfg(k6_sotto_mic)]
const SOTTO_MIC_REGS:[u8;4]=[0x30,0x47,0x48,0x7d];

// Called by explicit stop and all TX ownership exits; avoid tx guard
// recursion while the transmitter's watchdog is dekeying the radio.
#[cfg(k6_sotto_mic)]
unsafe fn sotto_mic_stop(){
    crate::hal::pcm_capture::stop();
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
    #[cfg(all(k6_live_serial,feature="spilib"))]
    if LIVE_ACTIVE.is_some(){return -1;}
    if op!=0 || SOTTO_MIC_ROUTE || !c.tx
        || n!=644 || ptr.is_null() || (ptr as usize)&3!=0
        || (ptr as usize)<c.scratch
        || (ptr as usize).saturating_add(n as usize)>scratch_end()
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
    if !crate::hal::pcm_capture::start(ptr) {
        sotto_mic_stop();return -2; // TIM17/vector or GPIO resource unavailable
    }
    0
}
// SOTTO_API30_MIC_END
