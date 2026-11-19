#!/usr/bin/env python3
"""Opt-in cable-only UART experiments; reset always restores the normal baud."""
from pathlib import Path
work = Path(__file__).parent / 'build/aura-fd6818b-port'
uart = work / 'src/hal/uart.rs'
s = uart.read_text()
marker = '// Flux serial lab: temporary baud; never persisted.'
if marker not in s:
    s += '''
// Flux serial lab: temporary baud; never persisted.
#[cfg(any(k6_serial_lab, k6_serial_transport))]
pub fn cable_baud(baud: u32) -> u32 {
    let raw = (crate::hal::clock::SYSCLK_HZ + baud / 2) / baud;
    flush();
    let regs = unsafe { &*kd32f328_pac::Usart1::ptr() };
    while regs.isr().read().tc().bit_is_clear() {}
    cortex_m::interrupt::free(|_| {
        regs.cr1().modify(|_, w| w.ue().clear_bit());
        regs.brr().write(|w| unsafe {
            w.div_mantissa().bits((raw >> 4) as u16);
            w.div_fraction().bits((raw & 15) as u8)
        });
        regs.cr1().modify(|_, w| w.ue().set_bit());
    });
    crate::hal::clock::SYSCLK_HZ / raw
}
'''
    uart.write_text(s)
p = work / 'src/cps/mod.rs'
s = p.read_text()
anchor = '            CMD_CABLE_INFO => {\n'
if '// Flux serial lab commands' in s:
    a = s.index('            // Flux serial lab commands')
    b = s.index(anchor, a)
    s = s[:a] + s[b:]
