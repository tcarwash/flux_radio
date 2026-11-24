// Live Flux cable protocol v1. Each request is independent; no loader ownership,
// stack reuse or reboot. Bulk reads temporarily change UART rate. Shared UART replies use CPS framing.
use super::frame;
use crate::{app::{App, Mode}, hal::{uart, wear_leveled::crc16_xmodem}};
use cortex_m::peripheral::SYST;

pub const MAGIC: &[u8; 6] = b"FLXLV1";
pub const MAGIC_V2: &[u8; 6] = b"FLXLV2";
const V2_MAX: usize = 128;
const V2_ECHO: u8 = 0x70;
// A leased, token-bound PTT control. Never accept a latched, unbounded TX.
// Standard live-serial feature; bounded leases and existing TX policy apply.
#[cfg(k6_live_serial)]
const PTT_LEASE:u8=0x80;
#[cfg(k6_live_serial)]
const PTT_RELEASE:u8=0x81;
// 128x64 page-major screen readout and logical navigation gestures.
// No GPIO manipulation and no PTT: ordinary remote PTT is a separate API.
#[cfg(k6_remote_ui)]
const SCREENSHOT:u8=0x82;
#[cfg(k6_remote_ui)]
const VIRTUAL_KEY:u8=0x83;
#[cfg(k6_remote_ui)]
const SCREEN_PAGE:u8=0x84;
#[cfg(k6_remote_ui)]
const SCREEN_END:u8=0x85;
#[cfg(k6_live_serial)]
const PTT_LEASE_TICKS:u16=8_000; // 800 ms at 100 us/tick
// Never make a remotely keyed transmitter depend solely on main-loop polling.
// In the standard live build, a failed TIM14/vector-table installation blocks
// remote key-up even though ordinary RX and live serial remain available.
#[cfg(k6_live_serial)]
fn ptt_watchdog_ready()->bool {
    #[cfg(k6_lean_txdog)]
    {crate::hal::txdog::ready()}
    #[cfg(not(k6_lean_txdog))]
    {false}
}

#[cfg(k6_live_serial)]
static mut PTT_ACTIVE:Option<([u8;8],u16)>=None;

#[cfg(k6_live_serial)]
fn ptt_expired(since:u16, now:u16)->bool {
    now.wrapping_sub(since)>=PTT_LEASE_TICKS
}

// Called from the foreground main loop independently of serial traffic.
// A host disappearing, invalid renewals, or a held PTT cannot extend this lease.
#[cfg(k6_live_serial)]
pub fn poll_ptt(app:&mut App,syst:&mut SYST) {
    let active=unsafe{PTT_ACTIVE};
    if let Some((_,since))=active {
        if ptt_expired(since,crate::hal::uptime::now())
            || crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
            || app.mode()!=Mode::Standby || !app.is_transmitting() {
            unsafe{PTT_ACTIVE=None;}
            // Even on an abnormal state, release the radio TX path.
            app.set_ptt(syst,false);
        }
    }
}

// A debounced physical PTT transition overrides the serial transmitter.
// The next lease renewal must fail, not re-key after the operator releases.
#[cfg(k6_live_serial)]
pub fn physical_ptt_override(app:&mut App,syst:&mut SYST)->bool {
    let was=unsafe{core::ptr::replace(core::ptr::addr_of_mut!(PTT_ACTIVE),None).is_some()};
    if was {app.set_ptt(syst,false);}
    was
}

