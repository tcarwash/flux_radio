// Experimental B-chip GP0/PB1 -> DMA1 CH1 IRQ9 -> Sotto 8kHz PCM service.
// The firmware owns the DMA ring, decimator and interrupt; a loadable module
// supplies its 644-byte PCM queue through API30 mic_stream(op=8).
// The ISR never accesses SPI, RFIC, UART, LCD, flash or module callbacks.
// The DMA/IRQ service is synchronous to start/stop and only runs under txdog.
// SPDX-License-Identifier: MIT
use core::ptr::{read_volatile as rdv,write_volatile as wrv};
use core::sync::atomic::{compiler_fence,Ordering};

const RCC_AHB:u32=0x40021014;
const RCC_APB2:u32=0x40021018;
const DMA_ISR:u32=0x40020000;
const DMA_IFCR:u32=0x40020004;
const DMA_CCR1:u32=0x40020008;
const DMA_CNDTR1:u32=0x4002000c;
const DMA_CPAR1:u32=0x40020010;
const DMA_CMAR1:u32=0x40020014;
const TIM17:u32=0x40014800;
const TIM17_CNT:u32=TIM17+0x24;
const DMA_CLOCK:u32=1;
const TIM17_CLOCK:u32=1<<18;
const IRQ:u32=1<<9;
const NVIC_ISER:u32=0xe000e100;
const NVIC_ICER:u32=0xe000e180;
const NVIC_ISPR:u32=0xe000e200;
const NVIC_ICPR:u32=0xe000e280;
const NVIC_IPR9:u32=0xe000e409;
const DMA_HT:u32=4;
const DMA_TC:u32=2;
const DMA_TE:u32=8;
const RAW_BYTES:usize=1024;
const HALF:usize=512;
const FS:u32=87260;
const PERIOD_LIMIT:u32=9000; // 96MHz nominal; half period ~8.9k ticks

#[repr(C)]
pub struct PcmRing {
    pcm:[i16;320],
    produced:u8,
    consumed:u8,
    offset:u8,
    lost:u8,
}
const _:()=assert!(core::mem::size_of::<PcmRing>()==644);


#[repr(C)]
#[derive(Clone,Copy)]
struct Decimator {fs:u32,phase:u32,area:i32,dc:i32,samples:u32,clipped:u32}
impl Decimator {const fn empty()->Self{Self{fs:0,phase:0,area:0,dc:0,samples:0,clipped:0}}}

#[repr(C)]
struct State {
    armed:u8,
    expect_tc:u8,
    failed:u8,
    #[cfg(k6_sotto_gp0_timing)]
    reason:u8,
    #[cfg(not(k6_sotto_gp0_timing))]
    _pad:u8,
    old_priority:u8,
    _pad2:[u8;3],
    old_ahb:u32,
    old_apb2:u32,
    dec:Decimator,
}
impl State {const fn empty()->Self{Self{armed:0,expect_tc:0,failed:0,
    #[cfg(k6_sotto_gp0_timing)] reason:0,
    #[cfg(not(k6_sotto_gp0_timing))] _pad:0,
    old_priority:0,_pad2:[0;3],old_ahb:0,old_apb2:0,dec:Decimator::empty()}}}

#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_RAW_RING:*mut u8=core::ptr::null_mut();
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_PCM_RING:*mut PcmRing=core::ptr::null_mut();
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_IRQ_COUNT:u32=0;
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_PEAK_TICKS:u32=0;
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_MISSED_HALVES:u32=0;
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
#[no_mangle]
pub static mut SOTTO_GP0_ERROR:u32=0;
#[cfg_attr(k6_sotto_ram0_state,link_section=".ram0")]
static mut STATE:State=State::empty();

#[inline(always)] fn rd(a:u32)->u32{unsafe{rdv(a as *const u32)}}
#[inline(always)] fn wr(a:u32,v:u32){unsafe{wrv(a as *mut u32,v)}}
#[inline(always)] fn rd8(a:u32)->u8{unsafe{rdv(a as *const u8)}}
#[inline(always)] fn wr8(a:u32,v:u8){unsafe{wrv(a as *mut u8,v)}}

