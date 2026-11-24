// The actual firmware's Rust API31 receiver code, compiled with native rustc,
// exercising its register/queue transitions against a deterministic FIFO mock.
// NO radio, SPI, RF or K6 firmware is executed in this host harness.
// SPDX-License-Identifier: MIT
#![allow(dead_code, non_camel_case_types, static_mut_refs)]
use std::collections::VecDeque;
use std::ptr::{addr_of, addr_of_mut};

struct Chip {
    regs: [u16; 256],
    fifo: VecDeque<u16>,
    pending: u16,
    latched: u16,
    writes: Vec<(u8,u16)>,
    underflow: usize,
}
impl Default for Chip {
    fn default()->Self { Self {regs:[0;256], fifo:VecDeque::new(),
        pending:0,latched:0,writes:Vec::new(),underflow:0} }
}
static mut CHIP: Option<Chip> = None;
static mut TIME: u32=0;
static mut PTT_UP: bool=true;
static mut KEY: bool=false;
static mut CANCEL: bool=false;
static mut POWER: bool=false;
static mut GUARD_CALLS: usize=0;

struct Gpioa;
static GPIOA: Gpioa=Gpioa;
mod kd32f328_pac {
    pub struct Gpioa;
    impl Gpioa { pub fn ptr()->*const super::Gpioa{ &super::GPIOA } }
}
mod board {
    pub fn read_ptt(_: &super::Gpioa)->bool { unsafe {super::PTT_UP} }
}
struct Fd;
impl Fd {
    fn reg_read(&mut self,_: &mut SYST,r:u8)->u16 {
        let c=unsafe {CHIP.as_mut().unwrap()};
        match r {
            0x0c=> if c.pending|c.latched!=0 {1} else {0},
            0x02=>c.pending|c.latched,
            0x5f=> c.fifo.pop_front().unwrap_or_else(||{c.underflow+=1;0}),
            _=>c.regs[r as usize]
        }
    }
    fn reg_write(&mut self,_:&mut SYST,r:u8,v:u16) {
        let c=unsafe {CHIP.as_mut().unwrap()};
        c.writes.push((r,v));
        c.regs[r as usize]=v;
        if r==0x02 && v==0 {c.latched=c.pending;c.pending=0;}
    }
}
struct Radio {fd6818: Fd}
struct App { radio: Radio }
#[allow(clippy::upper_case_acronyms)]
struct SYST;
static mut APP:App=App{radio:Radio{fd6818:Fd}};
static mut SYS:SYST=SYST;
struct ModCtx { app:*mut App, tx:bool, scratch:usize }
static mut MOD_CTX:ModCtx=ModCtx{app:std::ptr::null_mut(),tx:false,scratch:0};
static mut RAM:[u16;128]=[0;128];
unsafe fn mod_ctx()->(&'static mut App,&'static mut SYST){(&mut *addr_of_mut!(APP),&mut *addr_of_mut!(SYS))}
fn mod_tx_guard(){unsafe {GUARD_CALLS+=1}}
unsafe extern "C" fn mod_cancel_poll()->i32 {if CANCEL {1} else {0}}
unsafe fn mod_power_off_requested()->bool{POWER}
unsafe extern "C" fn mod_fd_read(r:u8)->u16{let (app,sys)=mod_ctx();app.radio.fd6818.reg_read(sys,r)}
unsafe extern "C" fn mod_key_down()->i32 {if KEY {1} else {0}}
fn mod_tick()->u32 {unsafe {TIME}}
fn scratch_end()->usize{unsafe {(*addr_of!(RAM)).as_ptr() as usize+256}}
include!("../../../firmware/k6-aura/sotto-fsk4800-rx.rs.inc");

fn reset() {
    unsafe {
        CHIP=Some(Chip::default());
        CHIP.as_mut().unwrap().regs[0]=0x4829;
        let c=CHIP.as_mut().unwrap();
        for r in [0x3fu8,0x59,0x58,0x72,0x5a,0x5b,0x5c,0x5d,0x5e] {
            c.regs[r as usize]=0;
        }
        TIME=0;PTT_UP=true;KEY=false;CANCEL=false;POWER=false;GUARD_CALLS=0;
        MOD_CTX=ModCtx{app:addr_of_mut!(APP),tx:false,scratch:addr_of_mut!(RAM).cast::<u8>() as usize};
        RX4800_ACTIVE=false;RX4800_GOT=0;
    }
}
fn event(words:&[u16],flag:u16){
    unsafe {
        let c=CHIP.as_mut().unwrap();
        c.fifo.extend(words);
        c.pending|=flag;
    }
}
fn op(cmd:u32,ptr:*mut u16,cfg:u32)->i32{unsafe{mod_fsk4800_rx(cmd,ptr,cfg)}}
fn scratch()->*mut u16{addr_of_mut!(RAM).cast::<u16>()}
fn no_pa(){
    unsafe{assert!(!CHIP.as_ref().unwrap().writes.iter().any(|(r,_)|matches!(r,0x30|0x33|0x36|0x37)));}
}
fn quiet(){
    unsafe{assert_eq!(CHIP.as_ref().unwrap().underflow,0);}
    no_pa();
}
fn arm()->i32 {op(0,std::ptr::null_mut(),4)}

