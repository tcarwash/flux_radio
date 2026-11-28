// SOTTO_SPI_BENCH_BEGIN
// Opt-in cable-only, read-only benchmark. Never enters TX or touches GP0.
// TIM14 counts 100us units and is initialized at boot as uptime.
// A 128-read batch resolves microsecond per-read differences with <1us
// aggregate quantization; each sample must complete within one timer wrap.
#[cfg(k6_sotto_spi_bench)]
#[inline(never)] // keep the 2x128-byte compare buffers off the cable dispatch frame
fn sotto_spi_bench(app: &mut App) -> [u8; 24] {
    const N: u16 = 128;
    let mut ticks = [0u16; 4]; // length20 baseline/fast; length128 baseline/fast
    let mut hash = [0u32; 2]; // cheap check, equal sampling order
    let mut faults = 0u16;
    let mut baseline = [0u8; 128];
    let mut optimized = [0u8; 128];
    for (ix, count) in [20usize, 128usize].iter().enumerate() {
        for mode in 0..2 {
            let start = crate::hal::uptime::now();
            for i in 0..N {
                // Codebook payload only; covers multiple data offsets.
                let address = 0x100020 + (u32::from(i) * 113) % (30720 - 128);
                let good = if mode == 0 {
                    app.storage_mut().norflash.read_bytes_checked(
                        address, &mut baseline[..*count])
                } else {
                    app.storage_mut().norflash.read_bytes_stream_checked(
                        address, &mut optimized[..*count])
                };
                if !good {
                    faults |= if mode == 0 {1} else {2};
                    break;
                }
                let buf = if mode == 0 {&baseline[..*count]}
                          else {&optimized[..*count]};
                hash[mode] = hash[mode].wrapping_mul(33)
                    ^ (u32::from(buf[0]) << 8)
                    ^ u32::from(buf[*count - 1]);
            }
            ticks[ix * 2 + mode] = crate::hal::uptime::now().wrapping_sub(start);
        }
    }
    if hash[0] != hash[1] {faults |= 4;}
    // Validate full bytes at 8 additional addresses, independently of the
    // timed loops; header/payload reads never modify SPI flash.
    for i in 0..8u32 {
        let address = 0x100020 + i * 383;
        if !app.storage_mut().norflash.read_bytes_checked(address, &mut baseline) {
            faults |= 1;
            break;
        }
        if !app.storage_mut().norflash.read_bytes_stream_checked(address, &mut optimized) {
            faults |= 2;
            break;
        }
        if baseline != optimized {faults |= 8;}
    }
    let mut result = [0u8;24];
    result[..4].copy_from_slice(b"SPB1");
    result[4..6].copy_from_slice(&N.to_le_bytes());
    result[6..8].copy_from_slice(&faults.to_le_bytes());
    for i in 0..4 {
        result[8 + 2*i..10 + 2*i].copy_from_slice(&ticks[i].to_le_bytes());
    }
    result[16..20].copy_from_slice(&hash[0].to_le_bytes());
    result[20..24].copy_from_slice(&hash[1].to_le_bytes());
    result
}
// SOTTO_SPI_BENCH_END
