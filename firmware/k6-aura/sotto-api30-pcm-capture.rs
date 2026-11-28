// Experimental TIM17 PA11 microphone sampler. No RF bus access from ISR.
// The only vector owner is txdog. Stop before returning to boot vectors.
use core::ptr::{read_volatile as rdv,write_volatile as wrv};
const A:u32=0x48000000;
const B:u32=0x48000400;
const T:u32=0x40014800;
const EN:u32=0x40021018;
const RST:u32=0x4002100c;
const MASK:u32=1<<18;
const IRQ:u32=1<<22;
const ISER:u32=0xe000e100;
const ICER:u32=0xe000e180;
const ICPR:u32=0xe000e280;
const IPR:u32=0xe000e416;
const PIN:u32=1<<11;
const MODE:u32=3<<22;
const PERIOD:u32=12000;
// 48 turns is the initial test profile, not a measured B-chip SNR guarantee.
const TURNS:u32=48;
#[repr(C)]
struct Ring {pcm:[i16;320],produced:u8,consumed:u8,offset:u8,lost:u8}
const _:()=assert!(core::mem::size_of::<Ring>()==644);
static mut RING:*mut Ring=core::ptr::null_mut();
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut SAVED:[u32;6]=[0;6];
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut CIC:[u32;4]=[0;4];
static mut DC:i32=0;
static mut WARM:u8=3;
static mut OLD_PRIO:u8=0;
static mut PEAK:u32=0;
// Debug-only raw PA11 sigma-delta diagnostics, cumulative across the active
// PCM session; read-only snapshots via API30 mic_stream ops 4/5/6.
// Count 1-bits, 0/1 edges, and frame-to-frame density changes, *before*
// the existing CIC and DC rejector. All counts stay below u32 in a run.
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RAW_ONES:u32=0;
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RAW_EDGES:u32=0;
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RAW_VARIATION:u32=0;
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RAW_LAST_ONES:u32=0xffff_ffff;
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut RAW_LAST_BIT:u32=2;

#[inline(always)]
fn rd(a:u32)->u32{unsafe{rdv(a as *const u32)}}
#[inline(always)]
fn wr(a:u32,v:u32){unsafe{wrv(a as *mut u32,v)}}
#[inline(always)]
fn mask(a:u32,m:u32,v:u32){wr(a,(rd(a)&!m)|(v&m))}
pub fn active()->bool{unsafe{!rdv(core::ptr::addr_of!(RING)).is_null()}}
pub fn start(ptr:*mut u8)->bool{
    cortex_m::interrupt::free(|_|unsafe{
        if active()||rd(EN)&MASK!=0||rd(ISER)&IRQ!=0||!super::txdog::pcm_vector(true, isr as *const () as usize as u32|1){return false;}
        SAVED=[rd(A),rd(A+8),rd(A+12),rd(A+20),rd(B+20),rd(EN)];
        OLD_PRIO=rdv(IPR as *const u8);
        wrv(IPR as *mut u8,0xc0);
        wr(B+24,1<<(2+16));mask(A,MODE,0);mask(A+12,MODE,0);mask(A+8,MODE,MODE);
        CIC=[0;4];DC=0;WARM=3;PEAK=0;RAW_ONES=0;RAW_EDGES=0;RAW_VARIATION=0;RAW_LAST_ONES=0xffff_ffff;RAW_LAST_BIT=2;
        let p=ptr.cast::<Ring>();
        // Caller provides an empty initialized ring. Never clear live samples.
        RING=p;
        wr(EN,rd(EN)|MASK);wr(RST,rd(RST)|MASK);wr(RST,rd(RST)&!MASK);
        wr(T,0);wr(T+0x28,0);wr(T+0x2c,PERIOD-1);wr(T+0x14,1);
        wr(T+0x10,0);wr(T+0x0c,1);wr(ICPR,IRQ);wr(ISER,IRQ);wr(T,1);
        true
    })
}
pub fn stop(){
    cortex_m::interrupt::free(|_|unsafe{
        if !active(){return;}
        wr(ICER,IRQ);wr(T,0);wr(T+0x0c,0);wr(T+0x10,0);wr(ICPR,IRQ);
        RING=core::ptr::null_mut();
        wr(RST,rd(RST)|MASK);wr(RST,rd(RST)&!MASK);wr(EN,rd(EN)&!MASK);
        wrv(IPR as *mut u8,OLD_PRIO);
        super::txdog::pcm_vector(false,0);
        mask(A,MODE,0);
        mask(A+8,MODE,SAVED[1]);mask(A+12,MODE,SAVED[2]);
        wr(A+24,if SAVED[3]&PIN!=0{PIN}else{PIN<<16});
        mask(A,MODE,SAVED[0]);
        wr(EN,SAVED[5]);
        // PA11 speaker switch remains muted until host receive setup.
    })
}
pub fn peak()->u32{unsafe{rdv(core::ptr::addr_of!(PEAK))}}
pub fn raw_ones()->u32{unsafe{rdv(core::ptr::addr_of!(RAW_ONES))}}
pub fn raw_edges()->u32{unsafe{rdv(core::ptr::addr_of!(RAW_EDGES))}}
pub fn raw_variation()->u32{unsafe{rdv(core::ptr::addr_of!(RAW_VARIATION))}}

