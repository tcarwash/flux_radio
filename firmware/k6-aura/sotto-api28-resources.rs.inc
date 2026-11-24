// SOTTO_API28_RESOURCES_BEGIN
// Opt-in, read-only API28+ module resources. No SPI writes, PA, heap or
// ABI shift. Supports the fixed K6CB codebook and a packed K6SC module deck.
#[cfg(k6_sotto_resources)]
const SOTTO_CB_BASE: u32 = 0x100000;
#[cfg(k6_sotto_resources)]
const SOTTO_CB_LEN: u32 = 32 + 30720;
#[cfg(k6_sotto_resources)]
const SOTTO_K6SC_LEN: u32 = 4182;
#[cfg(k6_sotto_resources)]
static mut SOTTO_RESOURCE_DECK: (u32,u32) = (0,0);

#[cfg(k6_sotto_resources)]
unsafe extern "C" fn mod_sotto_resource_open(slot:u32)->i32 {
    mod_tx_guard();
    SOTTO_RESOURCE_DECK=(0,0); // failed or cross-module opens never reuse a deck
    let ctx=&*addr_of!(MOD_CTX);
    if ctx.app.is_null() || ctx.tx || slot>=32 {return -1;}
    let (app,_) = mod_ctx();
    let Some((base,extent))=library_location(&mut app.storage.norflash,slot) else {
        return -1;
    };
    if extent < SOTTO_K6SC_LEN + 24 {return -1;}
    let mut h=[0u8;20];
    if !app.storage.norflash.read_bytes_checked(base,&mut h) {return -1;}
    if &h[..4]!=b"K6M\x01" || u16::from_le_bytes([h[4],h[5]]) as u32 != SOTTO_K6SC_LEN
       || h[6]!=0 || h[7]!=0 || h[13]!=5 || h[15]!=slot as u8
       || &h[16..20]!=b"K6SC" {
        return -1;
    }
    let Some(end)=base.checked_add(16+SOTTO_K6SC_LEN) else {return -1;};
    if end>0x200000 || end>base+extent {return -1;}
    SOTTO_RESOURCE_DECK=(base+16,SOTTO_K6SC_LEN);
    SOTTO_K6SC_LEN as i32
}

#[cfg(k6_sotto_resources)]
unsafe extern "C" fn mod_sotto_resource_read(kind:u32,off:u32,buf:*mut u8,n:u32)->i32 {
    mod_tx_guard();
    if (*addr_of!(MOD_CTX)).app.is_null() || buf.is_null() || n==0 || n>256 {
        return -1;
    }
    // Both module scratch and its stack are inside the KD32F328's 24 KiB SRAM.
    // A native module is not a security boundary, but invalid addresses must
    // not become peripheral writes through a host-owned SPI read.
    let start=buf as usize;
    let Some(end)=start.checked_add(n as usize) else {return -1;};
    if start<0x20000000 || end>0x20006000 {return -1;}
    let (base,len)=match kind {
        0=>(SOTTO_CB_BASE,SOTTO_CB_LEN),
        1=>SOTTO_RESOURCE_DECK,
        _=>return -1,
    };
    if base==0 || off>len || n>len-off {return -1;}
    let Some(addr)=base.checked_add(off) else{return -1;};
    if addr>=0x200000 || n>0x200000-addr {return -1;}
    let (app,_) = mod_ctx();
    if app.storage.norflash.read_bytes_checked(
        addr,core::slice::from_raw_parts_mut(buf,n as usize)) {0} else {-1}
}
// SOTTO_API28_RESOURCES_END
