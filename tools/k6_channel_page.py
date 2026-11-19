#!/usr/bin/env python3
"""Link channel field page against the exact firmware arena and build ID."""
import argparse,re,struct,subprocess,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modem import k6mod
from k6_build_identity import image_build_id
CAP=8192

def build(image,elf,output):
    rows=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True).splitlines()
    base=int(next(l.split()[0] for l in rows if l.split()[-1].endswith('arena5ARENA')),16)
    rust=(ROOT/'firmware/k6-aura/build/aura-fd6818b-port/src/app/settings.rs').read_text()
    tones=re.search(r'const DCS_TABLE: \[u16; 105\] = \[(.*?)\];',rust,re.S).group(1).replace('0o','0')
    ctcss=re.search(r'const CTCSS_TABLE: \[u16; 50\] = \[(.*?)\];',rust,re.S).group(1)
    with tempfile.TemporaryDirectory() as d:
        d=Path(d);(d/'channel_tones.h').write_text('static const uint16_t dcs[105]={'+tones+'};\nstatic const uint16_t ctcss[50]={'+ctcss+'};\n')
        subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb','-Oz','-ffreestanding','-I'+str(d),'-c',str(ROOT/'c/k6/channel_page.c'),'-o',str(d/'page.o')],check=True)
        subprocess.run(['ld.lld','--gc-sections','-T',str(ROOT/'c/k6/kp.ld'),f'--defsym=KP_ORG={base+16:#x}',f'--just-symbols={elf}','-e','kp_tab','-o',str(d/'page.elf'),str(d/'page.o')],check=True)
        subprocess.run(['llvm-objcopy','-O','binary','--only-section=.page',str(d/'page.elf'),str(d/'page.bin')],check=True)
        code=(d/'page.bin').read_bytes()
    magic,org,entry=struct.unpack_from('<III',code);assert magic==0x0212504b and org==base+16 and entry&1 and org+16<=entry-1<org+len(code)
    page=k6mod.image(code+bytes.fromhex(image_build_id(image.read_bytes())),0,1,None,level=1,lib=0,page=True,magic=b'K6S')
    if len(page)>CAP:raise ValueError('channel page too large')
    output.write_bytes(page);print(f'Channel field page: {len(page)} bytes, arena {base:#x}')
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('image',type=Path);p.add_argument('elf',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.image,a.elf,a.output)
