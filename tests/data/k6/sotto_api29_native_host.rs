// Compile the unmodified firmware API29 callback against an IN-MEMORY RFIC.
// NO actual RF, serial, SDR, GPIO, flash, device, or bench access.
// The IRQ/byte-clock/FIFO-capacity behavior is deliberately injected;
// it is NOT a validated FD6818B silicon model.
#![allow(dead_code, static_mut_refs, non_camel_case_types)]
use std::ptr::{addr_of,addr_of_mut};
use std::env;

const SYMBOL_BAUD:u32=4800;
const AIR_BITS:u32=33*16;
const AIR_MS:u32=110; // 66 payload bytes at 4800 baud
const PREAMBLE_BITS:u32=160; // 16x55 + 4-byte sync, before payload
const TICKS_PER_MS:u32=10; // real mod_tick() is 10 ticks / ms
const PREAMBLE_TICKS:u32=334; // ceil(160 bits at 4800 baud in 0.1-ms ticks)

#[derive(Clone,Debug)]
enum Event {
    Reg(u32,u8,u16),
    Pa(u32,bool),
    CarrierOff(u32),
    LeaseOff(u32),
}
struct Chip {
    regs:[u16;256], events:Vec<Event>, fifo:Vec<u16>,
    tx_start:Option<u32>, irqat:u32, fifo_capacity:usize,
    pa:bool, pa_drop:Option<u32>, overflowed:bool,
}
impl Chip {
    fn new(irq:u32,capacity:usize,drop:Option<u32>)->Self {
        let mut regs=[0u16;256];
        regs[0]=0x4829;
        regs[0x59]=0x00f8;
        regs[0x3f]=0x0100;
        // Incoming fault times are measured relative to FIRST PAYLOAD BIT.
        // The RFIC must first transmit the public 160-bit preamble+sync.
        Self {regs,events:vec![],fifo:vec![],tx_start:None,
              irqat:irq+PREAMBLE_TICKS,
              fifo_capacity:capacity,pa:false,
              pa_drop:drop.map(|d|d+PREAMBLE_TICKS),overflowed:false}
    }
    fn elapsed(&self)->u32 {
        self.tx_start.map(|t| unsafe{TIME.saturating_sub(t)}).unwrap_or(0)
    }
    fn tx_ready(&self)->bool {
        self.pa && !self.pa_drop.map(|t|self.elapsed()>=t).unwrap_or(false)
    }
}
static mut CHIP:Option<Chip>=None;
static mut TIME:u32=0;
static mut CANCEL:bool=false;
static mut KEY:bool=false;
static mut POWER:bool=false;
static mut GUARD_COUNT:usize=0;
#[allow(clippy::upper_case_acronyms)] struct SYST;
struct Fd;
impl Fd {
    fn reg_read(&mut self,_:&mut SYST,r:u8)->u16 {
        let result=unsafe {
            let c=CHIP.as_ref().unwrap();
            match r {
                0x0c=>if c.tx_start.is_some() && c.elapsed()>=c.irqat {1} else {0},
                0x30=>if c.tx_ready() {2} else {0},
                _=>c.regs[r as usize],
            }
        };
        #[cfg(k6_sotto_fsk_coop_preload)] unsafe{TIME+=4;}
        result
    }
    fn reg_write(&mut self,_:&mut SYST,r:u8,v:u16){
        unsafe {
            let c=CHIP.as_mut().unwrap();
            c.events.push(Event::Reg(TIME,r,v));
            c.regs[r as usize]=v;
            if r==0x59 && v==0x80f8 {c.fifo.clear();c.tx_start=None;}
            if r==0x5f {
                if c.fifo.len()<c.fifo_capacity {c.fifo.push(v);}
                else {c.overflowed=true;} // intentionally silent 32-word cap
            }
            if r==0x59 && v==0x08f8 {c.tx_start=Some(TIME);}
            // 0.4-ms synthetic SPI register write latency in cooperative
            // tests only; this is an approximation, NOT a measured RFIC.
            #[cfg(k6_sotto_fsk_coop_preload)] {TIME+=4;}
        }
    }
}
struct Radio{fd6818:Fd}
impl Radio{
    fn cw_carrier_off(&mut self,_:&mut SYST){
        unsafe{
            let c=CHIP.as_mut().unwrap();
            c.events.push(Event::CarrierOff(TIME));
            c.pa=false;
        }
    }
}
struct App{radio:Radio}
static mut APP:App=App{radio:Radio{fd6818:Fd}};
static mut SYS:SYST=SYST;
struct ModCtx{app:*mut App,tx:bool,scratch:usize}
static mut MOD_CTX:ModCtx=ModCtx{app:std::ptr::null_mut(),tx:false,scratch:0};
static mut RAM:[u16;64]=[0;64];
unsafe fn mod_ctx()->(&'static mut App,&'static mut SYST) {
    (&mut *addr_of_mut!(APP),&mut *addr_of_mut!(SYS))
}
fn mod_tx_guard() {unsafe{GUARD_COUNT+=1;}}
unsafe fn mod_power_off_requested()->bool {POWER}
unsafe extern "C" fn mod_cancel_poll()->i32 {if CANCEL {1} else {0}}
unsafe extern "C" fn mod_key_down()->i32 {if KEY {1} else {0}}
fn mod_tick()->u32 {unsafe{TIME}}
unsafe extern "C" fn mod_fd_read(r:u8)->u16 {
    let (app,syst)=mod_ctx();app.radio.fd6818.reg_read(syst,r)
}
fn scratch_end()->usize {unsafe{addr_of!(RAM).cast::<u16>() as usize+128}}
unsafe extern "C" fn mod_tx_pa(on:u32)->i32{
    let c=CHIP.as_mut().unwrap();
    c.pa=on!=0;
    c.events.push(Event::Pa(TIME,c.pa));
    0
}
unsafe extern "C" fn mod_tx_off() {
    sotto_tx29_cleanup();
    MOD_CTX.tx=false;
    let c=CHIP.as_mut().unwrap();
    c.pa=false;
    c.events.push(Event::LeaseOff(TIME));
}

