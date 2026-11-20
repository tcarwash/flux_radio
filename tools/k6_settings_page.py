#!/usr/bin/env python3
"""Build-bound settings formatting page, using stable generated scalar IDs."""
import argparse,struct,subprocess,tempfile,sys,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from modem import k6mod
from k6_build_identity import image_build_id
# IDs are independent of cfg-dependent Rust enum discriminants.
NAMES='Sql Step Tot Tdr Save Abr BusyLock TxForbid Wn TxPr RxCts TxCts Sftd Offse Beep ActionBeep AutoLk Vox VoxLv VoxDly Scrm Rtone Tail Rptrl Contrast Theme Roge ScanMd Rit ChDisp AniTx AniCall Side1Short Side1Long Side2Short Side2Long BandShort BandLong BkIn BootMode BootSnd BattCal FLock Info ChipId Reset AprsCall AprsFreq AprsLat AprsLon AprsPath AprsDevInfo AprsDevName AprsBatVolt AprsComment AprsSsid AprsSymbol AprsPower'.split()
def build(image,elf,output):
    rows=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True).splitlines()
    base=int(next(l.split()[0] for l in rows if l.split()[-1].endswith('arena5ARENA')),16)
    with tempfile.TemporaryDirectory() as td:
        d=Path(td);(d/'settings_ids.h').write_text('\n'.join(f'#define S_{n} {i}' for i,n in enumerate(NAMES)))
        subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb','-Oz','-ffreestanding','-I'+str(d),'-c',str(ROOT/'c/k6/settings_page.c'),'-o',str(d/'page.o')],check=True)
        subprocess.run(['ld.lld','--gc-sections','-T',str(ROOT/'c/k6/kp.ld'),f'--defsym=KP_ORG={base+16:#x}',f'--just-symbols={elf}','-e','kp_tab','-o',str(d/'page.elf'),str(d/'page.o')],check=True)
        subprocess.run(['llvm-objcopy','-O','binary','--only-section=.page',str(d/'page.elf'),str(d/'page.bin')],check=True)
        code=(d/'page.bin').read_bytes()
    magic,org,entry=struct.unpack_from('<III',code);assert magic==0x0114504b and org==base+16 and entry&1 and org+12<=entry-1<org+len(code)
    page=k6mod.image(code+bytes.fromhex(image_build_id(image.read_bytes())),0,1,None,level=1,lib=0,page=True,magic=b'K6S')
    if len(page)>8192:raise ValueError('settings page exceeds arena')
    output.write_bytes(page);print(f'Settings page: {len(page)} bytes')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('image',type=Path);p.add_argument('elf',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.image,a.elf,a.output)