#[inline(always)]
fn udiv(mut n:u32,mut d:u32)->u32{
    if d==0{return 0;}
    let mut q=0u32;let mut bit=1u32;
    while d<n && d&0x8000_0000==0{d<<=1;bit<<=1;}
    while bit!=0{if n>=d{n-=d;q|=bit;}d>>=1;bit>>=1;}
    q
}
// Exact constant-denominator divider for the hardware GP0 PDM rate.
// 2^32 / 87260 = 49220 remainder 30096. The reciprocal underestimates
// by at most one, so one correction produces EXACTLY floor(n/87260).
// Two 16x16 multiplies replace the per-sample variable-length restoring
// divide on Cortex-M0; no u64 multiply, heap, FPU, or persistent state.
#[cfg(k6_sotto_gp0_fast_isr)]
#[inline(always)]
fn gp0_div_87260(n:u32)->u32{
    const MAGIC:u32=49220;
    const DIVISOR:u32=87260;
    let hi=(n>>16)*MAGIC;
    let lo=((n&0xffff)*MAGIC)>>16;
    let q=(hi+lo)>>16;
    q+((n-q*DIVISOR)>=DIVISOR) as u32
}
#[inline(always)]
fn dec_push(d:&mut Decimator,bits:u32,out:&mut i16)->i32{
    if d.fs<=16000||d.fs>160000||bits>64{return -1;}
    let x=2*(bits as i32)-64;
    let newphase=d.phase+8000;
    if newphase<d.fs{d.area+=x*8000;d.phase=newphase;return 0;}
    let left=d.fs-d.phase;
    let area=d.area+x*(left as i32);
    let mag=area.unsigned_abs();
    let num=mag<<8;
    #[cfg(k6_sotto_gp0_fast_isr)]
    let q=(if d.fs==FS {gp0_div_87260(num)} else {udiv(num,d.fs)}) as i32;
    #[cfg(not(k6_sotto_gp0_fast_isr))]
    let q=udiv(num,d.fs) as i32;
    let centered=if area<0{-q}else{q};
    d.dc+=((centered<<8)-d.dc)>>7;
    let mut sample=(centered-(d.dc>>8))*8;
    if sample>32767{sample=32767;d.clipped+=1;}
    if sample< -32768{sample=-32768;d.clipped+=1;}
    d.phase=newphase-d.fs;
    d.area=x*(d.phase as i32);
    d.samples+=1;
    *out=sample as i16;
    1
}
// The real hardware uses 16 volatile 32-bit PDM reads per 64-bit-count
// group. Unrolling removes 16 loop branches per group (128 per DMA IRQ).
// All reads remain volatile and in the original order.
#[cfg(k6_sotto_gp0_fast_isr)]
#[inline(always)]
unsafe fn gp0_bits_64(raw:*mut u8,off:usize)->u32 {
    let p=raw.add(off).cast::<u32>();
    let mut acc=0u32;
    macro_rules! pdm_word {
        ($n:expr)=>{acc=acc.wrapping_add(rdv(p.add($n))&0x0202_0202);};
    }
    pdm_word!(0);pdm_word!(1);pdm_word!(2);pdm_word!(3);
    pdm_word!(4);pdm_word!(5);pdm_word!(6);pdm_word!(7);
    pdm_word!(8);pdm_word!(9);pdm_word!(10);pdm_word!(11);
    pdm_word!(12);pdm_word!(13);pdm_word!(14);pdm_word!(15);
    acc>>=1;
    acc=(acc&0x00ff_00ff)+((acc>>8)&0x00ff_00ff);
    (acc&0xffff)+(acc>>16)
}
#[inline(always)]
unsafe fn pcm_sample(p:*mut PcmRing,sample:i16)->bool{
    let h=rdv(core::ptr::addr_of!((*p).produced));
    let tail=rdv(core::ptr::addr_of!((*p).consumed));
    if rdv(core::ptr::addr_of!((*p).lost))!=0||h.wrapping_sub(tail)>=4{
        wrv(core::ptr::addr_of_mut!((*p).lost),1);return false;
    }
    let off=rdv(core::ptr::addr_of!((*p).offset));
    if off>=80{wrv(core::ptr::addr_of_mut!((*p).lost),1);return false;}
    wrv(core::ptr::addr_of_mut!((*p).pcm).cast::<i16>().add((h as usize&3)*80+off as usize),sample);
    if off==79{
        wrv(core::ptr::addr_of_mut!((*p).offset),0);
        compiler_fence(Ordering::Release);
        wrv(core::ptr::addr_of_mut!((*p).produced),h.wrapping_add(1));
    }else{wrv(core::ptr::addr_of_mut!((*p).offset),off+1);}
    true
}
unsafe fn fail(code:u32,p:*mut PcmRing){
    SOTTO_GP0_ERROR=code;
    if !p.is_null(){wrv(core::ptr::addr_of_mut!((*p).lost),1);}
    wr(DMA_CCR1,0);wr(DMA_IFCR,15);
}

