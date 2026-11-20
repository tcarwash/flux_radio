#!/usr/bin/env python3
"""Build-bound GP0 acquisition overlay, placed after 11000 bytes of DMA scratch."""
import argparse,struct,subprocess,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modem import k6mod
from k6_build_identity import image_build_id
SCRATCH=11000;ID=19

def build(image,elf,output):
    rows=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True).splitlines()
    row=next(l.split() for l in rows if l.split()[-1].endswith('arena5ARENA'))
    base,arena=(int(x,16) for x in row[:2]);org=base+SCRATCH+16
    with tempfile.TemporaryDirectory() as d:
        d=Path(d);objs=[]
        for unit in ('serial_if_lab','if_iq','if_cic'):
            obj=d/(unit+'.o');objs.append(str(obj))
            subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb','-Os','-ffreestanding','-ffunction-sections','-fdata-sections','-DK6_IQ_PAGE','-c',str(ROOT/'c/k6'/f'{unit}.c'),'-o',str(obj)],check=True)
        subprocess.run(['ld.lld','--gc-sections','-T',str(ROOT/'c/k6/kp.ld'),f'--defsym=KP_ORG={org:#x}',f'--just-symbols={elf}','-e','kp_tab','-o',str(d/'page.elf'),*objs],check=True)
        subprocess.run(['llvm-objcopy','-O','binary','--only-section=.page',str(d/'page.elf'),str(d/'page.bin')],check=True)
        code=(d/'page.bin').read_bytes()
    magic,linked,entry=struct.unpack_from('<III',code)
    assert magic==0x0113504b and linked==org and entry&1 and org+12<=entry-1<org+len(code)
    page=k6mod.image(code+bytes.fromhex(image_build_id(image.read_bytes())),0,1,None,level=1,lib=0,page=True,magic=b'K6S')
    if len(page)>arena-SCRATCH:raise ValueError('IQ page exceeds non-DMA arena tail')
    output.write_bytes(page);print(f'IQ engine page: {len(page)} bytes, scratch {SCRATCH}, tail {arena-SCRATCH}')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('image',type=Path);p.add_argument('elf',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.image,a.elf,a.output)
