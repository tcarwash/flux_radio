#!/usr/bin/env python3
"""Full radio emulator: link page for actual firmware, populate virtual SPI, call CPS."""
import argparse, pathlib, struct, subprocess, sys, tempfile
from binascii import crc_hqx
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from sim.k6emu.__main__ import K6Board
from k6_install import cps_frame
from k6_build_identity import image_build_id

def build_page(firmware, elf):
 rows=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True).splitlines()
 base=int(next(l.split()[0] for l in rows if l.split()[-1].endswith('arena5ARENA')),16)
 build=bytes.fromhex(image_build_id(firmware.read_bytes()))
 with tempfile.TemporaryDirectory() as d:
  d=pathlib.Path(d);(d/'page.c').write_text('#include <stdint.h>\nstatic int add(unsigned n){return n+37;}\n__attribute__((section(".kp_tab"),used)) const uintptr_t kp_tab[]={0x0111504b,(uintptr_t)kp_tab,(uintptr_t)add};')
  subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb','-Oz','-ffreestanding','-c',str(d/'page.c'),'-o',str(d/'page.o')],check=True)
  subprocess.run(['ld.lld','--gc-sections','-T',str(ROOT/'c/k6/kp.ld'),f'--defsym=KP_ORG={base+16:#x}','-e','kp_tab','-o',str(d/'page.elf'),str(d/'page.o')],check=True)
  subprocess.run(['llvm-objcopy','-O','binary','--only-section=.page',str(d/'page.elf'),str(d/'page.bin')],check=True)
  page=(d/'page.bin').read_bytes()
 return build,page

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('firmware',type=pathlib.Path);p.add_argument('elf',type=pathlib.Path);p.add_argument('spi',type=pathlib.Path);a=p.parse_args()
 build,page=build_page(a.firmware,a.elf)
 b=K6Board(a.firmware,spi_image=a.spi,collect_mmio=False);b.run(16000000,2048);b.inject_uart_rx(b'PROGRAMBF-K6BLWF');b.run(5000000,256)
 assert not b.fault and bytes(b.uart_tx).endswith(b'\x06')
 def request():
  start=len(b.uart_tx);b.inject_uart_rx(cps_frame(0x7b,0,struct.pack('<I',5)));b.run(3000000,256)
  response=bytes(b.uart_tx[start:]);i=response.index(0xa6);f=response[i:];assert f[:6]==b'\xa6\x7b\0\0\0\x04',f.hex();assert crc_hqx(f[1:10],0)==int.from_bytes(f[10:12],'big');assert not b.fault,b.fault
  return struct.unpack('<i',f[6:10])[0]
 def set_page(code,damage=False):
  image=bytearray(k6mod.image(code,0,1,None,level=1,lib=0,page=True,magic=b"K6S"));
  if damage:image[20]^=1
  b.flash_data[0x19c000:0x1a0000]=b'\xff'*0x4000
  b.flash_data[0x19c000:0x19c00c]=b"KSD1"+build
  struct.pack_into("<II",b.flash_data,0x19c010+8*17,0x19d000,len(image))
  b.flash_data[0x19d000:0x19d000+len(image)]=image
 b.flash_data[0x19c000:0x1a0000]=b'\xff'*0x4000
 assert request()<0
 set_page(page+build);assert request()==42;assert request()==42
 set_page(page+bytes(8));assert request()<0
 set_page(page+build,True);assert request()<0
 set_page(page+build);assert request()==42
 print('PASS: empty, valid, repeat, wrong build, bad CRC, replacement; no CPU faults')
if __name__=='__main__':main()