// The SRAM vector points here only while TXDOG owns MEM_MODE=3. DMA channel
// 1's HT/TC interrupt runs every ~90us; no other interrupt is masked here.
#[no_mangle]
pub unsafe extern "C" fn sotto_gp0_dma_irq(){
    let st=core::ptr::addr_of_mut!(STATE);
    let p=SOTTO_GP0_PCM_RING;
    if (*st).armed==0||p.is_null(){wr(DMA_IFCR,15);return;}
    if (*st).failed!=0{return;}
    let start=rd(TIM17_CNT)&0xffff;
    let flags=rd(DMA_ISR)&15;
    if flags&DMA_TE!=0{(*st).failed=1;fail(1,p);return;}
    if flags&(DMA_HT|DMA_TC)==(DMA_HT|DMA_TC){
        SOTTO_GP0_MISSED_HALVES=SOTTO_GP0_MISSED_HALVES.wrapping_add(1);
        (*st).failed=1;fail(2,p);return;
    }
    let expected=if (*st).expect_tc!=0{DMA_TC}else{DMA_HT};
    if flags&expected==0{
        SOTTO_GP0_MISSED_HALVES=SOTTO_GP0_MISSED_HALVES.wrapping_add(1);
        (*st).failed=1;fail(2,p);return;
    }
    // Ack before processing. If another half completes during this handler,
    // the next IRQ remains pending and the two flags detect a lost half.
    wr(DMA_IFCR,15);
    let off=if (*st).expect_tc!=0{HALF}else{0};
    (*st).expect_tc^=1;
    let raw=SOTTO_GP0_RAW_RING;
    for i in 0..8usize{
        #[cfg(k6_sotto_gp0_fast_isr)]
        let bits=gp0_bits_64(raw,off+i*64);
        #[cfg(not(k6_sotto_gp0_fast_isr))]
        let bits={
            let mut acc=0u32;
            for j in 0..16usize{
                let word=rdv(raw.add(off+i*64+j*4).cast::<u32>());
                acc=acc.wrapping_add(word&0x0202_0202);
            }
            acc>>=1;
            acc=(acc&0x00ff_00ff)+((acc>>8)&0x00ff_00ff);
            (acc&0xffff)+(acc>>16)
        };
        let mut sample=0i16;
        match dec_push(&mut (*st).dec,bits,&mut sample){
            -1=>{
                #[cfg(k6_sotto_gp0_timing)] {(*st).reason=1;} // invalid decimator input
                (*st).failed=3;fail(3,p);return;
            }
            1=>if !pcm_sample(p,sample){
                #[cfg(k6_sotto_gp0_timing)] {
                    let h=rdv(core::ptr::addr_of!((*p).produced));
                    let t=rdv(core::ptr::addr_of!((*p).consumed));
                    let o=rdv(core::ptr::addr_of!((*p).offset));
                    (*st).reason=if h.wrapping_sub(t)>=4{2} else if o>=80{3}else{4};
                }
                (*st).failed=3;fail(3,p);return;
            },
            _=>{},
        }
    }
    SOTTO_GP0_IRQ_COUNT=SOTTO_GP0_IRQ_COUNT.wrapping_add(1);
    let elapsed=(rd(TIM17_CNT)&0xffff).wrapping_sub(start)&0xffff;
    if elapsed>SOTTO_GP0_PEAK_TICKS{SOTTO_GP0_PEAK_TICKS=elapsed;}
    if elapsed>PERIOD_LIMIT{(*st).failed=4;fail(4,p);}
}

#[cfg(k6_sotto_gp0_timing)]
pub fn failure_reason()->u8{unsafe{rdv(core::ptr::addr_of!(STATE.reason))}}

pub fn active()->bool{unsafe{rdv(core::ptr::addr_of!(STATE.armed))!=0}}