// Compile this exact firmware source directly; no rewritten behavioral port.
include!("../../../firmware/k6-aura/sotto-api29-async-fsk.rs.inc");

fn vector()->Vec<u16>{
    let s=env::var("SOTTO_API29_KNOWN_HEX").expect("canonical packet required");
    assert_eq!(s.len(),132);
    let bytes:Vec<u8>=(0..66).map(|i|u8::from_str_radix(&s[2*i..2*i+2],16).unwrap()).collect();
    assert_eq!(&bytes[64..66],&[0x5a,0xa5]);
    bytes.chunks_exact(2).map(|b|u16::from_le_bytes([b[0],b[1]])).collect()
}
fn reset(irq:u32,cap:usize,drop:Option<u32>) {
    unsafe {
        CHIP=Some(Chip::new(irq*TICKS_PER_MS,cap,drop.map(|v|v*TICKS_PER_MS)));
        TIME=0;CANCEL=false;KEY=false;POWER=false;GUARD_COUNT=0;
        MOD_CTX=ModCtx{app:addr_of_mut!(APP),tx:true,scratch:addr_of_mut!(RAM).cast::<u16>() as usize};
        SOTTO_TX29_ACTIVE=false;SOTTO_TX29_STARTED=false;SOTTO_TX29_SNAPSHOT=false;SOTTO_TX29_UNTIL=0;SOTTO_TX29_START_AT=0;
        SOTTO_TX29_SAVED=[0;9];
        #[cfg(k6_sotto_fsk_coop_preload)] {
            SOTTO_TX29_PRELOAD_PTR=std::ptr::null();
            SOTTO_TX29_PRELOAD_STEP=0;
        }
        let words=vector();
        RAM[..33].copy_from_slice(&words);
    }
}
fn call_start()->i32{
    unsafe{mod_sotto_async_fsk(0,addr_of!(RAM).cast::<u16>(),33)}
}
fn call_poll()->i32{
    unsafe{mod_sotto_async_fsk(1,std::ptr::null(),0)}
}
fn send(irq:u32,cap:usize,drop:Option<u32>,period:u32,phase:u32)->(i32,u32,usize,usize) {
    reset(irq,cap,drop);
    assert_eq!(call_start(),0);
    unsafe{
        let c=CHIP.as_ref().unwrap();
        assert_eq!(c.fifo.len(),cap.min(33));
        assert_eq!(c.tx_start,None, "RF must be off during settle");
        let events=&c.events;
        let fifo_first=events.iter().position(|x|matches!(x,Event::Reg(_,0x5f,_))).unwrap();
        let pa_on=events.iter().position(|x|matches!(x,Event::Pa(_,true))).unwrap();
        assert!(pa_on<fifo_first, "qualified sequence requires PA on for preload");
        assert!(!events.iter().any(|x|matches!(x,Event::Reg(_,0x59,0x08f8))));
        assert_eq!(c.regs[0x5d],0x4100);
        assert_eq!(c.fifo.last().copied(),Some(if cap==33 {0xa55a} else {vector()[31]}));
    }
    let mut rc=0;
    let mut t=phase;
    while t<=260 {
        unsafe{TIME=t*TICKS_PER_MS;}
        rc=call_poll();
        if rc!=0 {break;}
        t+=period;
    }
    assert_ne!(rc,0,"mock did not complete");
    let mut end_time=0;
    let mut written=0;
    let mut on_air=0;
    unsafe {
        if rc==1 {
            assert!(MOD_CTX.tx && SOTTO_TX29_SNAPSHOT && !SOTTO_TX29_ACTIVE);
            assert!(!SOTTO_TX29_STARTED);
            assert!(CHIP.as_ref().unwrap().pa, "PA must stay active for next frame");
            assert!(!CHIP.as_ref().unwrap().events.iter().any(|e| matches!(e,Event::CarrierOff(_))));
            mod_tx_off(); // end session explicitly; not implicitly per frame
        }
        let c=CHIP.as_ref().unwrap();
        assert!(!c.pa,"cleanup failed to disable PA");
        for event in &c.events {
            match *event {
                Event::CarrierOff(at) if end_time==0=>end_time=at/TICKS_PER_MS,
                Event::Reg(_,0x5f,_)=>written+=1,
                _=>{}
            }
        }
        assert!(end_time!=0,"no carrier-off event");
        assert_eq!(written,33,"wrong FIFO write count");
        assert!(!SOTTO_TX29_ACTIVE && !SOTTO_TX29_SNAPSHOT);
        let off=c.events.iter().position(|x|matches!(x,Event::CarrierOff(_))).unwrap();
        let post=c.events[off+1..].iter().any(|x|matches!(x,Event::Reg(_,0x59,0x00f8)));
        assert!(post, "cleanup did not disable TX register");
        // Modelled symbols after the 160-bit public preamble+sync only;
        // all times are measured from when 0x59=0x08f8 enabled TX.
        let start_ticks=c.tx_start.expect("RF start never occurred");
        assert!(start_ticks>=200,"20ms pre-air settling missing");
        let payload_ticks=(end_time*TICKS_PER_MS).saturating_sub(start_ticks+PREAMBLE_TICKS);
        on_air=(cap.min(33)*16).min(payload_ticks as usize*SYMBOL_BAUD as usize/10_000);
        if rc==1 {assert_eq!(end_time,t);}
    }
    (rc,end_time,written,on_air)
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn normal_33_words_full_guard_and_rx_restoration(){
    let (rc,off,written,bits)=send(AIR_MS,33,None,5,0);
    assert_eq!((rc,off,written,bits),(1,165,33,528));
    unsafe{
        let c=CHIP.as_ref().unwrap();
        assert_eq!(c.regs[0x59],0x00f8);
        assert_eq!(c.regs[0x3f],0x0100);
        assert_eq!(c.regs[0x5d],0);
    }
    println!("CASE nominal rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn early_irq_immediately_cuts_real_callback_mid_payload(){
    let (rc,off,written,bits)=send(90,33,None,5,0);
    assert_eq!((rc,off,written,bits),(1,145,33,439));
    println!("CASE early_done rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn poll_jitter_changes_termination_byte(){
    let (rc,off,written,bits)=send(90,33,None,7,3);
    assert_eq!((rc,off,written,bits),(1,150,33,444));
    println!("CASE early_done_jitter rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn silent_32_word_capacity_truncates_guard_but_not_crc(){
    let (rc,off,written,bits)=send(AIR_MS,32,None,5,0);
    assert_eq!((rc,off,written,bits),(1,165,33,512));
    unsafe{assert!(CHIP.as_ref().unwrap().overflowed);}
    println!("CASE capacity32 rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn tx_ready_drop_aborts_and_restores(){
    let (rc,off,written,bits)=send(AIR_MS,33,Some(95),5,0);
    assert_eq!((rc,off,written,bits),(-2,150,33,463));
    println!("CASE ready_drop rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn explicit_abort_unconditionally_dekeys(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe{TIME=70*TICKS_PER_MS;}
    assert_eq!(unsafe{mod_sotto_async_fsk(2,std::ptr::null(),0)},0);
    unsafe{
        assert!(!CHIP.as_ref().unwrap().pa);
        assert!(!MOD_CTX.tx && !SOTTO_TX29_ACTIVE && !SOTTO_TX29_SNAPSHOT);
    }
    println!("CASE explicit_abort rc=0 off_ms=70");
}

#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn three_frames_reuse_one_pa_lease_and_restore_exactly_once() {
    reset(AIR_MS,33,None);
    for frame in 0..3 {
        let base=frame*165;
        unsafe{TIME=base*TICKS_PER_MS;}
        assert_eq!(call_start(),0);
        unsafe {
            let c=CHIP.as_ref().unwrap();
            assert_eq!(c.events.iter().filter(|e|matches!(e,Event::Pa(_,true))).count(),1);
            assert_eq!(c.events.iter().filter(|e|matches!(e,Event::CarrierOff(_))).count(),0);
            assert!(c.tx_start.is_none() || frame>0);
        }
        unsafe{TIME=(base+19)*TICKS_PER_MS;}
        assert_eq!(call_poll(),0);
        unsafe{assert!(!SOTTO_TX29_STARTED);}
        unsafe{TIME=(base+20)*TICKS_PER_MS;}
        assert_eq!(call_poll(),0);
        unsafe{assert!(SOTTO_TX29_STARTED);}
        unsafe{TIME=(base+163)*TICKS_PER_MS;}
        assert_eq!(call_poll(),0);
        unsafe{TIME=(base+164)*TICKS_PER_MS;}
        assert_eq!(call_poll(),1);
        unsafe {
            assert!(CHIP.as_ref().unwrap().pa);
            assert!(SOTTO_TX29_SNAPSHOT && !SOTTO_TX29_ACTIVE);
            assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0x00f8);
        }
    }
    unsafe {
        assert_eq!(CHIP.as_ref().unwrap().fifo.len(),33);
        assert_eq!(CHIP.as_ref().unwrap().events.iter().filter(|e|matches!(e,Event::Reg(_,0x5f,_))).count(),99);
        mod_tx_off();
        assert!(!CHIP.as_ref().unwrap().pa);
        assert_eq!(CHIP.as_ref().unwrap().events.iter().filter(|e|matches!(e,Event::CarrierOff(_))).count(),1);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0x00f8);
    }
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn abort_during_preair_settle_releases_pa_and_restores() {
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe {TIME=10*TICKS_PER_MS;CANCEL=true;}
    assert_eq!(call_poll(),-2);
    unsafe {
        assert!(!MOD_CTX.tx && !SOTTO_TX29_SNAPSHOT && !SOTTO_TX29_ACTIVE);
        assert!(!CHIP.as_ref().unwrap().pa);
        assert_eq!(CHIP.as_ref().unwrap().tx_start,None);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
    }
}
#[cfg(not(k6_sotto_fsk_coop_preload))]
#[test]
fn missing_poll_during_settle_has_startup_watchdog() {
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe {TIME=201*TICKS_PER_MS;}
    assert_eq!(call_poll(),-2);
    unsafe {
        assert!(!MOD_CTX.tx && !CHIP.as_ref().unwrap().pa);
        assert_eq!(CHIP.as_ref().unwrap().tx_start,None);
    }
}

// The cooperative variant is a separate, explicitly opt-in host compile.
// Each phase is exercised against the ACTUAL production include above.
#[cfg(k6_sotto_fsk_coop_preload)]
fn progress_coop()->u32 {
    assert_eq!(call_start(),0);
    unsafe {
        assert_eq!(SOTTO_TX29_PRELOAD_STEP,1);
        assert!(TIME<=50,"synchronous start still monopolizes the foreground");
        assert!(CHIP.as_ref().unwrap().fifo.is_empty());
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
    }
    let mut max_ticks=unsafe{TIME};
    for phase in 1..=7 {
        unsafe{TIME=phase*50;}
        let before=unsafe{TIME};
        assert_eq!(call_poll(),0);
        let ticks=unsafe{TIME-before};
        assert!(ticks<=36,"overlarge foreground RFIC operation: {} ticks",ticks);
        max_ticks=max_ticks.max(ticks);
        unsafe {
            assert!(CHIP.as_ref().unwrap().tx_start.is_none());
            assert!(SOTTO_TX29_ACTIVE);
            assert_eq!(SOTTO_TX29_PRELOAD_STEP,if phase==7 {0} else {(phase+1) as u8});
            let expected=if phase<3 {0} else if phase==7 {33} else {(phase-2)*8} as usize;
            assert_eq!(CHIP.as_ref().unwrap().fifo.len(),expected,"phase {}",phase);
        }
    }
    unsafe {
        assert_eq!(CHIP.as_ref().unwrap().fifo,vector());
        assert_eq!(SOTTO_TX29_PRELOAD_STEP,0);
        assert!(SOTTO_TX29_PRELOAD_PTR.is_null());
        assert!(SOTTO_TX29_START_AT>=550);
        assert_eq!(CHIP.as_ref().unwrap().events.iter()
            .filter(|e|matches!(e,Event::Pa(_,true))).count(),1);
        let events=&CHIP.as_ref().unwrap().events;
        let pa_on=events.iter().position(|e|matches!(e,Event::Pa(_,true))).unwrap();
        let fifo_first=events.iter().position(|e|matches!(e,Event::Reg(_,0x5f,_))).unwrap();
        assert!(pa_on<fifo_first);
    }
    max_ticks
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_seven_bounded_preload_steps_have_exact_fifo_and_late_rf_start(){
    reset(AIR_MS,33,None);
    let max=progress_coop();
    unsafe{TIME=550;}
    assert_eq!(call_poll(),0); // 55 ms: 20ms settling not quite complete
    unsafe{assert!(CHIP.as_ref().unwrap().tx_start.is_none());}
    unsafe{TIME=600;}
    assert_eq!(call_poll(),0);
    unsafe {
        assert!(CHIP.as_ref().unwrap().tx_start.is_some());
        assert_eq!(CHIP.as_ref().unwrap().fifo.len(),33);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0x08f8);
    }
    unsafe{TIME=2100;}
    assert_eq!(call_poll(),1);
    unsafe {
        assert!(MOD_CTX.tx && CHIP.as_ref().unwrap().pa);
        mod_tx_off();
        assert!(!CHIP.as_ref().unwrap().pa);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0x00f8);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x5d],0);
    }
    println!("COOP max_callback_ticks={} fifo_words=33 rf_start_after_full_fifo=1",max);
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_cancel_during_fifo_preload_dekeys_and_restores(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    for step in 1..=4 {
        unsafe{TIME=step*50;}
        assert_eq!(call_poll(),0);
    }
    unsafe {
        assert_eq!(CHIP.as_ref().unwrap().fifo.len(),16);
        CANCEL=true;TIME=250;
    }
    assert_eq!(call_poll(),-2);
    unsafe {
        assert!(!MOD_CTX.tx && !CHIP.as_ref().unwrap().pa);
        assert!(!SOTTO_TX29_ACTIVE && !SOTTO_TX29_SNAPSHOT);
        assert!(SOTTO_TX29_PRELOAD_PTR.is_null());
        assert_eq!(SOTTO_TX29_PRELOAD_STEP,0);
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
        assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0x00f8);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
    }
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_corrupted_guard_during_preload_must_abort_before_air(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe{TIME=50;}
    assert_eq!(call_poll(),0);
    unsafe{RAM[32]=0x0000;TIME=100;}
    assert_eq!(call_poll(),-2);
    unsafe {
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
        assert!(!CHIP.as_ref().unwrap().pa && !MOD_CTX.tx);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
    }
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_stalled_preload_hits_same_watchdog_and_dekeys(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe{TIME=50;}
    assert_eq!(call_poll(),0);
    unsafe{TIME=2050;}
    assert_eq!(call_poll(),-2);
    unsafe {
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
        assert!(!MOD_CTX.tx && !CHIP.as_ref().unwrap().pa);
    }
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_duplicate_start_while_staged_fails_closed(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    assert_eq!(call_start(),-2);
    unsafe {
        assert!(!MOD_CTX.tx && !CHIP.as_ref().unwrap().pa);
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
    }
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_three_packets_reuse_one_pa_and_each_fifo_is_full(){
    reset(AIR_MS,33,None);
    for frame in 0..3 {
        let base=frame*225;
        unsafe{TIME=base*10;}
        assert_eq!(call_start(),0);
        for step in 1..=7 {
            unsafe{TIME=(base+step*5)*10;}
            assert_eq!(call_poll(),0);
        }
        unsafe{TIME=(base+60)*10;}
        assert_eq!(call_poll(),0);
        unsafe{TIME=(base+210)*10;}
        assert_eq!(call_poll(),1);
        unsafe {
            assert_eq!(CHIP.as_ref().unwrap().fifo,vector());
            assert!(CHIP.as_ref().unwrap().pa);
            assert!(!SOTTO_TX29_ACTIVE && SOTTO_TX29_SNAPSHOT);
            assert_eq!(CHIP.as_ref().unwrap().events.iter().filter(|e|matches!(e,Event::Pa(_,true))).count(),1);
            assert_eq!(CHIP.as_ref().unwrap().events.iter().filter(|e|matches!(e,Event::Reg(_,0x5f,_))).count(),((frame+1)*33) as usize);
        }
    }
    unsafe {
        mod_tx_off();
        assert_eq!(CHIP.as_ref().unwrap().events.iter().filter(|e|matches!(e,Event::CarrierOff(_))).count(),1);
        assert!(!CHIP.as_ref().unwrap().pa);
        assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x0100);
    }
}

#[cfg(k6_sotto_fsk_coop_preload)]
#[test]
fn coop_explicit_abort_mid_preload_never_keys_rf(){
    reset(AIR_MS,33,None);
    assert_eq!(call_start(),0);
    unsafe{TIME=50;}
    assert_eq!(call_poll(),0);
    unsafe{TIME=100;}
    assert_eq!(unsafe{mod_sotto_abort_for_test()},0);
    unsafe {
        assert!(!CHIP.as_ref().unwrap().pa && !MOD_CTX.tx);
        assert!(SOTTO_TX29_PRELOAD_PTR.is_null());
        assert!(CHIP.as_ref().unwrap().tx_start.is_none());
    }
}
#[cfg(k6_sotto_fsk_coop_preload)]
unsafe fn mod_sotto_abort_for_test()->i32 {
    mod_sotto_async_fsk(2,std::ptr::null(),0)
}
