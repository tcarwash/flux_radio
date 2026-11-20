// Live Flux cable protocol v1. Each request is independent; no loader ownership,
// stack reuse or reboot. Bulk reads temporarily change UART rate. Shared UART replies use CPS framing.
use super::frame;
use crate::{app::{App, Mode}, hal::{uart, wear_leveled::crc16_xmodem}};
use cortex_m::peripheral::SYST;

pub const MAGIC: &[u8; 6] = b"FLXLV1";
pub const MAGIC_V2: &[u8; 6] = b"FLXLV2";
const V2_MAX: usize = 128;
const V2_ECHO: u8 = 0x70;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_BEGIN:u8=0x71;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_CHUNK:u8=0x72;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_COMMIT:u8=0x73;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_ABORT:u8=0x74;

pub const REQUEST_LEN: usize = 14;
pub const STATUS: u8 = 0x60;
pub const READ: u8 = 0x61;
pub const TUNE: u8 = 0x62;
pub const RUN: u8 = 0x66;
pub const RUN_EVENT: u8 = 0x67;
pub const CAPS: u8 = 0x68;
pub const MODE: u8 = 0x69;
pub const VFO: u8 = 0x6a;
pub const BURST: u8 = 0x6b;
pub const INFO: u8 = 0x6e;
pub const WATERMARK:u8=0x6f;
#[cfg(k6_serial_transport)]
const BURST_DATA: u8 = 0x6c;
#[cfg(k6_serial_transport)]
const BURST_END: u8 = 0x6d;
const OK: u8 = 0;
const BUSY: u8 = 1;
const INVALID: u8 = 2;
const IO: u8 = 3;

pub struct Request { pub cmd: u8, pub seq: u8, pub arg: u32 }

pub fn decode(data: &[u8]) -> Option<Request> {
    if data.len() != REQUEST_LEN || &data[..6] != MAGIC
        || crc16_xmodem(&data[..12]) != u16::from_be_bytes([data[12], data[13]]) {
        return None;
    }
    Some(Request { cmd: data[6], seq: data[7],
        arg: u32::from_le_bytes([data[8], data[9], data[10], data[11]]) })
}

fn reply(req: &Request, data: &[u8]) {
    let mut buf = [0u8; 73];
    let n = frame::encode(&mut buf, req.cmd, req.seq as u16, data);
    for &b in &buf[..n] { uart::send_byte(b); }
}

// A bounded, downlink-only baud excursion. The request and ACK are always
// at 115200. No host bytes are needed to restore the normal UART rate.
#[cfg(k6_serial_transport)]
fn spi_burst(app: &mut App, req: &Request) {
    use crate::hal::{clock::SYSCLK_HZ, uptime};
    let start = (req.arg & 0x7fff) * 64;
    let count = (req.arg >> 15) & 31;
    let rate = match req.arg >> 20 {
        0 => 115200, 1 => 192000, 2 => 500000,
        _ => {reply(req,&[INVALID]);return;}
    };
    if count == 0 || count > 16 || (rate==115200 && count>4)
        || start + count*256 > 0x200000 {reply(req,&[INVALID]);return;}
    if !app.live_bulk_idle() || uart::binary_mode() {reply(req,&[BUSY]);return;}
    let prior = uart::cable_stats();
    let mut ack=[0u8;5];
    let divisor=(SYSCLK_HZ+rate/2)/rate;
    ack[1..].copy_from_slice(&(SYSCLK_HZ/divisor).to_le_bytes());
    // Suppress debug output only during this exchange.
    uart::set_binary_mode(true);
    reply(req,&ack);
    if rate!=115200 {
        let t=uptime::now();
        while uptime::now().wrapping_sub(t)<300 {}
        uart::cable_baud(rate);
    }
    let mut result=OK;
    let mut sent=0u8;
    for i in 0..count {
        // Only RX is permitted. Incoming bytes during a burst are not commands.
        if app.radio_mut().ptt_asserted() || uart::take_byte().is_some() {
            result=BUSY;break;
        }
        let offset=start+i*256;
        let mut data=[0u8;260];
        data[..4].copy_from_slice(&offset.to_le_bytes());
        if !app.storage_mut().norflash.read_bytes_checked(offset,&mut data[4..]) {
            result=IO;break;
        }
        let mut buf=[0u8;268];
        let n=frame::encode(&mut buf,BURST_DATA,req.seq as u16,&data);
        for &b in &buf[..n] {uart::send_byte(b);}
        sent+=1;
    }
    // Explicitly drain bytes received while the host was meant to be silent.
    for _ in 0..320 {
        if uart::take_byte().is_some() {result=BUSY;} else {break;}
    }
    let after=uart::cable_stats();
    let mut end=[0u8;18];
    end[0]=result;end[1]=sent;
    for i in 0..4 {
        end[2+i*4..6+i*4].copy_from_slice(&after[i].wrapping_sub(prior[i]).to_le_bytes());
    }
    reply(&Request{cmd:BURST_END,seq:req.seq,arg:0},&end);
    // cable_baud waits for TC even in a lean build. Also use it at the
    // baseline to ensure the last byte has left before reenabling logs.
    uart::cable_baud(115200);
    for _ in 0..320 {if uart::take_byte().is_none() {break;}}
    uart::set_binary_mode(false);
}