unsafe extern "C" fn isr(){
    let t0=rd(T+0x24);
    wr(T+0x10,0);
    let p=rdv(core::ptr::addr_of!(RING));
    if p.is_null(){return;}
    let base=rd(A)&!MODE;
    let mut i1=CIC[0];let mut i2=CIC[1];
    let mut ones=0u32;let mut edges=0u32;let mut prev=RAW_LAST_BIT;
    for _ in 0..TURNS{
        wr(A+24,PIN);wr(A,base|(1<<22));
        core::arch::asm!("nop","nop","nop","nop",options(nomem,nostack,preserves_flags));
        wr(A,base);
        core::arch::asm!("nop","nop","nop","nop","nop","nop","nop","nop",options(nomem,nostack,preserves_flags));
        let b=(rd(A+16)>>11)&1;
        ones+=b;
        if prev<2{edges+=(prev^b)&1;}
        prev=b;
        wr(A+24,PIN<<16);wr(A,base|(b<<22));
        core::arch::asm!("nop","nop","nop",options(nomem,nostack,preserves_flags));
        wr(A,base);
        i1=i1.wrapping_add(b);i2=i2.wrapping_add(i1);
    }
    let d=i2.wrapping_sub(CIC[2]);let y=d.wrapping_sub(CIC[3]);
    CIC=[i1,i2,i2,d];
    let t1=rd(T+0x24);
    let elapsed=if t1>=t0{t1-t0}else{t1+PERIOD-t0};
    if elapsed>PEAK{PEAK=elapsed;}
    let lost=core::ptr::addr_of_mut!((*p).lost);
    // Any new update before this handler completed means the 125us
    // deadline was missed. Latch failure; foreground will dekey.
    if rd(T+0x10)&1!=0 {wrv(lost,1);return;}
    if WARM!=0{WARM-=1;if WARM==0{DC=(y<<8) as i32;}return;}
    RAW_ONES=RAW_ONES.wrapping_add(ones);
    RAW_EDGES=RAW_EDGES.wrapping_add(edges);
    if RAW_LAST_ONES<=TURNS{
        let d=if ones>RAW_LAST_ONES{ones-RAW_LAST_ONES}else{RAW_LAST_ONES-ones};
        RAW_VARIATION=RAW_VARIATION.wrapping_add(d);
    }
    RAW_LAST_ONES=ones;RAW_LAST_BIT=prev;
    let delta=((y<<8) as i32).wrapping_sub(DC);
    DC=DC.wrapping_add(delta>>3);
    let sample=(delta>>2).clamp(-32768,32767) as i16;
    let h=rdv(core::ptr::addr_of!((*p).produced));let tail=rdv(core::ptr::addr_of!((*p).consumed));
    if rdv(lost)!=0||h.wrapping_sub(tail)>=4{wrv(lost,1);return;}
    let off=rdv(core::ptr::addr_of!((*p).offset));
    if off>=80{wrv(lost,1);return;}
    wrv(core::ptr::addr_of_mut!((*p).pcm).cast::<i16>().add((h as usize&3)*80+off as usize),sample);
    if off==79{
        wrv(core::ptr::addr_of_mut!((*p).offset),0);
        core::sync::atomic::compiler_fence(core::sync::atomic::Ordering::Release);
        wrv(core::ptr::addr_of_mut!((*p).produced),h.wrapping_add(1));
    }else{wrv(core::ptr::addr_of_mut!((*p).offset),off+1);}
}
