import sys,subprocess,collections,statistics,time
from pathlib import Path
sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/'tools')]
from sim.k6emu.__main__ import K6Board
from k6_theme_package import pack
from k6_feature_packs import encode_layer
import unicorn
from unicorn.arm_const import UC_ARM_REG_LR,UC_ARM_REG_SP
import argparse
ap=argparse.ArgumentParser(description='Profile Designer instruction counts in the K6 emulator')
ap.add_argument('--image',type=Path,required=True)
ap.add_argument('--elf',type=Path,required=True)
ap.add_argument('--spi',type=Path,required=True)
args=ap.parse_args()
root=Path('firmware/k6-aura')
elf=args.elf
image=args.image
syms={}
sizes={}
for line in subprocess.check_output(['llvm-nm','-S','--demangle',str(elf)],text=True).splitlines():
 for name,tag in [('designer','aura::ui::designer::draw::<'),('widget','aura::ui::standby::draw_flat_widget::<'),('read','aura::ui::designer::read'),('mode','aura::ui::standby::mode_fields')]:
  if tag in line and int(line.split()[1],16)>sizes.get(name,0):
   syms[name]=int(line.split()[0],16)&~1
   sizes[name]=int(line.split()[1],16)
assert set(syms)=={'designer','widget','read','mode'},syms
print(syms,flush=True)
base=0x110000+31*0x4000
seed=args.spi.read_bytes()
from k6_theme_package import migrate
plain=migrate('custom-11-0000000000000000')
head,layout=plain.rsplit('-w',1)
dense='custom-11-0000000000000000-l' + '.'.join(encode_layer(bytes((i%2)+1 for i in range(8192))) for _ in range(3))+'-w'+layout
for name,blob in [('sparse',pack(plain)),('dense',pack(dense))]:
 board=K6Board(image,cpi=1.5,collect_mmio=False)
 board.flash_data[:]=seed
 if blob:
  from k6_user_store_migrate import BASE,END,is_packed,objects,compact
  raw=board.flash_data[BASE:END]
  if is_packed(raw):
   entries=objects(raw);entries[31]=blob;board.flash_data[BASE:END]=compact(entries)[0]
  else:
   board.flash_data[base:base+0x4000]=b'\xff'*0x4000
   board.flash_data[base:base+len(blob)]=blob
 # Install hooks before boot so initial frame and cache population are observed.
 tick=0; stack=[]; results=[]; counts=collections.Counter(); current=None
 def hook(uc,addr,size,data):
  global tick,current
  tick+=1
  if stack and addr==stack[-1][1]:
   fn,ret,start=stack.pop()
   if fn=='designer':
    results.append((tick-start,dict(counts)));counts.clear()
   else:counts[fn+'_instructions']+=tick-start
  for fn,target in syms.items():
   if addr==target and (fn=='designer' or stack):
    stack.append((fn,uc.reg_read(UC_ARM_REG_LR)&~1,tick))
    counts[fn+'_calls']+=1
 board.uc.hook_add(unicorn.UC_HOOK_CODE,hook)
 for _ in range(200):
  board.run(100_000,4096)
  if sum(bool(r[1].get('widget_calls')) for r in results)>=3 or board.fault:break
 frames=[r for r in results if r[1].get('widget_calls')]
 print(name,'fault',board.fault,'frames',frames[:5], 'total entries',len(results),flush=True)
 if board.fault or not frames:raise SystemExit('No successful themed frames')
