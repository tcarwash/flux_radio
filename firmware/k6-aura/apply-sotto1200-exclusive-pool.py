#!/usr/bin/env python3
"""Opt-in Sotto1200-only decoder pool init for lean half-duplex host.

Generated Aura pager normally initializes shared decoder pool only in nonlean
builds. This opt-in voice profile uses a 14,976-byte expanded shared arena,
but the FD6818B lean host also needs the decoder pool initialized. Preserve
all other profiles, fail closed on unknown anchors and no SPI/RF side effects.
"""
import os
from pathlib import Path

if (os.environ.get('K6_SOTTO1200_ONLY')!='1' or
    os.environ.get('K6_SOTTO_SERVICE')!='1' or
    os.environ.get('K6_RAM0')!='1'):
    raise SystemExit('1200-only pool requires SOTTO1200_ONLY=1, SERVICE=1, RAM0=1')
root=Path(os.environ.get('K6_RX4800_WORK',Path(__file__).parent/'build/aura-fd6818b-port')).resolve()
path=root/'src/app/pager.rs'
s=path.read_text()
old='''        #[cfg(not(feature = "lean"))]
        unsafe {
            ffi::sp_dec_pool(arena::dec(), arena::DEC_BYTES as u32)
        };
        #[cfg(not(feature = "lean"))]
        let need = unsafe { ffi::sp_dec_need() };
        #[cfg(not(feature = "lean"))]
        rl!('''
new=old.replace('#[cfg(not(feature = "lean"))]',
                '#[cfg(any(not(feature = "lean"), k6_sotto_1200_only))]')
ffi_old='''        #[cfg(not(feature = "lean"))]
        pub fn sp_dec_pool(mem: *mut u8, bytes: u32);
        #[cfg(not(feature = "lean"))]
        pub fn sp_dec_need() -> u32;'''
ffi_new=ffi_old.replace('#[cfg(not(feature = "lean"))]',
                        '#[cfg(any(not(feature = "lean"), k6_sotto_1200_only))]')
if s.count(old)==1 and s.count(ffi_old)==1:
    s=s.replace(old,new,1).replace(ffi_old,ffi_new,1)
    path.write_text(s)
elif s.count(new)!=1 or s.count(ffi_new)!=1:
    raise SystemExit('Decoder pool activation/FFI anchors changed; refusing update')
print('Enabled shared 1200 decoder pool initialization in lean host')
