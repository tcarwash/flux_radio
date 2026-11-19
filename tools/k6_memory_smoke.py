"""Actual cable commands on the simulator, never a physical radio."""
import argparse,tempfile
from pathlib import Path
from k6_system_install_smoke import SimCable,K6Board
from k6_memory_radio import read_bank,write_bank,plan,inventory,CAP
from k6_memories import builtins
p=argparse.ArgumentParser();p.add_argument('image',type=Path);p.add_argument('spi',type=Path);a=p.parse_args()
b=K6Board(a.image,spi_image=a.spi,collect_mmio=False);c=SimCable(b);old=read_bank(c);outside=bytes(b.flash_data[32768:]);assert old==bytes(b.flash_data[:32768]);assert c.status(0x92,8)<0
new,report=plan(old,builtins()['NOAA Weather'],'merge')
with tempfile.TemporaryDirectory() as d:write_bank(c,old,new,d)
assert bytes(b.flash_data[:32768])==new and bytes(b.flash_data[32768:])==outside
assert any(r['Duplex']=='off' for r in inventory(new)[0]);print('PASS: real serial read, NOAA merge, full readback, adjacent storage preserved',report)