pub fn start(ptr:*mut u8,fs:u32)->bool{
    cortex_m::interrupt::free(|_|unsafe{
        let st=core::ptr::addr_of_mut!(STATE);
        if (*st).armed!=0||ptr.is_null()||(ptr as usize&3)!=0||fs<35000||fs>130000
           ||super::pcm_capture::active(){return false;}
        let old_ahb=rd(RCC_AHB);let old_apb2=rd(RCC_APB2);
        if old_apb2&TIM17_CLOCK!=0{return false;}
        wr(RCC_AHB,old_ahb|DMA_CLOCK);wr(RCC_APB2,old_apb2|TIM17_CLOCK);
        // The channel must be wholly unconfigured, not merely disabled. This
        // lets stop restore the radio's prior state without spending RAM0 on
        // stale DMA descriptors. The previous GP0 diagnostic clears all four.
        if rd(DMA_CCR1)!=0||rd(DMA_CNDTR1)!=0||rd(DMA_CPAR1)!=0||rd(DMA_CMAR1)!=0
           ||rd(DMA_ISR)&15!=0||rd(NVIC_ISER)&IRQ!=0||rd(NVIC_ISPR)&IRQ!=0{
            wr(RCC_APB2,old_apb2);wr(RCC_AHB,old_ahb);return false;
        }
        let raw=(super::arena::end().saturating_sub(RAW_BYTES)) as *mut u8;
        if (raw as usize&3)!=0||(raw as usize)<ptr as usize+core::mem::size_of::<PcmRing>()
           ||super::arena::end().saturating_sub(1024)<super::arena::end().saturating_sub(1040)+16{
            wr(RCC_APB2,old_apb2);wr(RCC_AHB,old_ahb);return false;
        }
        let old_priority=rd8(NVIC_IPR9);
        (*st).old_ahb=old_ahb;(*st).old_apb2=old_apb2;
        (*st).old_priority=old_priority;
        (*st).dec=Decimator{fs,phase:0,area:0,dc:0,samples:0,clipped:0};
        (*st).expect_tc=0;(*st).failed=0;(*st).armed=1;
        #[cfg(k6_sotto_gp0_timing)] {(*st).reason=0;}
        SOTTO_GP0_PCM_RING=ptr.cast::<PcmRing>();
        SOTTO_GP0_RAW_RING=raw;
        SOTTO_GP0_IRQ_COUNT=0;SOTTO_GP0_PEAK_TICKS=0;SOTTO_GP0_MISSED_HALVES=0;SOTTO_GP0_ERROR=0;
        let p=SOTTO_GP0_PCM_RING;
        wrv(core::ptr::addr_of_mut!((*p).produced),0);
        wrv(core::ptr::addr_of_mut!((*p).consumed),0);
        wrv(core::ptr::addr_of_mut!((*p).offset),0);
        wrv(core::ptr::addr_of_mut!((*p).lost),0);
        // The raw ring borrows the final 1KiB of the arena, normally reserved
        // for the module LCD callback stack. The Sotto module does not invoke
        // LCD/say callbacks until after op1 stops this capture. Its own stack
        // is the page-buffer stack, not the arena stack (op8 enforces that).
        wr(TIM17,0);wr(TIM17+0x28,0);wr(TIM17+0x2c,0xffff);
        wr(TIM17+0x14,1);wr(TIM17+0x10,0);wr(TIM17+0x0c,1<<8);wr(TIM17,1);
        if !super::txdog::gp0_vector(true,sotto_gp0_dma_irq as *const () as usize as u32|1){
            (*st).armed=0;SOTTO_GP0_PCM_RING=core::ptr::null_mut();SOTTO_GP0_RAW_RING=core::ptr::null_mut();
            wr(TIM17,0);wr(TIM17+0x0c,0);
            wr(RCC_APB2,old_apb2);wr(RCC_AHB,old_ahb);return false;
        }
        wr8(NVIC_IPR9,0x80);wr(NVIC_ICPR,IRQ);
        wr(DMA_CCR1,0);wr(DMA_IFCR,15);
        wr(DMA_CPAR1,0x48000410);
        wr(DMA_CMAR1,raw as u32);
        wr(DMA_CNDTR1,RAW_BYTES as u32);
        wr(DMA_CCR1,(3<<12)|(1<<7)|(1<<5)|0x0e|1);
        wr(NVIC_ISER,IRQ);
        true
    })
}

pub fn stop(){
    cortex_m::interrupt::free(|_|unsafe{
        let st=core::ptr::addr_of_mut!(STATE);
        if (*st).armed==0{return;}
        wr(DMA_CCR1,0);wr(NVIC_ICER,IRQ);wr(DMA_IFCR,15);wr(NVIC_ICPR,IRQ);
        let _=super::txdog::gp0_vector(false,0);
        SOTTO_GP0_PCM_RING=core::ptr::null_mut();
        SOTTO_GP0_RAW_RING=core::ptr::null_mut();
        wr(DMA_CNDTR1,0);wr(DMA_CPAR1,0);wr(DMA_CMAR1,0);wr(DMA_CCR1,0);
        wr8(NVIC_IPR9,(*st).old_priority);
        wr(TIM17,0);wr(TIM17+0x0c,0);wr(TIM17+0x10,0);wr(TIM17+0x24,0);
        wr(RCC_APB2,(*st).old_apb2);wr(RCC_AHB,(*st).old_ahb);
        (*st).armed=0;
    })
}
