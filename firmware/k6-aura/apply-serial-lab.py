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
#[cfg(k6_serial_lab)]
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
            #[cfg(k6_serial_lab)]
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
            #[cfg(k6_serial_lab)]
            0x71 if f.len == 0 => {
                let stats = uart::cable_stats();
                let mut data = [0u8; 16];
                for i in 0..4 { data[i*4..i*4+4].copy_from_slice(&stats[i].to_le_bytes()); }
                cable_send(0x71, f.addr, &data);
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
new = '''    #[cfg(k6_serial_lab)]
    let mut out = [0u8; 264];
    #[cfg(not(k6_serial_lab))]
    let mut out = [0u8; 8 + FLASH_RAW_CHUNK as usize];'''
if new not in s:
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
    s = s.replace('struct RxRing {', 'struct RxRing {\n    #[cfg(k6_serial_lab)]\n    errors: [u32; 4],', 1)
    s = s.replace('        RxRing {', '        RxRing {\n            #[cfg(k6_serial_lab)]\n            errors: [0; 4],', 1)
    s = s.replace('        if self.len == RX_RING_CAP {', '        if self.len == RX_RING_CAP {\n            #[cfg(k6_serial_lab)]\n            { self.errors[3] = self.errors[3].wrapping_add(1); }', 1)
    anchor = '    if isr.rxne().bit_is_set() {'
    insert = """    #[cfg(k6_serial_lab)]
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
#[cfg(k6_serial_lab)]
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
    s = s.replace('pub(crate) fn crc16_xmodem(', '#[cfg(not(k6_serial_lab))]\npub(crate) fn crc16_xmodem(', 1)
    insert = """
// Flux serial lab nibble CRC (same CRC-16/XMODEM, initial value zero).
#[cfg(k6_serial_lab)]
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
