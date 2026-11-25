// SOTTO_4800_RX_BEGIN
// Experimental, guarded 4800-baud 33-word native B-chip RX. Independent
// native FDV1 CRC, session reassembly and playout remain module responsibilities.
// No RF TX, PA registers, timer vectors, shared IRQ handler or heap allocation.
// SPDX-License-Identifier: MIT

#[cfg(k6_fsk4800_rx)]
const RX4800_REGS: [u8; 9] = [0x3f,0x59,0x58,0x72,0x5a,0x5b,0x5c,0x5d,0x5e];
#[cfg(k6_fsk4800_rx)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RX4800_SAVED: [u16; 9] = [0;9];
#[cfg(k6_fsk4800_rx)]
static mut RX4800_ACTIVE: bool = false;
#[cfg(k6_fsk4800_rx)]
static mut RX4800_FRAME_READY: bool = false;
#[cfg(k6_fsk4800_rx)]
static mut RX4800_GOT: u8 = 0;
#[cfg(k6_fsk4800_rx)]
static mut RX4800_THRESHOLD: u8 = 4;
#[cfg(k6_fsk4800_rx)]
static mut RX4800_DEADLINE: u32 = 0;

#[cfg(k6_fsk4800_rx)]
unsafe fn rx4800_stop() {
    // Must run before tx_on changes radio mode and whenever the module
    // returns. Never restore previous RX snapshots after TX has begun.
    if !RX4800_ACTIVE {return;}
    RX4800_ACTIVE = false;
    RX4800_FRAME_READY = false;
    RX4800_GOT = 0;
    let ctx = &*addr_of!(MOD_CTX);
    if ctx.app.is_null() || ctx.tx {return;}
    let (app,syst) = mod_ctx();
    let fd = &mut app.radio.fd6818;
    for (r,v) in [(0x59,0x00f8),(0x3f,0),(0x02,0)] {
        fd.reg_write(syst,r,v);
    }
    for i in (0..9).rev() {
        fd.reg_write(syst,RX4800_REGS[i],RX4800_SAVED[i]);
    }
}

#[cfg(k6_fsk4800_rx)]
unsafe fn rx4800_fail()->i32 {
    rx4800_stop();
    -2
}

#[cfg(k6_fsk4800_rx)]
unsafe fn rx4800_allowed()->bool {
    let c = &*addr_of!(MOD_CTX);
    if c.app.is_null() || c.tx || mod_cancel_poll()==1
       || mod_power_off_requested() || mod_fd_read(0)!=0x4829
       || mod_key_down()!=0 {
        return false;
    }
    // PTT preempts RX; this is separate from the scanned keypad.
    let ga = &*kd32f328_pac::Gpioa::ptr();
    crate::board::read_ptt(ga)
}