if '// Flux serial lab commands' not in s:
    if s.count(anchor) != 1: raise SystemExit('cable dispatcher not unique')
    s = s.replace(anchor, '''            // Flux serial lab commands: only available in an opt-in test build.
            // b: ACK at old speed, 30 ms guard, then change; idle reset recovers 115200.
            #[cfg(any(k6_serial_lab, k6_serial_transport))]
            0x62 if f.addr == 0 && f.len == 4 => {
                let baud = u32::from_le_bytes([f.data[0], f.data[1], f.data[2], f.data[3]]);
                if !(115200..=3000000).contains(&baud) {
                    cable_send(frame::CMD_ERROR, f.addr, &[frame::ERR_BAD_LEN]);
                    continue;
                }
                let divisor = (SYSCLK_HZ + baud / 2) / baud;
                cable_send(0x62, 0, &(SYSCLK_HZ / divisor).to_le_bytes());
                uart::flush();
                delay::ms(syst, 30);
                syst.clear_current();
                syst.enable_counter();
                uart::cable_baud(baud);
                reader = frame::FrameReader::new();
            }
            #[cfg(any(k6_serial_lab, k6_serial_transport))]
            0x71 if f.len == 0 => {
                let stats = uart::cable_stats();
                let mut data = [0u8; 16];
                for i in 0..4 { data[i*4..i*4+4].copy_from_slice(&stats[i].to_le_bytes()); }
                cable_send(0x71, f.addr, &data);
            }
            // Read-only health snapshot: raw ADC sums (16 samples each), battery cV.
            #[cfg(k6_health_telemetry)]
            0x68 if f.addr == 0 && f.len == 0 => {
                let (t,v)=crate::app::App::serial_lab_temp(syst);
                let mut data=[0u8;12];
                data[..4].copy_from_slice(&t.to_le_bytes());
                data[4..8].copy_from_slice(&v.to_le_bytes());
                data[8..12].copy_from_slice(&(app.battery_voltage_cv() as u32).to_le_bytes());
                cable_send(0x68,0,&data);
            }
            // x: one experimental native FSK frame, followed by CW identification.
            #[cfg(all(k6_serial_lab, not(k6_no_fsk_rx)))]
            0x79 if f.addr == 0 && f.len == 9 && cfg!(feature = "fd6818b") => {
                let hz=u32::from_le_bytes([f.data[0],f.data[1],f.data[2],f.data[3]]);
                let result=app.serial_lab_fsk(syst,hz,f.data[4],u16::from_le_bytes([f.data[5],f.data[6]]),f.data[7],f.data[8]);
                let mut out=[0u8;28];
                for (i,v) in result.iter().enumerate(){out[i*4..i*4+4].copy_from_slice(&v.to_le_bytes());}
                cable_send(0x79,0,&out);
                syst.set_reload(SYSCLK_HZ/1000-1); syst.clear_current(); syst.enable_counter();
            }
            // p: bounded receive-only GP0 acquisition; IQ output frames use 'i'.
            #[cfg(any(k6_serial_lab, k6_serial_iq))]
            0x70 if f.len == 16 && cfg!(feature = "fd6818b") => {
                let get = |n: usize| u32::from_le_bytes([f.data[n],f.data[n+1],f.data[n+2],f.data[n+3]]);
                let hz=get(0); let ms=get(4); let mix=get(8);
                let mode=f.data[12]; let ratio=f.data[13];
                if !(50000000..=600000000).contains(&hz) || ms<10 || ms>10000 || mix>40000 || mode>1 || f.data[14]!=0 || f.data[15]!=0 || ratio==0 || ratio>32 || !ratio.is_power_of_two() {
                    cable_send(frame::CMD_ERROR,f.addr,&[frame::ERR_BAD_LEN]);continue;
                }
                let mut saved=[0u16;3];
                for (i,r) in [0x33,0x34,0x73].iter().enumerate() { saved[i]=app.radio.fd6818.reg_read(syst,*r); }
                app.radio.fd6818.reg_write(syst,0x33,saved[0]&!0x100);
                app.radio.fd6818.reg_write(syst,0x34,(saved[1]&0xfff0)|13);
                app.radio.fd6818.reg_write(syst,0x73,saved[2]|0x10);
                app.radio.retune_rx(syst,hz,true);
                delay::ms(syst,50);
                let rssi=app.radio.fd6818.reg_read(syst,0x67);
                let r3d=app.radio.fd6818.reg_read(syst,0x3d);
                let r43=app.radio.fd6818.reg_read(syst,0x43);
                let route=app.radio.fd6818.reg_read(syst,0x34);
                let mut stats=[0u32;24];
                unsafe extern "C" { fn k6_serial_if_lab(mem:*mut u8,bytes:u32,ms:u32,mode:u32,ratio:u32,mix:u32,stats:*mut u32)->i32; }
                let result=if route&15!=13 { -9 } else { unsafe { k6_serial_if_lab(crate::hal::arena::base(),crate::hal::arena::ARENA_BYTES as u32,ms,mode as u32,ratio as u32,(((mix as u64)<<32)/96000000) as u32,stats.as_mut_ptr()) } };
                app.radio.rf_off(syst);
                for (i,r) in [0x33,0x34,0x73].iter().enumerate() { app.radio.fd6818.reg_write(syst,*r,saved[i]); }
                let mut data=[0u8;112];
                stats[0]=result as u32;
                for i in 0..24 { data[i*4..i*4+4].copy_from_slice(&stats[i].to_le_bytes()); }
                data[96..100].copy_from_slice(&hz.to_le_bytes());
                data[100..104].copy_from_slice(&mix.to_le_bytes());
                data[104..106].copy_from_slice(&rssi.to_le_bytes());
                data[106..108].copy_from_slice(&r3d.to_le_bytes());
                data[108..110].copy_from_slice(&r43.to_le_bytes());
                data[110..112].copy_from_slice(&route.to_le_bytes());
                cable_send(0x70,f.addr,&data);
                syst.set_reload(SYSCLK_HZ/1000-1);syst.clear_current();syst.enable_counter();
            }
            // d: bounded read-only physical SPI burst, 256 bytes per response.
            #[cfg(k6_serial_lab)]
            0x64 if f.len == 2 => {
                let count = u16::from_le_bytes([f.data[0], f.data[1]]) as u32;
                let start = f.addr as u32 * 64;
                if count == 0 || count > 1024 || start + count * 256 > FLASH_CHIP_SIZE {
                    cable_send(frame::CMD_ERROR, f.addr, &[frame::ERR_BAD_ADDR]);
                    continue;
                }
                let mut data = [0u8; 256];
                for block in 0..count {
                    let offset = start + block * 256;
                    app.storage_mut().read_raw(offset, &mut data);
                    cable_send(0x64, (offset / 64) as u16, &data);
                }
            }
            // T: CRC-protected arbitrary binary echo, exercises both cable directions.
            #[cfg(k6_serial_lab)]
            0x54 => cable_send(0x54, f.addr, &f.data[..f.len]),
            // t: one request -> N independently CRC-protected 256-byte pattern frames.
            // Bounded to 256 KiB so a host disconnect cannot leave a long-running sender.
            #[cfg(k6_serial_lab)]
            0x74 if f.len == 2 => {
                let count = u16::from_le_bytes([f.data[0], f.data[1]]);
                if count == 0 || count > 1024 {
                    cable_send(frame::CMD_ERROR, f.addr, &[frame::ERR_BAD_LEN]);
                    continue;
                }
                let mut data = [0u8; 256];
                for block in 0..count {
                    let seq = f.addr.wrapping_add(block);
                    for (i, byte) in data.iter_mut().enumerate() {
                        *byte = (i as u8).wrapping_mul(73) ^ (seq as u8) ^ (seq >> 8) as u8;
                    }
                    cable_send(0x74, seq, &data);
                }
            }
''' + anchor, 1)
    p.write_text(s)
