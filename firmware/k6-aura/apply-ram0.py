#!/usr/bin/env python3
"""Flux RAM0 relocation, adapted from upstream d84accf; opt-in, no API renumbering."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port'
def patch(path, old, new):
 p=root/path;s=p.read_text()
 if new in s:return
 if path=='src/main.rs' and old=='static mut APP_SLOT:' and 'cfg_attr(all(k6_ram0,not(k6_ram0_small))' in s:return
 if path=='src/hal/arena.rs' and 'k6_ram0_small' in s:return
 if s.count(old)!=1:raise SystemExit(f'{path}: missing/ambiguous {old}')
 p.write_text(s.replace(old,new,1))
patch('memory.x','  RAM   : ORIGIN = 0x20001028, LENGTH = 20424','  RAM   : ORIGIN = 0x20001028, LENGTH = 20424\n  RAM0  : ORIGIN = 0x20000100, LENGTH = 0xF00')
p=root/'memory.x';s=p.read_text()
if '__sram0' not in s:p.write_text(s+'''\nSECTIONS {\n  .ram0 (NOLOAD) : ALIGN(4) {\n    __sram0 = .;\n    *(.ram0 .ram0.*);\n    . = ALIGN(4);\n    __eram0 = .;\n  } > RAM0\n} INSERT AFTER .uninit;\n/* Keep main-RAM heap/watermark and stack floor independent of RAM0 VMA. */\n__sheap = __euninit;\n_stack_end = __euninit;\n''')
# Repair previously generated linker scripts as well.
s=p.read_text()
if '__sheap = __euninit;' not in s:p.write_text(s+'\n__sheap = __euninit;\n_stack_end = __euninit;\n')
# Relocate the whole 3008-byte App and 352-byte RX ring; 200-byte screen fits too.
# All have zero/uninitialized initial state. No nonzero .data objects in NOLOAD.
for path,anchor in [('src/main.rs','static mut APP_SLOT:'),('src/hal/uart.rs','static RX_RING:'),('src/app/pager.rs','pub static mut MOD_SCREEN:')]:
 patch(path,anchor,'#[cfg_attr(k6_ram0, link_section = ".ram0")]\n'+anchor)
patch('src/main.rs','fn main() -> ! {','fn main() -> ! {\n    #[cfg(k6_ram0)]\n    unsafe {\n        extern "C" { static mut __sram0: u32; static mut __eram0: u32; }\n        let mut p=core::ptr::addr_of_mut!(__sram0);\n        let end=core::ptr::addr_of_mut!(__eram0);\n        while p<end { core::ptr::write_volatile(p,0); p=p.add(1); }\n    }')
patch('src/hal/arena.rs','#[cfg(not(small_arena))]\npub const ARENA_BYTES: usize = 11776;','#[cfg(all(not(small_arena),not(k6_ram0)))]\npub const ARENA_BYTES: usize = 11776;\n#[cfg(all(not(small_arena),k6_ram0))]\npub const ARENA_BYTES: usize = 14976;')

# Diagnostic layout: relocate small buffers only; retain full App and original arena.
for path,anchor in [('src/main.rs','static mut APP_SLOT:'),('src/hal/arena.rs','pub const ARENA_BYTES: usize = 14976;')]:
 p=root/path;s=p.read_text()
 if path.endswith('main.rs'):
  s=s.replace('#[cfg_attr(k6_ram0, link_section = ".ram0")]\n'+anchor,'#[cfg_attr(all(k6_ram0,not(k6_ram0_small)), link_section = ".ram0")]\n'+anchor)
 else:
  s=s.replace('#[cfg(all(not(small_arena),not(k6_ram0)))]','#[cfg(all(not(small_arena),any(not(k6_ram0),k6_ram0_small)))]').replace('#[cfg(all(not(small_arena),k6_ram0))]','#[cfg(all(not(small_arena),k6_ram0,not(k6_ram0_small)))]')
 p.write_text(s)

# Diagnostic full relocation with the original arena, reserving the gain as stack.
p=root/'src/hal/arena.rs';s=p.read_text()
s=s.replace('any(not(k6_ram0),k6_ram0_small)', 'any(not(k6_ram0),k6_ram0_small,k6_ram0_stack)').replace('k6_ram0,not(k6_ram0_small)))]', 'k6_ram0,not(k6_ram0_small),not(k6_ram0_stack)))]')
# Balanced keeps the expanded-stack live loader gate but gives 2624 bytes
# back to the arena. Replace only the existing stack-layout declaration.
old='#[cfg(all(not(small_arena),any(not(k6_ram0),k6_ram0_small,k6_ram0_stack)))]\npub const ARENA_BYTES: usize = 11776;'
new='#[cfg(all(not(small_arena),not(k6_ram0_balanced),any(not(k6_ram0),k6_ram0_small,k6_ram0_stack)))]\npub const ARENA_BYTES: usize = 11776;\n#[cfg(all(not(small_arena),k6_ram0_balanced))]\npub const ARENA_BYTES: usize = 14400;'
if new not in s:
 if s.count(old)!=1:raise SystemExit("unexpected arena declaration")
 s=s.replace(old,new,1)
p.write_text(s)
