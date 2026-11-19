"""Execute a real Thumb overlay after the production C validator reads simulated SPI."""
import pathlib, struct, subprocess, sys
import pytest
import unicorn as u
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
BASE=0x19c000; RAM=0x20002000; FLASH=0x08002000; STOP=0x08001000; BUILD=b'fluxdemo'

@pytest.fixture(scope='module')
def program(tmp_path_factory):
 d=tmp_path_factory.mktemp('system-page'); k=ROOT/'c/k6'
 # Only SPI transport stubbed. Link the production tag checker, with cable-only CRC.
 (d/'host.c').write_text('''#include "system_page.h"
 #include <stdint.h>
 const uint8_t build[8]={'f','l','u','x','d','e','m','o'};
 static int read_spi(uint32_t addr,void *buf,uint32_t n){
   if(addr<KS_BASE || addr>=KS_END || n>KS_END-addr)return -1;
   for(unsigned i=0;i<n;i++)((uint8_t*)buf)[i]=((volatile uint8_t*)0x60000000)[addr-KS_BASE+i];return 0;}
 static const kl_flash_t fl={read_spi,0,0};
 int run(unsigned slot,unsigned value){int n=ks_load(slot,&fl,(uint8_t*)0x20002000,8192,17,1,build);
 if(n<0)return n; return ((int(*)(unsigned))(*(uint32_t*)0x20002018))(value);}
 ''')
 (d/'page.c').write_text('''#include <stdint.h>
 static int add(unsigned n){return n+37;}
 __attribute__((section(".kp_tab"),used)) const uintptr_t kp_tab[]={0x0111504b,(uintptr_t)kp_tab,(uintptr_t)add};''')
 flags=['--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb','-Oz','-ffreestanding','-ffunction-sections','-fdata-sections','-DKM_CABLE_ONLY','-I'+str(k),'-I'+str(ROOT/'c')]
 for name,src in [('host',d/'host.c'),('loader',k/'system_page.c'),('tag',k/'k6_mod.c'),('page',d/'page.c')]:
  subprocess.run(['clang',*flags,'-c',str(src),'-o',str(d/(name+'.o'))],check=True)
 subprocess.run(['ld.lld','--gc-sections','-Ttext=0x08002000','-e','run','-o',str(d/'host.elf'),*[str(d/(n+'.o')) for n in ('host','loader','tag')]],check=True)
 subprocess.run(['llvm-objcopy','-O','binary',str(d/'host.elf'),str(d/'host.bin')],check=True)
 subprocess.run(['ld.lld','--gc-sections','-T',str(k/'kp.ld'),'--defsym=KP_ORG=0x20002010','-e','kp_tab','-o',str(d/'page.elf'),str(d/'page.o')],check=True)
 subprocess.run(['llvm-objcopy','-O','binary','--only-section=.page',str(d/'page.elf'),str(d/'page.bin')],check=True)
 syms=subprocess.check_output(['llvm-nm',str(d/'host.elf')],text=True); entry=int(next(l.split()[0] for l in syms.splitlines() if l.endswith(' T run')),16)
 return (d/'host.bin').read_bytes(),entry,(d/'page.bin').read_bytes()+BUILD

def execute(program,code=None,slot=0,damage=False,empty=False,kind=True):
 host,entry,original=program;code=original if code is None else code
 m=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_THUMB|u.UC_MODE_MCLASS)
 m.mem_map(0x08000000,0x20000);m.mem_write(FLASH,host);m.mem_map(0x20000000,0x6000);m.mem_map(0x60000000,0x64000)
 image=bytearray(k6mod.image(code,0,1,None,level=1,lib=0,page=kind));
 if damage:image[20]^=1
 m.mem_write(0x60000000,b'\xff'*0x64000)
 if not empty:m.mem_write(0x60000000,bytes(image))
 m.reg_write(UC_ARM_REG_SP,0x20005ff0);m.reg_write(UC_ARM_REG_LR,STOP|1);m.reg_write(UC_ARM_REG_R0,slot);m.reg_write(UC_ARM_REG_R1,5)
 m.emu_start(entry|1,STOP,count=100000);assert m.reg_read(UC_ARM_REG_PC)==STOP
 return struct.unpack('<i',struct.pack('<I',m.reg_read(UC_ARM_REG_R0)))[0]

def test_execute(program):assert execute(program)==42
@pytest.mark.parametrize('kind',['wrong_build','wrong_address','wrong_count','bad_pointer','bad_crc','empty','wrong_flag','out_of_range'])
def test_reject(program,kind):
 code=bytearray(program[2]);opts={}
 if kind=='wrong_build':code[-1]^=1
 elif kind=='wrong_address':code[4]^=4
 elif kind=='wrong_count':code[3]=2
 elif kind=='bad_pointer':struct.pack_into('<I',code,8,0x08002001)
 elif kind=='bad_crc':opts['damage']=True
 elif kind=='empty':opts['empty']=True
 elif kind=='wrong_flag':opts['kind']=False
 elif kind=='out_of_range':opts['slot']=25
 assert execute(program,bytes(code),**opts)<0