# The production sender only needs 64 bytes; lab echo/stream frames need more.
s = p.read_text()
old = '    let mut out = [0u8; 8 + FLASH_RAW_CHUNK as usize];'
new = '''    #[cfg(any(k6_serial_lab, k6_serial_iq))]
    let mut out = [0u8; 264];
    #[cfg(not(any(k6_serial_lab, k6_serial_iq)))]
    let mut out = [0u8; 8 + FLASH_RAW_CHUNK as usize];'''
if new not in s:
    previous = new.replace('any(k6_serial_lab, k6_serial_iq)', 'k6_serial_lab')
    if previous in s: old = previous
    if s.count(old) != 1: raise SystemExit('cable sender not unique')
    p.write_text(s.replace(old, new, 1))
# CRC the encoded response directly: inbound frame_crc has a 96-byte scratch limit.
p = work / 'src/cps/frame.rs'
s = p.read_text()
old = '    let crc = frame_crc(cmd, addr, data);'
new = '    let crc = crc16_xmodem(&out[1..6 + data.len()]);'
if new not in s:
    if s.count(old) != 1: raise SystemExit('response CRC anchor not unique')
    p.write_text(s.replace(old, new, 1))

# Count hardware overruns, framing/noise errors and software ring drops separately.
p = uart
s = p.read_text()
if 'fn cable_stats()' not in s:
    s = s.replace('struct RxRing {', 'struct RxRing {\n    #[cfg(any(k6_serial_lab, k6_serial_transport))]\n    errors: [u32; 4],', 1)
    s = s.replace('        RxRing {', '        RxRing {\n            #[cfg(any(k6_serial_lab, k6_serial_transport))]\n            errors: [0; 4],', 1)
    s = s.replace('        if self.len == RX_RING_CAP {', '        if self.len == RX_RING_CAP {\n            #[cfg(any(k6_serial_lab, k6_serial_transport))]\n            { self.errors[3] = self.errors[3].wrapping_add(1); }', 1)
    anchor = '    if isr.rxne().bit_is_set() {'
    insert = """    #[cfg(any(k6_serial_lab, k6_serial_transport))]
    if isr.bits() & 14 != 0 {
        cortex_m::interrupt::free(|cs| {
            let mut r = RX_RING.borrow(cs).borrow_mut();
            r.errors[0] = r.errors[0].wrapping_add(isr.ore().bit_is_set() as u32);
            r.errors[1] = r.errors[1].wrapping_add(isr.fe().bit_is_set() as u32);
            r.errors[2] = r.errors[2].wrapping_add(isr.nf().bit_is_set() as u32);
        });
        regs.icr().write(|w| w.orecf().set_bit().fecf().set_bit().ncf().set_bit());
    }
"""
    if s.count(anchor) != 1: raise SystemExit('RX IRQ anchor not unique')
    s = s.replace(anchor, insert + anchor, 1)
    s += """
#[cfg(any(k6_serial_lab, k6_serial_transport))]
pub fn cable_stats() -> [u32; 4] {
    cortex_m::interrupt::free(|cs| RX_RING.borrow(cs).borrow().errors)
}
"""
    p.write_text(s)

