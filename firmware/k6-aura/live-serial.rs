// Live Flux cable protocol v1. Each request is independent; no loader ownership,
// stack reuse, UART mode change, or reboot. Shared UART replies use CPS framing.
use super::frame;
use crate::{app::{App, Mode}, hal::{uart, wear_leveled::crc16_xmodem}};
use cortex_m::peripheral::SYST;

pub const MAGIC: &[u8; 6] = b"FLXLV1";
pub const REQUEST_LEN: usize = 14;
pub const STATUS: u8 = 0x60;
pub const READ: u8 = 0x61;
pub const TUNE: u8 = 0x62;
pub const RUN: u8 = 0x66;
pub const RUN_EVENT: u8 = 0x67;
pub const CAPS: u8 = 0x68;
pub const MODE: u8 = 0x69;
pub const VFO: u8 = 0x6a;
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
            let flags: u32 = 1 | 2 | 4 | 16 | 32
                | if cfg!(feature="spilib") {8} else {0}
                | if cfg!(k6_health_telemetry) {64} else {0};
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