#[test]
fn exact_host_callback_full_fsk_fifo_frame_no_transmit() {
    reset();
    assert_eq!(arm(),0);
    unsafe{
        let c=CHIP.as_ref().unwrap();
        assert!(RX4800_ACTIVE);
        assert_eq!(c.regs[0x5d],0x4100);
        assert_eq!(c.regs[0x5e],0x0204);
        assert_eq!(c.regs[0x59],0x10f8);
        assert_eq!(c.regs[0x3f],0x3002);
    }
    let expected:Vec<u16>=(0..33).map(|i|0x2180+i).collect();
    for chunk in expected.chunks(4) {
        event(chunk,if chunk.len()==1 {0x2000} else {0x1000});
        let result=op(1,scratch(),0);
        if chunk.len()==1 {assert_eq!(result,33);}else{assert_eq!(result,0)}
    }
    let seen=unsafe{RAM[..33].to_vec()};
    assert_eq!(seen,expected);
    unsafe{
        assert!(!RX4800_ACTIVE);
        assert_eq!(RX4800_GOT,0);
        let c=CHIP.as_ref().unwrap();
        assert_eq!(c.regs[0x59],0);
        assert_eq!(c.regs[0x3f],0);
        assert!(c.fifo.is_empty());
    }
    assert_eq!(op(2,std::ptr::null_mut(),0),0);
    quiet();
}

#[test]
fn native_callback_uses_correct_active_low_ptt_and_cleans_up() {
    reset();
    unsafe{PTT_UP=false;} // physical pin low means PTT pressed
    assert_eq!(arm(),-2);
    unsafe{assert!(!RX4800_ACTIVE);PTT_UP=true;}
    assert_eq!(arm(),0);
    event(&[1,2,3,4],0x1000);
    unsafe{PTT_UP=false;}
    assert_eq!(op(1,scratch(),0),-2);
    unsafe{assert!(!RX4800_ACTIVE);assert_eq!(CHIP.as_ref().unwrap().regs[0x59],0);}
    quiet();
}

#[test]
fn native_callback_recovers_rx_registers_on_cancel_timeout_and_stop() {
    reset();
    assert_eq!(arm(),0);
    unsafe{CANCEL=true;}
    assert_eq!(op(1,scratch(),0),-2);
    unsafe{CANCEL=false;assert!(!RX4800_ACTIVE);}
    assert_eq!(arm(),0);
    unsafe{TIME=50000;}
    assert_eq!(op(1,scratch(),0),-2);
    assert_eq!(arm(),0);
    assert_eq!(op(2,std::ptr::null_mut(),0),0);
    unsafe{assert!(!RX4800_ACTIVE);}
    quiet();
}

#[test]
fn native_callback_rejects_bad_buffers_and_preserves_other_rf_owner() {
    reset();
    assert_eq!(op(0,std::ptr::null_mut(),8),-1);
    assert_eq!(arm(),0);
    let bad=scratch().wrapping_add(127);
    assert_eq!(op(1,bad,0),-2);
    assert_eq!(arm(),0);
    assert_eq!(op(1,scratch(),1),-2);
    unsafe{
        CHIP.as_mut().unwrap().regs[0x3f]=0x8000;
    }
    assert_eq!(arm(),-1);
    unsafe {assert!(!RX4800_ACTIVE);assert_eq!(CHIP.as_ref().unwrap().regs[0x3f],0x8000);}
    quiet();
}

#[test]
fn native_callback_recovers_with_key_exit_and_power_abort() {
    reset();
    assert_eq!(arm(),0);
    unsafe{KEY=true;}
    assert_eq!(op(1,scratch(),0),-2);
    unsafe{KEY=false;}
    assert_eq!(arm(),0);
    unsafe{POWER=true;}
    assert_eq!(op(1,scratch(),0),-2);
    unsafe{assert!(!RX4800_ACTIVE);}
    quiet();
}

#[test]
fn native_callback_streams_real_virtual_iq_receiver_frames() {
    let Ok(path)=std::env::var("SOTTO_RX4800_FRAMES") else {
        eprintln!("optional IQ-derived fixture not requested");
        return;
    };
    let data=std::fs::read(path).expect("read independently recovered RF frames");
    assert!(data.len()%66==0 && data.len()>=3*66);
    let mut recovered=Vec::with_capacity(data.len());
    reset();
    for frame in data.chunks_exact(66) {
        assert_eq!(arm(),0);
        let source:Vec<u16>=frame.chunks_exact(2)
            .map(|w|u16::from_le_bytes([w[0],w[1]])).collect();
        assert_eq!(source.len(),33);
        for (i,group) in source.chunks(4).enumerate() {
            // The event model exposes an asserted FIFO FULL at each
            // threshold, DONE when the last word has been received.
            // Consume each pending interrupt before advancing symbol time.
            let flag=if i==8 {0x2000} else {0x1000};
            event(group,flag);
            let rc=op(1,scratch(),0);
            assert_eq!(rc,if i==8 {33} else {0},"group {i}");
        }
        assert_eq!(unsafe{RAM[..33].to_vec()},source);
        recovered.extend_from_slice(frame);
        unsafe{assert!(!RX4800_ACTIVE);assert_eq!(CHIP.as_ref().unwrap().underflow,0);}
    }
    assert_eq!(recovered,data);
    assert_eq!(op(2,std::ptr::null_mut(),0),0);
    quiet();
}
