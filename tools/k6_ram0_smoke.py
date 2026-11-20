#!/usr/bin/env python3
"""Simulate boot and exercise the real menu and CPS handshake; not hardware evidence."""
import argparse,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sim.k6emu.__main__ import K6Board
from unicorn import UC_HOOK_CODE
from k6_install import cps_frame, CMD_INFO, CMD_READ, CMD_CAPS, Cable
import struct
from binascii import crc_hqx

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('image',type=Path);p.add_argument('elf',type=Path);p.add_argument('spi',type=Path);a=p.parse_args()
 rows=subprocess.check_output(['llvm-nm','-S',str(a.elf)],text=True).splitlines()
 def sym(s):
  return int(next(l.split()[0] for l in rows if s in l.split()[-1]),16)&~1
 b=K6Board(a.image,spi_image=a.spi,collect_mmio=False);hits={}
 for name in ['poll_keys_on_arena_callback','draw_ui_on_arena_callback','run_cable_session_inner']:
  addr=sym(name);hits[name]=0
  def hook(uc,address,size,data):hits[data]+=1
  b.uc.hook_add(UC_HOOK_CODE,hook,name,begin=addr,end=addr)
 b.run(16000000,2048)
 assert not b.fault and hits['poll_keys_on_arena_callback'] and hits['draw_ui_on_arena_callback'],(b.fault,hits)
 before=b.screen.snapshot();b.press_key('MENU',True);b.run(3000000,2048);b.press_key('MENU',False);b.run(3000000,2048)
 changed=before!=b.screen.snapshot()
 b.inject_uart_rx(b'PROGRAMBF-K6BLWF');b.run(5000000,256)
 def request(cmd,addr=0):
  start=len(b.uart_tx);b.inject_uart_rx(cps_frame(cmd,addr));b.run(2500000,256)
  response=bytes(b.uart_tx[start:]);i=response.index(0xa6);frame=response[i:];n=int.from_bytes(frame[4:6],'big');assert frame[:4]==bytes([0xa6,cmd])+addr.to_bytes(2,'big'),frame.hex()
  assert len(frame)>=8+n and crc_hqx(frame[1:6+n],0)==int.from_bytes(frame[6+n:8+n],'big'),frame.hex()
  return frame[6:6+n]
 info=request(CMD_INFO);assert info[:4]==b'BLWF';arena=int.from_bytes(info[6:8],'little')
 assert arena==next(int(l.split()[1],16) for l in rows if l.split()[-1].endswith('arena5ARENA'))
 caps=Cable.parse_capabilities(request(CMD_CAPS));cal=request(CMD_READ,0xf000//64);assert cal==a.spi.read_bytes()[0xf000:0xf040]
 result={'arena_bytes':arena,'capabilities':caps,'calibration_read_matches':True,'hits':hits,'menu_changed':changed,'fault':b.fault,'uart_tail':bytes(b.uart_tx[-256:]).hex(),'virtual_ms':b.cycles/96000}
 print(json.dumps(result,indent=2));assert not b.fault and changed and hits['run_cable_session_inner'],result
if __name__=='__main__':main()
