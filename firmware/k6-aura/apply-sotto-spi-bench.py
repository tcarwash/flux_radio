#!/usr/bin/env python3
"""Reversible, read-only Sotto SPI cable-timing overlay.

Only modifies a generated Aura checkout when explicitly enabled. No
physical radio I/O or flashing is done by this source generator.
"""
from pathlib import Path
import os
import re

here=Path(__file__).resolve().parent
work=Path(os.environ.get('K6_RX4800_WORK',
        here/'build/aura-fd6818b-port')).resolve()
p=work/'src/cps/mod.rs'
s=p.read_text()
flag=os.environ.get('K6_SOTTO_SPI_BENCH','0')
if flag not in ('0','1'):
    raise SystemExit('K6_SOTTO_SPI_BENCH must be 0 or 1')

const='''// SOTTO_SPI_BENCH_CMD_BEGIN
#[cfg(k6_sotto_spi_bench)]
const CMD_SOTTO_SPI_BENCH: u8 = 0x73;
// SOTTO_SPI_BENCH_CMD_END
'''
branch='''            // SOTTO_SPI_BENCH_DISPATCH_BEGIN
            #[cfg(k6_sotto_spi_bench)]
            CMD_SOTTO_SPI_BENCH if f.addr == 0 && f.len == 0 => {
                let report = sotto_spi_bench(app);
                cable_send(CMD_SOTTO_SPI_BENCH, 0, &report);
            }
            // SOTTO_SPI_BENCH_DISPATCH_END
'''
helper=(here/'sotto-spi-bench.rs.inc').read_text().rstrip()+'\n'

# Remove old generated forms, including source evolved across revisions.
# Do this in memory; never leave only half of an overlay installed.
blocks=(
 ('// SOTTO_SPI_BENCH_BEGIN','// SOTTO_SPI_BENCH_END'),
 ('// SOTTO_SPI_BENCH_CMD_BEGIN','// SOTTO_SPI_BENCH_CMD_END'),
 ('            // SOTTO_SPI_BENCH_DISPATCH_BEGIN',
  '            // SOTTO_SPI_BENCH_DISPATCH_END'),
)
def strip_old(source):
    for begin,end in blocks:
        if source.count(begin) != source.count(end) or source.count(begin)>3:
            raise SystemExit('Invalid/duplicate Sotto SPI bench markers')
        if not source.count(begin):
            continue
        source_count=source.count(begin)
        pattern=r'(?ms)^'+re.escape(begin)+r'\n.*?^'+re.escape(end)+r'\n'
        source,count=re.subn(pattern,'',source)
        if count!=source_count:
            raise SystemExit('Could not safely remove '+begin)
    if 'SOTTO_SPI_BENCH_' in source:
        raise SystemExit('Unknown Sotto SPI bench markers')
    return source

clean=strip_old(s)
if flag=='0':
    if clean!=s:p.write_text(clean)
    print('Disabled Sotto SPI bench; original cable dispatcher restored')
else:
    nor=work/'src/drivers/norflash.rs'
    if 'read_bytes_stream_checked(' not in nor.read_text():
        raise SystemExit('SPI timing bench requires opted-in streaming SPI read path')
    entries=(
       (helper,'const HANDSHAKE: &[u8] = b"PROGRAMBF-K6AURA";\n'),
       (const,'const CMD_CABLE_INFO: u8 = 0x56; // \'V\'\n'),
       (branch,'            frame::CMD_READ_FLASH_RAW if (f.addr as u32) < FLASH_CHIP_SIZE / FLASH_RAW_CHUNK => {\n'),
    )
    for piece,anchor in entries:
        if clean.count(anchor)!=1:
            raise SystemExit('Missing/ambiguous Sotto bench anchor: '+repr(anchor))
    new=clean
    for piece,anchor in entries:
        new=new.replace(anchor,piece+anchor,1)
    if new!=s:p.write_text(new)
    print('Applied opt-in, read-only SPI timing cable command (source only)')