#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_BEGIN:u8=0x71;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_CHUNK:u8=0x72;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_COMMIT:u8=0x73;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_ABORT:u8=0x74;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_STATUS:u8=0x75;
#[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
const UPLOAD_PROBE:u8=0x76;
#[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
const RAM_FAST_BEGIN:u8=0x77;
#[cfg(all(feature="spilib",k6_ram0_stack))]
const RAM_READY:u8=0x78;
#[cfg(feature="spilib")]
const MODULE_CAPS:u8=0x7c;
const LIB_STAGE_MAX:usize=if cfg!(k6_ram0_balanced) {10240} else {7168};
// V2 cooperative launch and request. Neither command keys the radio.
#[cfg(feature="spilib")]
const RUN_COOP:u8=0x7e;
#[cfg(feature="spilib")]
const RUN_CANCEL:u8=0x7f;
#[cfg(all(feature="spilib",k6_ram0_stack,k6_user_store))]
const LIB_BEGIN:u8=0x79;
#[cfg(all(feature="spilib",k6_ram0_stack,k6_user_store,k6_serial_transport))]
const LIB_FAST_BEGIN:u8=0x7a;
#[cfg(all(feature="spilib",k6_ram0_stack,k6_user_store))]
const LIB_COMMIT:u8=0x7b;
// Only the main runtime/foreground dispatcher touches this boot-local cache.
// Never use the 8-bit wire sequence as a durable execution identifier.
#[cfg(all(feature="spilib",k6_ram0_stack))]
static mut RAM_LAST: Option<(u32,i32,bool,Option<i32>)> = None;

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
// Read-only stream: up to 128 x 256 B (32 KiB) at 500kbaud.
#[cfg(k6_serial_transport)]
const BURST_LONG: u8 = 0x7d;
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

// The 32-bit st7565 0.6.0 graphics buffer owns 8 x 140-byte pages,
// with an existing 1,152-byte static allocation (32 bytes spare).
// Rust's repr(Rust) places the Option<(usize,usize)> dirty bookkeeping
// FIRST: 12 bytes, then 128 actual LCD pixel bytes. The previous attempt
// used offset 0 and shifted all pixels right by 12, clipping the right edge.
// Every pixel byte is page*140 + 12 + x, bit0 = top pixel in its page.
// No second framebuffer is allocated.
// This is an intentionally pinned, compile-time-checked firmware layout:
// reject any future ST7565 representation change until revalidated.
#[cfg(k6_remote_ui)]
const _: () = assert!(core::mem::size_of::<st7565::GraphicsPageBuffer<128,8>>() == 1120);

