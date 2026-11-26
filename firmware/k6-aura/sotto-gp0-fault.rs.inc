// SOTTO_GP0_FAULT_TRACE_BEGIN
// Opt-in, foreground-only fault trace. No module ABI changes, RF work or SPI
// writes. The 24-byte record uses spare RAM0, preserving arena/main-stack ABI.
#[cfg(k6_sotto_gp0_trace)]
#[derive(Clone, Copy)]
struct Gp0FaultTrace {
    serial: u32, ret: i32,
    resource_errors: u16, fsk_errors: u16,
    irq_count: u16, irq_peak: u16, missed_halves: u16,
    last_fsk_error: i16,
    gp0_error: u8, state: u8,
}
#[cfg(k6_sotto_gp0_trace)]
impl Gp0FaultTrace {
    const fn empty() -> Self {
        Self {serial:0,ret:0,resource_errors:0,fsk_errors:0,
            irq_count:0,irq_peak:0,missed_halves:0,last_fsk_error:0,
            gp0_error:0,state:0}
    }
    // state bits: active(0), valid(1), started(2), lost(3).
    const fn active(&self)->bool{self.state&1!=0}
}
#[cfg(k6_sotto_gp0_trace)]
const _:()=assert!(core::mem::size_of::<Gp0FaultTrace>()==24);
#[cfg(k6_sotto_gp0_trace)]
#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]
static mut GP0_FAULT_TRACE:Gp0FaultTrace=Gp0FaultTrace::empty();

#[cfg(k6_sotto_gp0_trace)]
fn gp0_fault_trace_begin(serial:u32, module:*mut u8) {
    if module.is_null() || unsafe{core::slice::from_raw_parts(module,13)}!=b"K6NMSOTTO GP0" {
        return; // never overwrite a GP0 report when unrelated modules run
    }
    unsafe{
        GP0_FAULT_TRACE=Gp0FaultTrace::empty();
        GP0_FAULT_TRACE.serial=serial;
        GP0_FAULT_TRACE.state=1;
    }
}
#[cfg(k6_sotto_gp0_trace)]
fn gp0_fault_trace_finish(ret:i32) {
    unsafe {
        if !GP0_FAULT_TRACE.active(){return;}
        GP0_FAULT_TRACE.state=(GP0_FAULT_TRACE.state&!1)|2;
        GP0_FAULT_TRACE.ret=ret;
        // GP0 HAL counters persist until the next capture. If this module
        // returned before a successful op8 start, they belong to an older
        // run and MUST NOT be mistaken for this run's failure evidence.
        if GP0_FAULT_TRACE.state&4!=0 {
            GP0_FAULT_TRACE.gp0_error=core::ptr::read_volatile(
                core::ptr::addr_of!(crate::hal::gp0_capture::SOTTO_GP0_ERROR)).min(255) as u8;
            GP0_FAULT_TRACE.irq_count=core::ptr::read_volatile(
                core::ptr::addr_of!(crate::hal::gp0_capture::SOTTO_GP0_IRQ_COUNT)).min(65535) as u16;
            GP0_FAULT_TRACE.irq_peak=core::ptr::read_volatile(
                core::ptr::addr_of!(crate::hal::gp0_capture::SOTTO_GP0_PEAK_TICKS)).min(65535) as u16;
            GP0_FAULT_TRACE.missed_halves=core::ptr::read_volatile(
                core::ptr::addr_of!(crate::hal::gp0_capture::SOTTO_GP0_MISSED_HALVES)).min(65535) as u16;
            if GP0_FAULT_TRACE.gp0_error!=0 {GP0_FAULT_TRACE.state|=8;}
        }
    }
}
#[cfg(k6_sotto_gp0_trace)]
unsafe extern "C" fn mod_sotto_resource_read_traced(
    kind:u32, off:u32, buf:*mut u8, n:u32)->i32 {
    let r=unsafe{mod_sotto_resource_read(kind,off,buf,n)};
    unsafe {
        if GP0_FAULT_TRACE.active() && r!=0 {
            GP0_FAULT_TRACE.resource_errors=GP0_FAULT_TRACE.resource_errors.saturating_add(1);
        }
    }
    r
}
#[cfg(k6_sotto_gp0_trace)]
unsafe extern "C" fn mod_sotto_async_fsk_traced(
    op:u32, words:*const u16, n:u32)->i32 {
    let r=unsafe{mod_sotto_async_fsk(op,words,n)};
    unsafe {
        if GP0_FAULT_TRACE.active() && (op==0 || op==1) &&
           (r<0 || (op==0 && r!=0) || (op==1 && r>1)) {
            GP0_FAULT_TRACE.fsk_errors=GP0_FAULT_TRACE.fsk_errors.saturating_add(1);
            GP0_FAULT_TRACE.last_fsk_error=r.clamp(-32768,32767) as i16;
        }
    }
    r
}
#[cfg(k6_sotto_gp0_trace)]
unsafe extern "C" fn mod_sotto_mic_stream_traced(
    op:u32, ptr:*mut u8, n:u32)->i32 {
    // Preserve the queue pointer before op1 stops IRQ9 and clears it.
    let ring=unsafe{crate::hal::gp0_capture::SOTTO_GP0_PCM_RING};
    let r=unsafe{mod_sotto_mic_stream(op,ptr,n)};
    unsafe {
        if GP0_FAULT_TRACE.active() {
            if op==8 && r==0 { GP0_FAULT_TRACE.state|=4; }
            if op==1 && !ring.is_null() &&
                crate::hal::gp0_capture::fault_ring_lost(ring) {
                GP0_FAULT_TRACE.state|=8;
            }
        }
    }
    r
}
#[cfg(k6_sotto_gp0_trace)]
impl App {
    // 44-byte cable-only, read-only diagnostic. State saturates at u16 max.
    pub(crate) fn gp0_fault_trace_report(&self)->[u8;44] {
        let mut out=[0u8;44];
        out[..4].copy_from_slice(b"GPF1");
        let s=unsafe{GP0_FAULT_TRACE};
        let valid=s.state&2!=0;
        let flags=(valid as u32) |
            (((s.state&4!=0) as u32)<<1) |
            (((s.state&8!=0) as u32)<<2) |
            (((s.resource_errors!=0) as u32)<<3) |
            (((s.fsk_errors!=0) as u32)<<4) |
            (((valid && s.ret<0) as u32)<<5);
        let values=[s.serial,s.ret as u32,flags,
            s.resource_errors as u32,s.fsk_errors as u32,
            s.last_fsk_error as i32 as u32,s.gp0_error as u32,
            s.irq_count as u32,s.irq_peak as u32,s.missed_halves as u32];
        for (i,v) in values.iter().enumerate() {
            let off=4+4*i;
            out[off..off+4].copy_from_slice(&v.to_le_bytes());
        }
        out
    }
}
// SOTTO_GP0_FAULT_TRACE_END