# A nibble lookup keeps CRC computation from dominating a fast streaming link.
p = work / 'src/hal/wear_leveled.rs'
s = p.read_text()
marker = '// Flux serial lab nibble CRC'
if marker not in s:
    s = s.replace('pub(crate) fn crc16_xmodem(', '#[cfg(not(any(k6_serial_lab, k6_serial_transport)))]\npub(crate) fn crc16_xmodem(', 1)
    insert = """
// Flux serial lab nibble CRC (same CRC-16/XMODEM, initial value zero).
#[cfg(any(k6_serial_lab, k6_serial_transport))]
pub(crate) fn crc16_xmodem(data: &[u8]) -> u16 {
    const TABLE: [u16; 16] = [
        0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
        0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef,
    ];
    let mut crc = 0u16;
    for &byte in data {
        crc = (crc << 4) ^ TABLE[((crc >> 12) as u8 ^ (byte >> 4)) as usize];
        crc = (crc << 4) ^ TABLE[((crc >> 12) as u8 ^ (byte & 15)) as usize];
    }
    crc
}
"""
    anchor = 'const CLEAR_MASK:'
    if s.count(anchor) != 1: raise SystemExit('CRC helper anchor not unique')
    s = s.replace(anchor, insert + '\n' + anchor, 1)
    p.write_text(s)

# Let the opt-in C acquisition queue service TXE without blocking the DMA drain loop.
p = uart
s = p.read_text()
if '// Serial IF lab TX interrupt' not in s:
    anchor = '    #[cfg(not(feature = "lean"))]\n    if isr.txe().bit_is_set()'
    insert = """    // Serial IF lab TX interrupt; RX has already been drained above.
    #[cfg(any(k6_serial_lab, k6_serial_iq))]
    if isr.txe().bit_is_set() && regs.cr1().read().txeie().bit_is_set() {
        unsafe extern "C" { fn k6_serial_if_tx_irq() -> i32; }
        if unsafe { k6_serial_if_tx_irq() } != 0 { return; }
    }
"""
    if s.count(anchor) != 1: raise SystemExit('UART TX IRQ anchor not unique')
    p.write_text(s.replace(anchor, insert + anchor, 1))

# Test-only RF command; normal firmware omits this method.
p = work / 'src/app/mod.rs'
s = p.read_text()
if 'pub fn serial_lab_fsk(' in s:
    a=s.index('    #[cfg(all(k6_serial_lab, not(k6_no_fsk_rx), feature = "fd6818b"))]\n    pub fn serial_lab_fsk(')
    b=s.index('    #[cfg(feature = "spilib")]\n    pub fn cable_begin',a)
    s=s[:a]+s[b:]