// V2 uses high-bit-only symbols for the body, including the CRC. No body
// fragment can look like a V1 or legacy programming handshake after a timeout.
struct V2Frame {
    raw: [u8; 6+8+V2_MAX+2],
    n: usize,
}
impl V2Frame {
    fn req(&self) -> Request {
        Request {cmd:self.raw[6],seq:self.raw[7],
            arg:u32::from_le_bytes([self.raw[10],self.raw[11],self.raw[12],self.raw[13]])}
    }
    fn payload(&self) -> &[u8] {&self.raw[14..self.n-2]}
}

// Called only after the detector has consumed the full V2 prefix. Bound the
// wait even for a disconnected host; invalid bodies are consumed to NUL.
fn receive_v2() -> Option<V2Frame> {
    use crate::hal::uptime;
    let mut f=V2Frame{raw:[0;6+8+V2_MAX+2],n:6};
    f.raw[..6].copy_from_slice(MAGIC_V2);
    let mut bits=0u8;
    let mut acc=0u16;
    let mut symbols=0usize;
    let mut bad=false;
    let t=uptime::now();
    loop {
        if uptime::now().wrapping_sub(t)>=1000 {return None;}
        let Some(b)=uart::take_byte() else {continue;};
        if b==0 {break;}
        if bad {continue;}
        if b&0x80==0 {bad=true;continue;}
        symbols+=1;
        acc|=u16::from(b&127)<<bits;
        bits+=7;
        if bits>=8 {
            if f.n==f.raw.len() {bad=true;continue;}
            f.raw[f.n]=acc as u8;
            f.n+=1;
            acc>>=8;bits-=8;
        }
    }
    if bad || acc!=0 || f.n<16 || symbols!=((f.n-6)*8+6)/7 {return None;}
    let len=u16::from_le_bytes([f.raw[8],f.raw[9]]) as usize;
    if len>V2_MAX || f.n!=16+len
        || crc16_xmodem(&f.raw[..f.n-2])!=u16::from_be_bytes([f.raw[f.n-2],f.raw[f.n-1]]) {return None;}
    Some(f)
}

fn reply_v2(req:&Request,payload:&[u8]) {
    let mut buf=[0u8;V2_MAX+9];
    let mut data=[0u8;V2_MAX+1];
    data[1..1+payload.len()].copy_from_slice(payload);
    let n=frame::encode(&mut buf,req.cmd,req.seq as u16,&data[..1+payload.len()]);
    for &b in &buf[..n] {uart::send_byte(b);}
}

// RAM staging is a single bounded exchange on the borrowed arena stack.
// Keep the lower 4 KiB for code and at least 4 KiB for this stack/interrupts.
#[cfg(all(feature="spilib",k6_ram0_stack))]
fn ram_allowed(app:&mut App)->bool {
    !app.radio_mut().ptt_asserted() &&
        !crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
}