#[cfg(k6_remote_ui)]
fn screen_capture(req: &Request) {
    // Fixed 64-byte chunks are below CPS's 96-byte frame limit.
    // Reading is serialized with the normal UI draw/flush main loop;
    // no framebuffer is modified and no SPI/MMIO read is performed.
    reply(req,&[OK,b'F',b'L',b'S',b'C',1,128,64,8,64]);
    uart::flush();
    uart::set_binary_mode(true);
    let base=core::ptr::addr_of!(crate::MODULE_PAGE_BUFFER_STORAGE).cast::<u8>();
    for page in 0..8usize {
        for half in 0..2usize {
            let chunk=page*2+half;
            let pixels=unsafe {core::slice::from_raw_parts(base.add(page*140+12+half*64),64)};
            let mut packet=[0u8;72];
            let n=frame::encode(&mut packet,SCREEN_PAGE,
                ((req.seq as u16)<<8)|(chunk as u16),pixels);
            for &byte in &packet[..n] {uart::send_byte(byte);}
        }
    }
    uart::flush();
    uart::set_binary_mode(false);
    reply(&Request{cmd:SCREEN_END,seq:req.seq,arg:0},&[OK,16]);
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
    let long = req.cmd == BURST_LONG;
    let count = if long {(req.arg >> 15) & 255} else {(req.arg >> 15) & 31};
    let rate = if long {
        // 32 KiB at 500kbaud is a bounded ~700 ms foreground pause.
        if req.arg >> 25 != 0 || ((req.arg >> 23) & 3) != 2 {
            reply(req,&[INVALID]);return;
        }
        500000
    } else {
        match req.arg >> 20 {
            0 => 115200, 1 => 192000, 2 => 500000,
            _ => {reply(req,&[INVALID]);return;}
        }
    };
    if count == 0 || count > (if long {128} else {16})
        || (rate==115200 && count>4)
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
    let mut sent=0u16;
    for i in 0..count {
        // Only RX is permitted. Incoming bytes during a burst are not commands.
        if app.radio_mut().ptt_asserted()
            || crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
            || uart::take_byte().is_some() {
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
    // V1 legacy completion unchanged; extended stream count uses u16.
    if long {
        let mut end=[0u8;19];
        end[0]=result;
        end[1..3].copy_from_slice(&sent.to_le_bytes());
        for i in 0..4 {
            end[3+i*4..7+i*4].copy_from_slice(&after[i].wrapping_sub(prior[i]).to_le_bytes());
        }
        reply(&Request{cmd:BURST_END,seq:req.seq,arg:0},&end);
    } else {
        let mut end=[0u8;18];
        end[0]=result;end[1]=sent as u8;
        for i in 0..4 {
            end[2+i*4..6+i*4].copy_from_slice(&after[i].wrapping_sub(prior[i]).to_le_bytes());
        }
        reply(&Request{cmd:BURST_END,seq:req.seq,arg:0},&end);
    }
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

// A synchronous native module never services normal serial dispatch.
// Only an opt-in API-25 module calls cancel_poll; it consumes a bounded
// number of bytes from the UART ring and recognizes exactly one 8-byte
// capability token in the canonical V2 frame. No forced stack unwind,
// interruption of a TX frame, or implicit retry is permitted.
#[cfg(feature="spilib")]
const CANCEL_RAW:usize=18; // cmd, seq, length, arg, token, CRC
#[cfg(feature="spilib")]
struct CancelState {
    armed:bool, seq:u8, token:[u8;8], accepted:bool,
    prefix:usize, n:usize, symbols:usize, acc:u16, bits:u8,
    raw:[u8;CANCEL_RAW],
}
#[cfg(feature="spilib")]
impl CancelState {
    const fn empty()->Self {
        Self {armed:false,seq:0,token:[0;8],accepted:false,
              prefix:0,n:0,symbols:0,acc:0,bits:0,raw:[0;CANCEL_RAW]}
    }
    fn reset_frame(&mut self) {
        self.prefix=0;self.n=0;self.symbols=0;self.acc=0;self.bits=0;
    }
    fn byte(&mut self,byte:u8)->bool {
        if self.prefix < MAGIC_V2.len() {
            self.prefix=if byte==MAGIC_V2[self.prefix] {
                self.prefix+1
            } else if byte==MAGIC_V2[0] {1} else {0};
            return false;
        }
        if byte==0 {
            let valid=self.n==CANCEL_RAW && self.symbols==21
                && self.acc==0 && self.raw[0]==RUN_CANCEL
                && self.raw[1]==self.seq && self.raw[2..4]==[8,0]
                && self.raw[4..8]==[0,0,0,0]
                && self.raw[8..16]==self.token;
            let mut crc_bytes=[0u8;6+16];
            crc_bytes[..6].copy_from_slice(MAGIC_V2);
            crc_bytes[6..].copy_from_slice(&self.raw[..16]);
            let ok=valid && crc16_xmodem(&crc_bytes)
                ==u16::from_be_bytes([self.raw[16],self.raw[17]]);
            self.reset_frame();
            return ok;
        }
        if byte&0x80==0 || self.symbols>=21 {
            self.reset_frame();
            return false;
        }
        self.symbols+=1;
        self.acc|=u16::from(byte&127)<<self.bits;
        self.bits+=7;
        if self.bits>=8 {
            if self.n==CANCEL_RAW {
                self.reset_frame();
                return false;
            }
            self.raw[self.n]=self.acc as u8;
            self.n+=1;
            self.acc>>=8;self.bits-=8;
        }
        false
    }
}
#[cfg(feature="spilib")]
static mut CANCEL:CancelState=CancelState::empty();

#[cfg(feature="spilib")]
fn cancel_arm(seq:u8,token:[u8;8]){
    unsafe {
        CANCEL=CancelState::empty();
        CANCEL.armed=true;
        CANCEL.seq=seq;
        CANCEL.token=token;
    }
}

#[cfg(feature="spilib")]
fn cancel_disarm(){
    unsafe {CANCEL=CancelState::empty();}
}

/// Return -1 if no token-bound launch (or UART is in exclusive KISS
/// mode), 0 if still active, 1 only after a valid matching token frame.
/// Never call the full HostHandshake while a foreground module runs.
#[cfg(feature="spilib")]
pub fn cancel_poll()->i32 {
    if uart::binary_mode(){return -1;}
    let state=unsafe{&mut *core::ptr::addr_of_mut!(CANCEL)};
    if !state.armed{return -1;}
    if state.accepted{return 1;}
    // Bound caller latency even if a hostile UART producer streams forever.
    // A full cancel packet is only 28 bytes on the wire.
    for _ in 0..64 {
        let Some(b)=uart::take_byte() else {break;};
        if state.byte(b) {
            state.accepted=true;
            reply(&Request{cmd:RUN_CANCEL,seq:state.seq,arg:0},&[OK]);
            return 1;
        }
    }
    0
}

// RAM staging is a single bounded exchange on the borrowed arena stack.
// Keep the lower 4 KiB for code and at least 4 KiB for this stack/interrupts.
#[cfg(all(feature="spilib",k6_ram0_stack))]
fn ram_allowed(app:&mut App)->bool {
    !app.radio_mut().ptt_asserted() &&
        !crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
}

#[cfg(all(feature="spilib",k6_ram0_stack))]
fn ram_upload(app:&mut App, req:&Request, payload:&[u8], fast:bool, library:bool)->Option<(u8,i32)> {
    use crate::hal::{arena,uptime};
    let n=req.arg as usize;
    if !payload.is_empty() || n<25 || n>(if library {LIB_STAGE_MAX} else {4096}) || n+4096>arena::ARENA_BYTES {
        reply(req,&[INVALID]);return None;
    }
    if !ram_allowed(app) || uart::binary_mode() || !app.live_ram_begin() {
        reply(req,&[BUSY]);return None;
    }
    uart::set_binary_mode(true);
    let mut offset=0usize;
    if fast {
        #[cfg(k6_serial_transport)]
        {
            if !fast_receive(app,req,n,false) {
                uart::set_binary_mode(false);
                app.live_ram_abort();
                return None;
            }
            offset=n;
        }
        #[cfg(not(k6_serial_transport))]
        {unreachable!();}
    } else {
        reply(req,&[OK]);
    }
    let t=uptime::now();
    let mut job=None;
    let mut ready=!fast;
    // Per-frame prefix deadline 500 ms, whole transaction <3.5 s plus
    // at most 100 ms for a frame body. No other request is dispatched here.
    'upload: loop {
        let frame_start=uptime::now();
        // PA10 is both UART RX and PTT. Individual serial start/data
        // bits pull it low. Never interpret an instantaneous low as PTT
        // while waiting for the incoming prefix. A continuous low for
        // 10 ms is a physical hold (or break), not valid 115200 UART.
        let mut low_since=None;
        for &want in MAGIC_V2 {
            loop {
                let now=uptime::now();
                if crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
                    || now.wrapping_sub(t)>=35000
                    || now.wrapping_sub(frame_start)>=5000 {break 'upload;}
                if app.radio_mut().ptt_asserted() {
                    let since=*low_since.get_or_insert(now);
                    if now.wrapping_sub(since)>=100 {break 'upload;}
                } else {
                    low_since=None;
                }
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
            RAM_CHUNK if !fast && r.arg as usize==offset && !p.is_empty() && offset+p.len()<=n => {
                unsafe{core::ptr::copy_nonoverlapping(p.as_ptr(),arena::base().add(offset),p.len());}
                offset+=p.len();reply(&r,&[OK]);
            }
            RAM_READY if fast && !ready && r.arg==n as u32 && p.is_empty()=>{ready=true;reply(&r,&[OK]);}
            RAM_ABORT if r.arg==0 && p.is_empty()=>{reply(&r,&[OK]);break;}
            #[cfg(k6_user_store)]
            LIB_COMMIT if library && ready && r.arg as usize==n && offset==n && p.is_empty()=>{
                let result=app.live_lib_commit(n);
                let mut d=[0u8;5];d[1..].copy_from_slice(&result.to_le_bytes());
                reply(&r,&d);
                break;
            }
            RAM_COMMIT if !library && ready && r.arg as usize==n && offset==n && p.is_empty()=>{
                unsafe extern "C" {fn km_live_verify(a:*const u8,n:u32)->i32;}
                // The verifier moves code over the envelope; read its serial first.
                let serial=unsafe{u32::from_le_bytes(core::ptr::read_unaligned(
                    arena::base().add(8).cast::<[u8;4]>()))};
                let checked=unsafe{km_live_verify(arena::base(),n as u32)};
                if checked>0 {unsafe{RAM_LAST=Some((serial,checked,false,None));}}
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

// Fast uplink is strictly a data-only phase. A frame consists of the V2
// high-bit body and NUL (no ASCII prefix). Only one <=160-byte frame may
// be outstanding. The radio restores 115200 before accepting COMMIT.
#[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
fn fast_receive(app:&mut App,req:&Request,n:usize,probe:bool)->bool {
    use crate::hal::{arena,uptime,clock::SYSCLK_HZ};
    const RATE:u32=192000;
    let prior=uart::cable_stats();
    let mut ack=[0u8;5];
    ack[1..].copy_from_slice(&(SYSCLK_HZ/((SYSCLK_HZ+RATE/2)/RATE)).to_le_bytes());
    reply(req,&ack);
    let t=uptime::now();
    while uptime::now().wrapping_sub(t)<300 {}
    uart::cable_baud(RATE);
    let start=uptime::now();
    let mut offset=0usize;
    let mut seq=req.seq;
    while offset<n {
        if !ram_allowed(app) || uptime::now().wrapping_sub(start)>=35000 {break;}
        let Some(f)=receive_v2() else {break;};
        let r=f.req();
        let p=f.payload();
        seq=seq.wrapping_add(1);
        let size=(n-offset).min(V2_MAX);
        if !ram_allowed(app) || r.cmd!=RAM_CHUNK || r.seq!=seq ||
            r.arg as usize!=offset || p.len()!=size ||
            uart::cable_stats()!=prior ||
            (probe && p.iter().enumerate().any(|(i,&b)| {
                let j=offset+i;
                b!=(j*73+j/256) as u8
            })) {
            reply(&r,&[IO]);break;
        }
        if !probe {
            unsafe{core::ptr::copy_nonoverlapping(p.as_ptr(),arena::base().add(offset),size);}
        }
        offset+=size;
        reply(&r,&[OK]);
    }
    let ok=offset==n;
    // A compliant sender stops after its one outstanding frame on error.
    // Drain that bounded tail at the fast rate, before restoring baseline.
    if !ok {
        let t=uptime::now();
        while uptime::now().wrapping_sub(t)<300 {let _=uart::take_byte();}
    }
    uart::cable_baud(115200);
    for _ in 0..320 {if uart::take_byte().is_none(){break;}}
    ok
}

#[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
fn upload_probe(app:&mut App,req:&Request,payload:&[u8]) {
    if !payload.is_empty() || req.arg!=4096 {reply(req,&[INVALID]);return;}
    if !ram_allowed(app) || !app.live_bulk_idle() || uart::binary_mode() {
        reply(req,&[BUSY]);return;
    }
    uart::set_binary_mode(true);
    let _=fast_receive(app,req,4096,true);
    uart::set_binary_mode(false);
}

struct V2Args {app:*mut App,syst:*mut SYST,job:Option<(u8,i32)>}
unsafe extern "C" fn dispatch_v2_on_stack(raw: *mut core::ffi::c_void) {
    let Some(f)=receive_v2() else {return;};
    let req=f.req();
    let args=unsafe{&mut *raw.cast::<V2Args>()};
    match req.cmd {
        V2_ECHO if req.arg==0 => reply_v2(&req,f.payload()),
        #[cfg(k6_live_serial)]
        PTT_LEASE if req.arg==800 && f.payload().len()==8
            && f.payload().iter().any(|&b|b!=0)=>{
            let app=unsafe{&mut *args.app};
            let mut token=[0u8;8];token.copy_from_slice(f.payload());
            let state=unsafe{PTT_ACTIVE};
            let now=crate::hal::uptime::now();
            let status=if let Some((owner,since))=state {
                if ptt_expired(since,now) || owner!=token || !app.is_transmitting() {
                    BUSY
                } else {
                    unsafe{PTT_ACTIVE=Some((owner,now));}
                    OK
                }
            } else if !ptt_watchdog_ready() || !app.live_cat_idle() || app.power_save_is_asleep()
                || app.side_is_channel_mode(app.master_index())
                || app.live_modulation(app.master_index())!=0 {
                BUSY
            } else {
                app.set_ptt(unsafe{&mut *args.syst},true);
                if app.is_transmitting() {
                    // Independent RF watchdog in addition to 800 ms main-loop lease.
                    // Existing keyed() arms it before this point.
                    crate::hal::txdog::limit(1_500);
                    unsafe{PTT_ACTIVE=Some((token,now));}
                    OK
                } else {BUSY}
            };
            reply(&req,&[status]);
        }
        #[cfg(k6_live_serial)]
        PTT_RELEASE if req.arg==0 && f.payload().len()==8 => {
            let app=unsafe{&mut *args.app};
            let state=unsafe{PTT_ACTIVE};
            let status=if let Some((owner,_))=state {
                if owner!=f.payload() {BUSY}
                else {
                    unsafe{PTT_ACTIVE=None;}
                    app.set_ptt(unsafe{&mut *args.syst},false);
                    if app.is_transmitting(){IO}else{OK}
                }
            } else {OK}; // already released (idempotent de-key)
            reply(&req,&[status]);
        }

        #[cfg(all(feature="spilib",k6_ram0_stack))]
        RAM_BEGIN=>{args.job=ram_upload(unsafe{&mut *args.app},&req,f.payload(),false,false);}
        #[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
        RAM_FAST_BEGIN=>{args.job=ram_upload(unsafe{&mut *args.app},&req,f.payload(),true,false);}
        #[cfg(all(feature="spilib",k6_ram0_stack,k6_serial_transport))]
        UPLOAD_PROBE=>upload_probe(unsafe{&mut *args.app},&req,f.payload()),
        #[cfg(all(feature="spilib",k6_ram0_stack,k6_user_store))]
        LIB_BEGIN=>{args.job=ram_upload(unsafe{&mut *args.app},&req,f.payload(),false,true);}
        #[cfg(all(feature="spilib",k6_ram0_stack,k6_user_store,k6_serial_transport))]
        LIB_FAST_BEGIN=>{args.job=ram_upload(unsafe{&mut *args.app},&req,f.payload(),true,true);}
        #[cfg(feature="spilib")]
        RUN_COOP if req.arg & !0x11f == 0 && f.payload().len()==8
            && f.payload().iter().any(|&b|b!=0) => {
            let app=unsafe{&mut *args.app};
            if !app.live_module_idle() {
                reply(&req,&[BUSY]);
            } else {
                let status=app.live_queue_module(req.arg as u8,req.seq,req.arg&0x100!=0);
                if status==OK {
                    let mut token=[0u8;8];
                    token.copy_from_slice(f.payload());
                    cancel_arm(req.seq,token);
                    reply_v2(&req,&[]);
                    uart::flush();
                } else {
                    reply(&req,&[status]);
                }
            }
        }
        #[cfg(feature="spilib")]
        MODULE_CAPS if req.arg==0 && (f.payload().is_empty() || f.payload().len()==16)=>{
            let record=super::module_capability_record();
            if f.payload().is_empty() {reply_v2(&req,&record);}
            else {
                unsafe extern "C" {fn km_capability_proof(record:*const u8,nonce:*const u8,tag:*mut u8)->i32;}
                let mut d=[0u8;25];
                d[..16].copy_from_slice(&record);
                d[16]=unsafe{km_capability_proof(record.as_ptr(),f.payload().as_ptr(),d[17..].as_mut_ptr())} as u8;
                reply_v2(&req,&d);
            }
        }
        _=>reply(&req,&[INVALID]),
    }
}

#[inline(never)]
pub fn dispatch_v2(app:&mut App,syst:&mut SYST) {
    unsafe extern "C" {
        fn km_call_on_stack(top:*mut u8,f:unsafe extern "C" fn(*mut core::ffi::c_void),arg:*mut core::ffi::c_void);
    }
    let mut args=V2Args{app,syst,job:None};
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
    #[cfg(feature="spilib")]
    cancel_disarm();
    #[cfg(all(feature="spilib",k6_ram0_stack))]
    if slot==255 {
        unsafe {
            if let Some((id,_,false,_))=RAM_LAST {
                RAM_LAST=Some((id,load,true,result));
            }
        }
    }
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
    // The CPU can remain awake after the switch opens. Never advertise
    // that interval as an idle, safe-to-launch radio state.
    let idle = app.mode() == Mode::Standby && !app.is_transmitting()
        && !crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()})
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
                | if cfg!(all(feature="spilib",k6_ram0_stack)) {1024 | 2048} else {0}
                | if cfg!(all(feature="spilib",k6_ram0_stack,k6_serial_transport)) {4096} else {0}
                | if cfg!(all(feature="spilib",k6_ram0_stack,k6_user_store)) {8192 | 32768} else {0}
                | if cfg!(feature="spilib") {16384} else {0}
                | if cfg!(k6_serial_transport) {65536} else {0}
                | if cfg!(feature="spilib") {131072} else {0}
                | if cfg!(all(k6_live_serial,k6_lean_txdog)) {262144} else {0}
                | if cfg!(k6_remote_ui) {524288 | 1048576} else {0}
                | if cfg!(all(feature="spilib",k6_ram0_balanced,k6_user_store)) {2097152} else {0};
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
        BURST | BURST_LONG => spi_burst(app,req),
        #[cfg(all(feature="spilib",k6_ram0_stack))]
        WATERMARK if req.arg==0 => {
            unsafe extern "C" {fn km_serial_highwater()->u32;}
            let mut d=[0u8;5];
            d[1..].copy_from_slice(&unsafe{km_serial_highwater()}.to_le_bytes());
            reply(req,&d);
        }
        #[cfg(all(feature="spilib",k6_ram0_stack))]
        RAM_STATUS if req.arg!=0 => {
            let mut d=[0u8;11];
            if let Some((id,load,done,result))=unsafe{RAM_LAST} {
                if id==req.arg {
                    d[1]=if done {2} else {1};
                    d[2..6].copy_from_slice(&load.to_le_bytes());
                    if let Some(ret)=result {
                        d[6]=1;
                        d[7..11].copy_from_slice(&ret.to_le_bytes());
                    }
                }
            }
            reply(req,&d);
        }
        INFO if req.arg==0 => {
            let mut d=[0u8;17];
            d[1..5].copy_from_slice(b"BLWF");
            d[5]=if cfg!(k6_user_store) {5} else if cfg!(k6_designer) {4} else {2};
            d[6]=32;
            d[7..9].copy_from_slice(&(crate::hal::arena::ARENA_BYTES as u16).to_le_bytes());
            #[cfg(any(feature="voice-explore",feature="spilib"))]
            {
                unsafe extern "C" {static k6_build_id:[u8;8];}
                d[9..].copy_from_slice(unsafe{&*core::ptr::addr_of!(k6_build_id)});
            }
            reply(req,&d);
        }
        #[cfg(k6_remote_ui)]
        SCREENSHOT if req.arg==0 => {
            // Screenshot is passive and works inside menus, unlike CAT tune.
            if app.is_transmitting() || app.power_save_is_asleep()
                || crate::board::power_switch_off(unsafe{&*kd32f328_pac::Gpioa::ptr()}) {
                reply(req,&[BUSY]);
            } else {screen_capture(req);}
        }
        #[cfg(k6_remote_ui)]
        VIRTUAL_KEY => {let r=app.live_virtual_key(syst,req.arg);reply(req,&[r]);}
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