if 'pub fn serial_lab_fsk(' not in s:
    anchor = '    pub fn cable_begin(&mut self) {'
    # Insert ahead of cable_begin's existing cfg attribute.
    anchor = '    #[cfg(feature = "spilib")]\n' + anchor
    assert s.count(anchor) == 1
    s = s.replace(anchor, """    #[cfg(all(k6_serial_lab, not(k6_no_fsk_rx), feature = "fd6818b"))]
    pub fn serial_lab_fsk(&mut self, syst: &mut SYST, hz: u32, target: u8, baudword: u16, nwords: u8, gain: u8) -> [i32;7] {
        use crate::hal::{delay, txdog};
        let mut out=[0i32;7];
        if hz != 433_500_000 || nwords==0 || nwords>128 || gain<16 || gain>96 || baudword<0x3065 || self.settings.tx_forbid {out[0]=-1;return out;}
        let (t0,v0)=Self::serial_lab_temp(syst);
        out[1]=t0;out[2]=v0;out[3]=self.radio.read_battery_raw() as i32;
        self.radio.set_frequency(hz);
        self.radio.set_tx_frequency(hz);
        self.radio.set_power(Power::Low);
        self.radio.set_subaudio_tx(SubAudio::None);
        self.radio.set_modulation(Modulation::Fm);
        self.radio.set_scramble_level(syst, 0);
        self.radio.set_tx_allowed(BandLock::from_u8(self.settings.band_lock).tx_ranges());
        let Some(factory)=self.storage.read_pa_calibration(hz,Power::Low) else {out[0]=-2;return out;};
        if factory==0 || factory==255 || target>factory {out[0]=-2;return out;}
        self.radio.fd6818.set_pa_calibration(target);
        if !self.radio.enter_tx(syst) {out[0]=-3;return out;}
        txdog::limit(9000);
        self.radio.fd6818.reg_write(syst,0x30,0xC1FA);
        out[4]=self.radio.read_battery_raw() as i32;
        let fd=&mut self.radio.fd6818;
        for (r,v) in [(0x3f,0x8000),(0x58,0x00e1),(0x70,0x0080|gain as u16),(0x72,baudword),(0x5a,0x076b),(0x5b,0x3e36),(0x5c,0x5625),(0x5d,((nwords as u16*2-1)<<8)),(0x59,0x80f8),(0x59,0x00f8)] {fd.reg_write(syst,r,v);}
        let mut x=0xace1u16;
        let mut crc=0xffffu16;
        for _ in 1..nwords {
            for b in x.to_le_bytes() {crc^=(b as u16)<<8;for _ in 0..8 {crc=if crc&0x8000!=0 {(crc<<1)^0x1021} else {crc<<1};}}
            fd.reg_write(syst,0x5f,x); x=(x>>1)^if x&1!=0 {0xb400} else {0};
        }
        fd.reg_write(syst,0x5f,crc.swap_bytes());
        fd.reg_write(syst,0x59,0x08f8);
        let mut ok=false;
        for _ in 0..420 {delay::ms(syst,5);if fd.reg_read(syst,0x0c)&1!=0 {ok=true;break;}}
        for (r,v) in [(0x02,0),(0x3f,0),(0x59,0x00f8)] {fd.reg_write(syst,r,v);}
        self.radio.fd6818.reg_write(syst,0x70,0);
        self.radio.fd6818.reg_write(syst,0x30,0xC1FA);
        self.radio.fd6818.reg_write(syst,0x71,8260);
        delay::ms(syst,180);
        self.radio.fd6818.reg_write(syst,0x30,0xC1FA);
        for ch in [b".-".as_slice(),b"--.",b"--...",b"...",b"..-"] {
            for &symbol in ch {
                self.radio.fd6818.reg_write(syst,0x70,0xBC00);
                delay::ms(syst,if symbol==b'-' {180} else {60});
                self.radio.fd6818.reg_write(syst,0x70,0);
                delay::ms(syst,60);
            }
            delay::ms(syst,120);
        }
        self.radio.fd6818.reg_write(syst,0x71,0);
        self.radio.enter_rx(syst);
        self.radio.rf_off(syst);
        let (t1,v1)=Self::serial_lab_temp(syst);out[5]=t1;out[6]=v1;
        out[0]=if ok {((factory as i32)<<8)|target as i32} else {-4};out
    }
    #[cfg(any(k6_health_telemetry, all(k6_serial_lab, not(k6_no_fsk_rx), feature = "fd6818b")))]
    pub(crate) fn serial_lab_temp(syst:&mut SYST)->(i32,i32) {
        unsafe {
            let rd=|a:usize|core::ptr::read_volatile(a as *const u32);
            let wr=|a:usize,v:u32|core::ptr::write_volatile(a as *mut u32,v);
            let b=0x40012400usize;
            if rd(b+8)&5!=1 {return (0,0);}
            let (ccr,sel,smp)=(rd(b+0x308),rd(b+0x28),rd(b+0x14));
            wr(b+0x308,ccr|(3<<22));wr(b+0x14,7);crate::hal::delay::us(syst,200);
            let sample=|ch:u32|->i32 {wr(b+0x28,1<<ch);let mut sum=0;for _ in 0..16 {wr(b+8,rd(b+8)|4);let mut n=0;while rd(b)&4==0 {n+=1;if n>20000{return 0;}}sum+=(rd(b+0x40)&4095) as i32;}sum};
            let t=sample(16);let v=sample(17);wr(b+0x14,smp);wr(b+0x28,sel);wr(b+0x308,ccr);(t,v)
        }
    }

""" + anchor, 1)
    p.write_text(s)
