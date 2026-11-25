// SOTTO_API29_ASYNC_FSK_BEGIN
// API29: A *single* bounded 33-word B-chip native FSK burst.
// Opt-in; PA is muted until FIFO preload and validation succeed.
// The caller owns the finite TX lease from tx_on and must tx_off.
// No background ISR, FIFO refill, module-owned RF addresses or heap.
#[cfg(k6_sotto_async_fsk)]
static mut SOTTO_TX29_ACTIVE: bool = false;
#[cfg(k6_sotto_async_fsk)]
static mut SOTTO_TX29_SNAPSHOT: bool = false;
#[cfg(k6_sotto_async_fsk)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut SOTTO_TX29_SAVED: [u16;9] = [0;9];
#[cfg(k6_sotto_async_fsk)]
static mut SOTTO_TX29_UNTIL: u32 = 0;
#[cfg(k6_sotto_async_fsk)]
const SOTTO_TX29_REGS:[u8;9] = [0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d];
#[cfg(k6_sotto_async_fsk)]
const SOTTO_TX29_VALUES:[u16;9] = [0x8000,0x00f8,0x00e1,0x0095,0xc194,0x076b,0x3e36,0x5625,0x4100];

// Must NOT call mod_tx_guard from the cleanup path: tx_guard may call
// mod_tx_off, which itself invokes this cleanup on any timeout/return.
#[cfg(k6_sotto_async_fsk)]
unsafe fn sotto_tx29_cleanup() {
    SOTTO_TX29_ACTIVE=false;
    if !SOTTO_TX29_SNAPSHOT {return;}
    SOTTO_TX29_SNAPSHOT=false;
    let c=&*addr_of!(MOD_CTX);
    if c.app.is_null() || !c.tx {return;}
    let (app,syst)=mod_ctx();
    app.radio.cw_carrier_off(syst); // PA off *before* altering TX FSK mode
    let fd=&mut app.radio.fd6818;
    for (r,v) in [(0x59,0x00f8),(0x3f,0),(0x02,0)] {fd.reg_write(syst,r,v);}
    for i in (0..9).rev() {fd.reg_write(syst,SOTTO_TX29_REGS[i],SOTTO_TX29_SAVED[i]);}
}

#[cfg(k6_sotto_async_fsk)]
unsafe fn sotto_tx29_abort()->i32 { mod_tx_off(); -2 }

#[cfg(k6_sotto_async_fsk)]
unsafe fn sotto_tx29_valid()->bool {
    mod_tx_guard();
    let c=&*addr_of!(MOD_CTX);
    if c.app.is_null() || !c.tx || mod_power_off_requested()
       || mod_cancel_poll()==1 || mod_key_down()!=0 {return false;}
    true
}

#[cfg(k6_sotto_async_fsk)]
unsafe fn sotto_tx29_write(reg:u8,value:u16)->bool {
    if !sotto_tx29_valid() {return false;}
    let (app,syst)=mod_ctx();
    app.radio.fd6818.reg_write(syst,reg,value);
    true
}

#[cfg(k6_sotto_async_fsk)]
unsafe extern "C" fn mod_sotto_async_fsk(op:u32,words:*const u16,n:u32)->i32 {
    if op==2 {mod_tx_off();return 0;} // abort always releases TX lease
    if op>2 {return -1;}
    if !sotto_tx29_valid() {return sotto_tx29_abort();}
    if op==0 {
        if SOTTO_TX29_ACTIVE || SOTTO_TX29_SNAPSHOT || n!=33
           || words.is_null() || (words as usize)&1!=0
           || (words as usize)<(*addr_of!(MOD_CTX)).scratch
           || (words as usize).saturating_add(66)>scratch_end()
           || mod_fd_read(0)!=0x4829 {
            return sotto_tx29_abort();
        }
        // Exactly the tested 66-byte native FSK frame with a guard word.
        if words.add(32).read()!=0xa55a {return sotto_tx29_abort();}
        let (app,syst)=mod_ctx();
        let fd=&mut app.radio.fd6818;
        for i in 0..9 {SOTTO_TX29_SAVED[i]=fd.reg_read(syst,SOTTO_TX29_REGS[i]);}
        if SOTTO_TX29_SAVED[0]&0xa000!=0 || SOTTO_TX29_SAVED[1]&0x0800!=0 {
            return sotto_tx29_abort(); // another owner is active
        }
        SOTTO_TX29_SNAPSHOT=true;
        if !sotto_tx29_write(0x59,0x00f8)||!sotto_tx29_write(0x02,0) {return sotto_tx29_abort();}
        for i in 0..9 {
            if !sotto_tx29_write(SOTTO_TX29_REGS[i],SOTTO_TX29_VALUES[i]) {
                return sotto_tx29_abort();
            }
        }
        if !sotto_tx29_write(0x59,0x80f8)||!sotto_tx29_write(0x59,0x00f8) {
            return sotto_tx29_abort();
        }
        for i in 0..33 {
            if !sotto_tx29_write(0x5f,words.add(i).read()) {return sotto_tx29_abort();}
        }
        if !sotto_tx29_valid() || mod_fd_read(0x0c)&1!=0 {
            return sotto_tx29_abort();
        }
        // Raising PA is the *last* step before enabling the FIFO transmitter.
        if mod_tx_pa(1)!=0 || !sotto_tx29_write(0x59,0x08f8) {
            return sotto_tx29_abort();
        }
        SOTTO_TX29_UNTIL=mod_tick().wrapping_add(7000); // <=700ms burst, <=lease
        SOTTO_TX29_ACTIVE=true;
        return 0;
    }
    if !SOTTO_TX29_ACTIVE || n!=0 || !words.is_null() {
        return sotto_tx29_abort();
    }
    if mod_tick().wrapping_sub(SOTTO_TX29_UNTIL)<0x8000_0000
       || mod_fd_read(0x30)&2==0 {return sotto_tx29_abort();}
    if mod_fd_read(0x0c)&1==0 {return 0;}
    // Only an observed RFIC TX-done IRQ can complete a frame.
    let (app,syst)=mod_ctx();
    app.radio.cw_carrier_off(syst);
    sotto_tx29_cleanup();
    mod_tx_guard();
    if !(*addr_of!(MOD_CTX)).tx {return -2;}
    1
}
// SOTTO_API29_ASYNC_FSK_END