// No "autoconfigure 4800" through level-24 fsk_configured. This primitive
// owns its radio-register snapshot until one 66-byte frame completes or
// caller stops/aborts; the receiver must explicitly re-arm every packet.
#[cfg(k6_fsk4800_rx)]
unsafe extern "C" fn mod_fsk4800_rx(op:u32,words:*mut u16,cfg:u32)->i32 {
    mod_tx_guard();
    if op==2 {
        rx4800_stop();
        return 0;
    }
    if op>2 {return -1;}
    if !rx4800_allowed() {return rx4800_fail();}
    if op==0 {
        if cfg>7 {return -1;}
        let threshold = if cfg==0 {4} else {cfg as u8};
        if RX4800_ACTIVE && RX4800_FRAME_READY && RX4800_THRESHOLD==threshold {
            // One half-duplex FDV1 epoch has closely spaced bursts. Retain
            // the original register snapshot between frames; a complete
            // restore/reprogram (~2ms simulated) misses the short tail gap.
            // No active TX/PA, new GPIO ownership, or timer is introduced.
            let (app,syst) = mod_ctx();
            let fd = &mut app.radio.fd6818;
            fd.reg_write(syst,0x59,0x40f8); // reset demod / FIFO
            fd.reg_write(syst,0x02,0);     // clear stale IRQ latch
            fd.reg_write(syst,0x59,0x10f8); // RX re-arm
            RX4800_FRAME_READY=false;
            RX4800_GOT=0;
            RX4800_DEADLINE=mod_tick().wrapping_add(50_000);
            return 0;
        }
        if RX4800_ACTIVE {rx4800_stop();}
        let (app,syst) = mod_ctx();
        let fd = &mut app.radio.fd6818;
        // The exact receive register profile from the compiled
        // tests/data/k6/fsk_rx_emu.c polling fixture.
        for i in 0..9 {
            RX4800_SAVED[i]=fd.reg_read(syst,RX4800_REGS[i]);
        }
        if RX4800_SAVED[0]&0xa000!=0 || RX4800_SAVED[1]&0x0800!=0 {
            return -1; // never reset another active FSK/TX owner
        }
        RX4800_THRESHOLD=threshold;
        RX4800_GOT=0;
        RX4800_FRAME_READY=false;
        RX4800_ACTIVE=true; // cleanup can restore snapshot on any failure
        for (r,v) in [(0x59,0x00f8),(0x02,0),(0x3f,0x3002),
                       (0x58,0x00e1),(0x72,0xc194),(0x5a,0x076b),
                       (0x5b,0x3e36),(0x5c,0x5625),(0x5d,0x4100),
                       (0x5e,0x0200 | threshold as u16),
                       (0x59,0x40f8),(0x59,0x10f8)] {
            fd.reg_write(syst,r,v);
        }
        // 5 s acquisition bound; caller can repeat op=0 for idle listen.
        RX4800_DEADLINE=mod_tick().wrapping_add(50_000);
        return 0;
    }
    if RX4800_FRAME_READY {return -3;} // caller must re-arm or stop
    if cfg!=0 || !RX4800_ACTIVE || words.is_null()
       || (words as usize)&1 != 0
       || (words as usize) < (*addr_of!(MOD_CTX)).scratch
       || (words as usize).saturating_add(66) > scratch_end() {
        return rx4800_fail();
    }
    if mod_tick().wrapping_sub(RX4800_DEADLINE)<0x8000_0000 {
        return rx4800_fail();
    }
    let (app,syst) = mod_ctx();
    let fd = &mut app.radio.fd6818;
    if fd.reg_read(syst,0x0c)&1 == 0 {return 0;}
    // REG_02 is a latched event register; read it AFTER clearing the IRQ.
    fd.reg_write(syst,0x02,0);
    let flags=fd.reg_read(syst,0x02);
    if flags&0x1000 != 0 {
        if RX4800_GOT as u32 + RX4800_THRESHOLD as u32 > 33 {
            return rx4800_fail();
        }
        for _ in 0..RX4800_THRESHOLD {
            words.add(RX4800_GOT as usize).write(fd.reg_read(syst,0x5f));
            RX4800_GOT+=1;
        }
    }
    if flags&0x2000 != 0 {
        while RX4800_GOT<33 {
            words.add(RX4800_GOT as usize).write(fd.reg_read(syst,0x5f));
            RX4800_GOT+=1;
        }
        // Keep the RFIC receive configuration and saved snapshot until
        // re-arm or explicit stop. This is only a *completed RX packet*;
        // TX safety still requires rx4800_stop before any PA transition.
        RX4800_FRAME_READY=true;
        return 33;
    }
    0
}
#[cfg(not(k6_fsk4800_rx))]
unsafe extern "C" fn mod_fsk4800_rx(_:u32,_:*mut u16,_:u32)->i32 {-1}
#[cfg(not(k6_fsk4800_rx))]
unsafe fn rx4800_stop() {}

// The experimental API31 host currently implements RX only. API28-30
// callback slots are explicit stubs, never NULL pointers or misaligned tails.
// When full resource/FSK-async/mic services land, replace these stubs and
// their distinct initialization identifiers without shifting older offsets.
unsafe extern "C" fn mod_sotto_resource_unavailable(_:u32)->i32 {-1}
unsafe extern "C" fn mod_sotto_read_unavailable(_:u32,_:u32,_:*mut u8,_:u32)->i32 {-1}
unsafe extern "C" fn mod_sotto_async_unavailable(_:u32,_:*const u16,_:u32)->i32 {-1}
unsafe extern "C" fn mod_sotto_mic_unavailable(_:u32,_:*mut u8,_:u32)->i32 {-1}
// SOTTO_4800_RX_END
