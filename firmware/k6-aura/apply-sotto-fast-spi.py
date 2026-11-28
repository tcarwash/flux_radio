#!/usr/bin/env python3
"""Opt-in Sotto API28 NOR streaming read path; never changes the default build.

The SPI controller is 8-bit with FRXTH (one RXNE per received byte).
For a *single* CS-low NOR READ transaction, RXNE proves the last byte has
clocked through; BUSY must be checked only once, before raising CS.
Keep the loader's separately bounded read_bytes_checked path unchanged.
This script patches only the generated, pinned Aura checkout.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("K6_RX4800_WORK", HERE/"build/aura-fd6818b-port")).resolve()
SPI = WORK/"src/hal/spi.rs"
NOR = WORK/"src/drivers/norflash.rs"
PAGER = WORK/"src/app/pager.rs"

def insert(path:Path, anchor:str, text:str, marker:str):
    original=path.read_text()
    if marker in original: return False
    if original.count(anchor)!=1:
        raise SystemExit(f"Expected one {anchor[:70]!r} in {path}; refusing partial patch")
    path.write_text(original.replace(anchor,text+anchor,1))
    return True

# A generated Aura checkout is reused across build profiles. Switching this
# flag OFF must also remove a previously applied fast path: otherwise a later
# default build could silently inherit an unqualified SPI implementation.
mode=os.environ.get("K6_SOTTO_FAST_SPI","1")
if mode not in ("0","1"):
    raise SystemExit("K6_SOTTO_FAST_SPI must be 0 or 1")
if mode=="0":
    import re
    for path,label in ((SPI,"STREAM"),(NOR,"NOR")):
        original=path.read_text()
        pattern=(r"(?ms)^    // SOTTO_FAST_SPI_"+label+
                 r"_BEGIN\n.*?^    // SOTTO_FAST_SPI_"+label+r"_END\n\n")
        patched,count=re.subn(pattern,"",original)
        if count>1:raise SystemExit(f"Duplicate opt-in SPI markers in {path}")
        if count:path.write_text(patched)
    original=PAGER.read_text()
    old='''    // SOTTO_FAST_SPI_RESOURCE_BEGIN: bounded CS-low burst, check BSY once.
    if app.storage.norflash.read_bytes_stream_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}
    // SOTTO_FAST_SPI_RESOURCE_END'''
    new='''    if app.storage.norflash.read_bytes_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}'''
    if old in original:PAGER.write_text(original.replace(old,new,1))
    elif "SOTTO_FAST_SPI_RESOURCE_BEGIN" in original:
        raise SystemExit("Unrecognized fast SPI resource callback, cannot disable")
    print("Disabled opt-in Sotto fast SPI path; default NOR reads restored")
    raise SystemExit(0)

if "// SOTTO_API28_RESOURCES_BEGIN" not in PAGER.read_text():
    raise SystemExit("Sotto opt-in streaming SPI requires API28 resource service")
if "pub fn try_transfer_byte(" not in SPI.read_text() or "pub fn read_bytes_checked(" not in NOR.read_text():
    raise SystemExit("Unsupported SPI/NOR driver; existing bounded checked path missing")

spi_code="""    // SOTTO_FAST_SPI_STREAM_BEGIN
    /// A NOR READ transaction under an already asserted chip select.
    /// The existing checked single-byte transfer is deliberately unchanged.
    /// In 8-bit FRXTH mode, each RXNE means the current byte completed.
    /// We wait for BSY to clear *once*, after the final byte, before CS-high.
    /// On any bounded timeout, the caller deasserts CS and reports failure.
    #[inline(always)]
    fn try_stream_byte(&mut self, byte: u8) -> Option<u8> {
        let mut ready = false;
        for _ in 0..TRANSFER_SPIN_LIMIT {
            if self.regs.sr().read().txe().bit_is_set() {ready = true;break;}
        }
        if !ready {return None;}
        // DR MUST be 8-bit; writing 16/32 bits causes FIFO byte misalignment.
        unsafe {core::ptr::write_volatile(self.regs.dr().as_ptr() as *mut u8, byte);}
        ready = false;
        for _ in 0..TRANSFER_SPIN_LIMIT {
            if self.regs.sr().read().rxne().bit_is_set() {ready = true;break;}
        }
        if !ready {return None;}
        Some(unsafe {core::ptr::read_volatile(self.regs.dr().as_ptr() as *const u8)})
    }

    pub fn try_transfer_stream(&mut self, prefix: &[u8], dst: &mut [u8]) -> bool {
        for &byte in prefix {
            if self.try_stream_byte(byte).is_none() {return false;}
        }
        for slot in dst {
            let Some(rx) = self.try_stream_byte(0x00) else {return false;};
            *slot = rx;
        }
        // The final RXNE alone is not sufficient to release flash CS.
        for _ in 0..TRANSFER_SPIN_LIMIT {
            if self.regs.sr().read().bsy().bit_is_clear() {return true;}
        }
        false
    }
    // SOTTO_FAST_SPI_STREAM_END

"""
nor_code="""    // SOTTO_FAST_SPI_NOR_BEGIN
    /// Only the opt-in API28 codec resource service calls this path.
    /// Header and bytes are one continuous CS-low transaction. All
    /// timeouts deassert CS and fail; other flash operations are unchanged.
    pub fn read_bytes_stream_checked(&mut self, addr: u32, buf: &mut [u8]) -> bool {
        if addr >= 0x1000000 {return false;}
        self.cs_low();
        let header = [CMD_READ_DATA,(addr >> 16) as u8,(addr >> 8) as u8,addr as u8];
        let ok = self.spi.try_transfer_stream(&header, buf);
        self.cs_high();
        ok
    }
    // SOTTO_FAST_SPI_NOR_END

"""
insert(SPI, "    /// A bounded transfer for operations that must recover from a wedged peripheral.",spi_code,"// SOTTO_FAST_SPI_STREAM_BEGIN")
insert(NOR, "    /// `addr` and `data` together must not cross a page boundary.",nor_code,"// SOTTO_FAST_SPI_NOR_BEGIN")

source=PAGER.read_text()
old="""    if app.storage.norflash.read_bytes_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}
}
 // SOTTO_API28_RESOURCES_END"""
# Exact callback end block -- keep the preflight/header and other NOR reads on legacy path.
before="""    if app.storage.norflash.read_bytes_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}
}
// SOTTO_API28_RESOURCES_END"""
after="""    // SOTTO_FAST_SPI_RESOURCE_BEGIN: bounded CS-low burst, check BSY once.
    if app.storage.norflash.read_bytes_stream_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}
    // SOTTO_FAST_SPI_RESOURCE_END
}
// SOTTO_API28_RESOURCES_END"""
if "SOTTO_FAST_SPI_RESOURCE_BEGIN" not in source:
    if source.count(before)!=1:
        raise SystemExit("API28 callback anchor missing or ambiguous; refuses to patch resource service")
    PAGER.write_text(source.replace(before,after,1))
print(f"Applied opt-in Sotto resource-only SPI streaming path to {WORK}")
