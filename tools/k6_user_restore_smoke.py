"""Actual firmware CPS restore test using the virtual radio only."""
import argparse,tempfile
from modem import k6mod
from pathlib import Path
from k6_system_install_smoke import SimCable,K6Board
from k6_user_store_restore import restore,prepare,load_recovery,complete
from k6_user_store_migrate import BASE,END,upgrade,compact,objects
from k6_build_identity import image_build_id

def main():
 p=argparse.ArgumentParser();p.add_argument('firmware',type=Path);p.add_argument('spi',type=Path);p.add_argument('--keyed',action='store_true');a=p.parse_args()
 build=bytes.fromhex(image_build_id(a.firmware.read_bytes()));original=a.spi.read_bytes();bank,_=upgrade(original[BASE:END]);key=None
 if a.keyed:
  key=bytes(range(16));# ephemeral test key, never a user's key
  fw=a.firmware.with_name('keyed-smoke.bin');fw.write_bytes(k6mod.key_image(a.firmware.read_bytes(),key));a.firmware=fw
  code=bytes.fromhex('00207047')
  img=k6mod.image(code,0,1,key,lib=7,level=1)
  bank,_=compact({7:img});original=original[:BASE]+bank+original[END:]
 b=K6Board(a.firmware,spi_image=a.spi,collect_mmio=False);b.flash_data[:]=original
 class TestCable(SimCable):
  def read(self,addr,n,**kwargs): # simulator-side full image inspection, not physical wire throughput
   return bytes(self.board.flash_data[addr:addr+n])
 c=TestCable(b);assert c.status(0x86,0,bytes(8))<0;assert c.status(0x86,0,build)==0;assert c.status(0x87,128)<0;assert c.status(0x87,1)<0;assert c.status(0x87,0)==0;assert c.status(0x89)<0
 partial=bytes(b.flash_data);b=K6Board(a.firmware,spi_image=a.spi,collect_mmio=False);b.flash_data[:]=partial;c=TestCable(b)
 if key:
  from k6_install import InstallError
  for invalid in (None,bytes([255])*16):
   try:restore(c,build,bank,key=invalid)
   except InstallError:pass
   else:raise AssertionError('incorrect key accepted')
  assert bytes(b.flash_data)==partial
 restore(c,build,bank,key=key);assert bytes(b.flash_data[BASE:END])==bank;assert bytes(b.flash_data[:BASE])==original[:BASE];assert bytes(b.flash_data[END:])==original[END:];assert c.status(0x89)==0
 if key:
  # Execute a freshly signed install via the actual cable receiver, verify COW replacement.
  from k6_install import Serials
  from k6_install import CMD_CHUNK
  image=k6mod.image(bytes.fromhex('01207047'),0,2,key,lib=7,level=1)
  pk=k6mod.packets(image,2,64)
  for i,packet in enumerate(pk):result=c.call(CMD_CHUNK,i,packet)
  assert result[0]==3,result.hex()
  assert objects(b.flash_data[BASE:END])[7]==image
  print('PASS: keyed capability authentication and signed module replacement')
 print('PASS: interrupted erase, wrong build, out-of-range commands, full restore, repeat activation, preserved adjacent partitions')
if __name__=='__main__':main()
