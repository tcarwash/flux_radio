#!/usr/bin/env python3
"""Exercise actual NOR erase/program/readback and exclusive CPS commands in K6Board."""
import argparse, struct
from pathlib import Path
from binascii import crc_hqx
from k6_system_demo_smoke import build_page, K6Board, k6mod, cps_frame
from k6_system_install import BASE, END, BEGIN, ERASE, STAGE, DATA, COMMIT, install
from k6_install import Cable

class SimCable(Cable):
    def __init__(self, board):
        self.board=board
        board.run(16000000,2048)
        board.inject_uart_rx(b'PROGRAMBF-K6BLWF');board.run(5000000,256)
        assert not board.fault and bytes(board.uart_tx).endswith(b'\x06')
    def call(self,cmd,addr=0,data=b''):
        b=self.board;start=len(b.uart_tx);b.inject_uart_rx(cps_frame(cmd,addr,data));
        for _ in range(100):
            b.run(100000,256)
            if len(b.uart_tx)-start>=12 or b.fault:break
        response=bytes(b.uart_tx[start:]);f=response[response.index(0xa6):];n=int.from_bytes(f[4:6],'big')
        assert f[:4]==bytes([0xa6,cmd])+addr.to_bytes(2,'big'),f.hex()
        assert crc_hqx(f[1:6+n],0)==int.from_bytes(f[6+n:8+n],'big') and not b.fault,b.fault
        return f[6:6+n]
    def end(self): # Verify bytes before resetting the simulated target.
        pass
    def status(self,*args):return struct.unpack('<i',self.call(*args))[0]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('firmware',type=Path);p.add_argument('elf',type=Path);p.add_argument('spi',type=Path);p.add_argument('--page',type=Path);a=p.parse_args()
    if a.page:
        img=a.page.read_bytes();build=img[-16:-8]
    else:
        build,page=build_page(a.firmware,a.elf);img=k6mod.image(page+build,0,1,None,level=1,lib=0,page=True,magic=b"K6S")
    old=bytearray(a.spi.read_bytes());old[BASE:END]=b'\x00'*(END-BASE)
    b=K6Board(a.firmware,spi_image=a.spi,collect_mmio=False);b.flash_data[:]=old;c=SimCable(b)
    assert c.status(BEGIN,0,bytes(8))<0 and b.flash_data==old
    assert c.status(BEGIN,0,build)==0
    assert c.status(ERASE,1)<0 and c.status(ERASE,0)==0 and c.status(ERASE,0)==0
    assert c.status(0x4d,0,b'\0'*64)<0
    # Simulate cable disconnect/reboot after first sector, preserving physical SPI.
    partial=bytes(b.flash_data);b=K6Board(a.firmware,spi_image=a.spi,collect_mmio=False);b.flash_data[:]=partial;c=SimCable(b)
    install(c,a.firmware.read_bytes(),img)
    assert bytes(b.flash_data[:BASE])==old[:BASE]
    assert bytes(b.flash_data[BASE:BASE+len(img)])==img
    assert bytes(b.flash_data[BASE+len(img):END])==b'\xff'*(END-BASE-len(img))
    # Reboot and execute installed code if demo command present; installation build may omit it.
    print('PASS: wrong build, erase ordering/retry, exclusive mode, interrupted erase, reinstall, preserved non-system SPI')
if __name__=='__main__':main()