#[cfg(all(feature="spilib",k6_ram0_stack))]
fn ram_upload(app:&mut App, req:&Request, payload:&[u8])->Option<(u8,i32)> {
    use crate::hal::{arena,uptime};
    let n=req.arg as usize;
    if !payload.is_empty() || n<25 || n>4096 || n+4096>arena::ARENA_BYTES {
        reply(req,&[INVALID]);return None;
    }
    if !ram_allowed(app) || uart::binary_mode() || !app.live_ram_begin() {
        reply(req,&[BUSY]);return None;
    }
    uart::set_binary_mode(true);
    reply(req,&[OK]);
    let t=uptime::now();
    let mut offset=0usize;
    let mut job=None;
    // Per-frame prefix deadline 500 ms, whole transaction <3.5 s plus
    // at most 100 ms for a frame body. No other request is dispatched here.
    'upload: loop {
        let frame_start=uptime::now();
        for &want in MAGIC_V2 {
            loop {
                if !ram_allowed(app) || uptime::now().wrapping_sub(t)>=35000
                    || uptime::now().wrapping_sub(frame_start)>=5000 {break 'upload;}
                if let Some(b)=uart::take_byte() {
                    if b!=want {break 'upload;}
                    break;
                }
            }
        }
        let Some(f)=receive_v2() else {break;};
        let r=f.req();
        let p=f.payload();
        if !ram_allowed(app) || uptime::now().wrapping_sub(t)>=35000 {
            reply(&r,&[BUSY]);break;
        }
        match r.cmd {
            RAM_CHUNK if r.arg as usize==offset && !p.is_empty() && offset+p.len()<=n => {
                unsafe{core::ptr::copy_nonoverlapping(p.as_ptr(),arena::base().add(offset),p.len());}
                offset+=p.len();reply(&r,&[OK]);
            }
            RAM_ABORT if r.arg==0 && p.is_empty()=>{reply(&r,&[OK]);break;}
            RAM_COMMIT if r.arg as usize==n && offset==n && p.is_empty()=>{
                unsafe extern "C" {fn km_live_verify(a:*const u8,n:u32)->i32;}
                let checked=unsafe{km_live_verify(arena::base(),n as u32)};
                let mut d=[0u8;5];d[1..].copy_from_slice(&checked.to_le_bytes());
                reply(&r,&d);
                if checked>0 {job=Some((r.seq,checked));}
                break;
            }
            _=>{reply(&r,&[INVALID]);break;}
        }
    }
    uart::flush();
    uart::set_binary_mode(false);
    if job.is_none(){app.live_ram_abort();}
    job
}

struct V2Args {app:*mut App,job:Option<(u8,i32)>}
unsafe extern "C" fn dispatch_v2_on_stack(raw: *mut core::ffi::c_void) {
    let Some(f)=receive_v2() else {return;};
    let req=f.req();
    let args=unsafe{&mut *raw.cast::<V2Args>()};
    match req.cmd {
        V2_ECHO if req.arg==0 => reply_v2(&req,f.payload()),
        #[cfg(all(feature="spilib",k6_ram0_stack))]
        RAM_BEGIN=>{args.job=ram_upload(unsafe{&mut *args.app},&req,f.payload());}
        _=>reply(&req,&[INVALID]),
    }
}

#[inline(never)]
pub fn dispatch_v2(app:&mut App,syst:&mut SYST) {
    unsafe extern "C" {
        fn km_call_on_stack(top:*mut u8,f:unsafe extern "C" fn(*mut core::ffi::c_void),arg:*mut core::ffi::c_void);
    }
    let mut args=V2Args{app,job:None};
    unsafe {km_call_on_stack(crate::hal::arena::end() as *mut u8,dispatch_v2_on_stack,(&mut args as *mut V2Args).cast());}
    // Switch back to the audited main stack BEFORE calling the normal
    // runner. Its LCD callbacks borrow the arena top as their own stack.
    #[cfg(all(feature="spilib",k6_ram0_stack))]
    if let Some((seq,n))=args.job {app.live_ram_finish(syst,seq,n);}
}

// Events are emitted by the normal deferred module dispatcher, not the
// live request stack. Text is bounded by the module API (60 bytes).
pub fn module_text(seq: u8, text: &[u8]) {
    let mut d = [0u8; 61];
    let n = text.len().min(60);
    d[1..1+n].copy_from_slice(&text[..n]);
    reply(&Request { cmd: RUN_EVENT, seq, arg: 0 }, &d[..1+n]);
}

pub fn module_finish(seq: u8, slot: u8, load: i32, result: Option<i32>) {
    let mut d = [0u8; 11];
    d[0] = 1;
    d[1] = slot;
    d[2..6].copy_from_slice(&load.to_le_bytes());
    if let Some(ret) = result {
        d[6] = 1;
        d[7..11].copy_from_slice(&ret.to_le_bytes());
    }
    reply(&Request { cmd: RUN_EVENT, seq, arg: 0 }, &d);
}

// Use the same *returning* arena stack trampoline as normal UI/key work,
// not the loader's destructive SRAM-top stack.
struct Args { app: *mut App, syst: *mut SYST, req: Request }

unsafe extern "C" fn dispatch_on_stack(raw: *mut core::ffi::c_void) {
    let a = unsafe { &mut *raw.cast::<Args>() };
    handle(unsafe { &mut *a.app }, unsafe { &mut *a.syst }, &a.req);
}

#[inline(never)]
pub fn dispatch(app: &mut App, syst: &mut SYST, req: Request) {
    let mut args = Args { app, syst, req };
    unsafe extern "C" {
        fn km_call_on_stack(top: *mut u8, f: unsafe extern "C" fn(*mut core::ffi::c_void),
                            arg: *mut core::ffi::c_void);
    }
    unsafe { km_call_on_stack(crate::hal::arena::end() as *mut u8,
        dispatch_on_stack, (&mut args as *mut Args).cast()); }
}

fn handle(app: &mut App, syst: &mut SYST, req: &Request) {
    // No SPI or tuning while transmitting, asleep, or an app owns the RF path.
    let idle = app.mode() == Mode::Standby && !app.is_transmitting()
        && !app.cw_key_down() && !app.radio_mut().ptt_asserted();
    match req.cmd {
        STATUS if req.arg == 0 => {
            let mut d = [0u8; 27];
            d[0] = OK;
            d[1..5].copy_from_slice(b"FLIV");
            d[5] = 1;
            d[6] = app.master_index() as u8;
            d[7] = app.watching_index() as u8;
            d[8] = (app.is_transmitting() as u8) | ((app.rssi_open() as u8) << 1)
                | ((idle as u8) << 2) | ((app.power_save_is_asleep() as u8) << 3)
                | ((app.side_is_channel_mode(0) as u8) << 4)
                | ((app.side_is_channel_mode(1) as u8) << 5);
            d[9..13].copy_from_slice(&app.side_freq_hz(0).to_le_bytes());
            d[13..17].copy_from_slice(&app.side_freq_hz(1).to_le_bytes());
            d[17..19].copy_from_slice(&app.battery_voltage_cv().to_le_bytes());
            d[19..23].copy_from_slice(&app.rssi_dbm().to_le_bytes());
            d[23] = app.live_modulation(0);
            d[24] = app.live_modulation(1);
            d[25] = app.side_wide_band(0) as u8;
            d[26] = app.side_wide_band(1) as u8;
            reply(req, &d);
        }
        READ if req.arg % 64 == 0 && (req.arg <= 0xb000 || (0xf000..=0xffc0).contains(&req.arg)) => {
            if !idle { reply(req, &[BUSY]); return; }
            let mut d = [0u8; 65];
            if app.storage_mut().norflash.read_bytes_checked(req.arg, &mut d[1..]) {
                reply(req, &d);
            } else { reply(req, &[IO]); }
        }
        #[cfg(feature = "spilib")]
        RUN if req.arg & !0x11f == 0 => {
            if !idle || app.power_save_is_asleep() {
                reply(req, &[BUSY]); return;
            }
            let result = app.live_queue_module(req.arg as u8, req.seq, req.arg & 0x100 != 0);
            reply(req, &[result]);
            uart::flush();
        }
        // Capability bits are protocol features, not the module API level.
        CAPS if req.arg == 0 => {
            let flags: u32 = 1 | 2 | 4 | 16 | 32 | 256 | 512
                | if cfg!(feature="spilib") {8} else {0}
                | if cfg!(k6_health_telemetry) {64} else {0}
                | if cfg!(k6_serial_transport) {128} else {0}
                | if cfg!(all(feature="spilib",k6_ram0_stack)) {1024} else {0};
            let mut d=[0u8;10];
            d[1..5].copy_from_slice(b"FLCP");
            d[5]=1;
            d[6..10].copy_from_slice(&flags.to_le_bytes());
            reply(req,&d);
        }
        #[cfg(k6_health_telemetry)]
        0x65 if req.arg == 0 => {
            if !idle || app.power_save_is_asleep() {reply(req,&[BUSY]);return;}
            let (temp,vref)=App::serial_lab_temp(syst);
            if temp<=0 || vref<=0 {reply(req,&[IO]);return;}
            let vals=[temp,vref,app.battery_voltage_cv() as i32];
            let mut d=[0u8;13];
            for (i,v) in vals.iter().enumerate(){d[1+i*4..5+i*4].copy_from_slice(&v.to_le_bytes());}
            reply(req,&d);
        }
        #[cfg(k6_serial_transport)]
        BURST => spi_burst(app,req),
        #[cfg(all(feature="spilib",k6_ram0_stack))]
        WATERMARK if req.arg==0 => {
            unsafe extern "C" {fn km_serial_highwater()->u32;}
            let mut d=[0u8;5];
            d[1..].copy_from_slice(&unsafe{km_serial_highwater()}.to_le_bytes());
            reply(req,&d);
        }
        INFO if req.arg==0 => {
            let mut d=[0u8;17];
            d[1..5].copy_from_slice(b"BLWF");
            d[5]=if cfg!(k6_user_store) {5} else if cfg!(k6_designer) {4} else {2};
            d[6]=32;
            d[7..9].copy_from_slice(&(crate::hal::arena::ARENA_BYTES as u16).to_le_bytes());
            #[cfg(feature="voice-explore")]
            {
                unsafe extern "C" {static k6_build_id:[u8;8];}
                d[9..].copy_from_slice(unsafe{&*core::ptr::addr_of!(k6_build_id)});
            }
            reply(req,&d);
        }
        MODE => {let r=app.live_set_mode(syst,req.arg);reply(req,&[r]);}
        VFO => {let r=app.live_select_vfo(syst,req.arg);reply(req,&[r]);}
        TUNE => {
            if !idle || app.power_save_is_asleep() {
                reply(req, &[BUSY]); return;
            }
            let result = app.live_tune(syst, req.arg);
            reply(req, &[result]);
        }
        _ => reply(req, &[INVALID]),
    }
}
