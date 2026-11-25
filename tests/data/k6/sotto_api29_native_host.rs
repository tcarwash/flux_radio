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
        unsafe {
            let c=CHIP.as_ref().unwrap();
            match r {
                0x0c=>if c.tx_start.is_some() && c.elapsed()>=c.irqat {1} else {0},
                0x30=>if c.tx_ready() {2} else {0},
                _=>c.regs[r as usize],
            }
        }
    }
    fn reg_write(&mut self,_:&mut SYST,r:u8,v:u16){
        unsafe {
            let c=CHIP.as_mut().unwrap();
            c.events.push(Event::Reg(TIME,r,v));
            c.regs[r as usize]=v;
            if r==0x59 && v==0x80f8 {c.fifo.clear();}
            if r==0x5f {
                if c.fifo.len()<c.fifo_capacity {c.fifo.push(v);}
                else {c.overflowed=true;} // intentionally silent 32-word cap
            }
            if r==0x59 && v==0x08f8 {c.tx_start=Some(TIME);}
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
        SOTTO_TX29_ACTIVE=false;SOTTO_TX29_SNAPSHOT=false;SOTTO_TX29_UNTIL=0;
        SOTTO_TX29_SAVED=[0;9];
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
        assert_eq!(c.tx_start,Some(0));
        let events=&c.events;
        let fifo_last=events.iter().rposition(|x|matches!(x,Event::Reg(_,0x5f,_))).unwrap();
        let pa_on=events.iter().position(|x|matches!(x,Event::Pa(_,true))).unwrap();
        let fsk_on=events.iter().position(|x|matches!(x,Event::Reg(_,0x59,0x08f8))).unwrap();
        assert!(fifo_last<pa_on && pa_on<fsk_on);
        assert_eq!(c.regs[0x5d],0x4100);
        assert_eq!(c.fifo.last().copied(),Some(if cap==33 {0xa55a} else {vector()[31]}));
    }
    let mut rc=0;
    let mut t=phase;
    while t<=180 {
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
        let payload_ticks=(end_time*TICKS_PER_MS).saturating_sub(PREAMBLE_TICKS);
        on_air=(cap.min(33)*16).min(payload_ticks as usize*SYMBOL_BAUD as usize/10_000);
        if rc==1 {assert_eq!(end_time,t);}
    }
    (rc,end_time,written,on_air)
}
#[test]
fn normal_33_words_full_guard_and_rx_restoration(){
    let (rc,off,written,bits)=send(AIR_MS,33,None,5,0);
    assert_eq!((rc,off,written,bits),(1,145,33,528));
    unsafe{
        let c=CHIP.as_ref().unwrap();
        assert_eq!(c.regs[0x59],0x00f8);
        assert_eq!(c.regs[0x3f],0x0100);
        assert_eq!(c.regs[0x5d],0);
    }
    println!("CASE nominal rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[test]
fn early_irq_immediately_cuts_real_callback_mid_payload(){
    let (rc,off,written,bits)=send(90,33,None,5,0);
    assert_eq!((rc,off,written,bits),(1,125,33,439));
    println!("CASE early_done rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[test]
fn poll_jitter_changes_termination_byte(){
    let (rc,off,written,bits)=send(90,33,None,7,3);
    assert_eq!((rc,off,written,bits),(1,129,33,458));
    println!("CASE early_done_jitter rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[test]
fn silent_32_word_capacity_truncates_guard_but_not_crc(){
    let (rc,off,written,bits)=send(AIR_MS,32,None,5,0);
    assert_eq!((rc,off,written,bits),(1,145,33,512));
    unsafe{assert!(CHIP.as_ref().unwrap().overflowed);}
    println!("CASE capacity32 rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
#[test]
fn tx_ready_drop_aborts_and_restores(){
    let (rc,off,written,bits)=send(AIR_MS,33,Some(95),5,0);
    assert_eq!((rc,off,written,bits),(-2,130,33,463));
    println!("CASE ready_drop rc={} off_ms={} words={} bits={} bytes={}",rc,off,written,bits,bits/8);
}
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
