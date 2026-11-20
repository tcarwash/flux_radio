import sys,random
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from k6_live_v2 import *

def test_binary_safe_codec():
    rng=random.Random(0)
    for n in range(129):
        p=rng.randbytes(n)
        wire=request_bytes_v2(ECHO,255,0x12345678,p)
        assert all(b>=128 for b in wire[6:-1])
        assert len(wire)==7+((n+10)*8+6)//7
        assert decode_request(wire)==(ECHO,255,0x12345678,p)
    p=b"PROGRAMBF-K6AURA PROGRAMBF-K6BLWF FLXLV1 FLXLV2"
    assert decode_request(request_bytes_v2(ECHO,1,payload=p))[-1]==p
    with pytest.raises(ValueError):request_bytes_v2(ECHO,1,payload=b"x"*129)

def test_corruption_and_truncation():
    wire=request_bytes_v2(ECHO,4,payload=bytes(range(128)))
    for i in range(6,len(wire)-1):
        corrupt=wire[:i]+bytes([wire[i]^1])+wire[i+1:]
        with pytest.raises(ValueError):decode_request(corrupt)
    for i in range(len(wire)-1):
        with pytest.raises(ValueError):decode_request(wire[:i]+b"\0")
    with pytest.raises(ValueError):decode_request(wire[:-1]+b"\x80\0")
    with pytest.raises(ValueError):decode_request(wire[:10]+b"F"+wire[11:])


def test_ram_envelope_validation(tmp_path):
    import shutil,subprocess
    from modem import k6mod
    from host_link import gc_sections
    cc=shutil.which("cc")
    if not cc:pytest.skip("C compiler required")
    root=Path(__file__).resolve().parents[1]
    src=tmp_path/"verify.c"
    src.write_text(r'''
#include <stdio.h>
#include <stdint.h>
#include "k6_mod.h"
int main(int argc,char **argv){
  static uint8_t buf[4096];
  km_init();
  for(int a=1;a<argc;a++){
    unsigned n=0,v;
    for(const char *p=argv[a]; p[0]&&p[1]&&n<sizeof(buf);p+=2){
      if(sscanf(p,"%2x",&v)!=1)return 1;
      buf[n++]=(uint8_t)v;
    }
    int r=km_live_verify(buf,n);
    printf("%d %d %u\n",r,km_armed(),km_serial_highwater());
  }
}
''')
    key=bytes(range(16))
    binary=tmp_path/"verify"
    def build(extra):
        subprocess.run([cc,"-O1","-ffunction-sections","-fdata-sections",gc_sections(),*extra,
             "-I"+str(root/"c/k6"),"-I"+str(root/"c"),
             str(src),str(root/"c/k6/k6_mod.c"),"-o",str(binary)],check=True)
    def verify(images):
        r=subprocess.check_output([str(binary),*(i.hex() for i in images)],text=True)
        return [tuple(map(int,line.split())) for line in r.splitlines()]
    def image(serial,key=key,**kw):
        return k6mod.image(b"\x70\x47",0,serial,key,**kw)
    valid=image(10)
    build(["-DKM_KEY="+",".join(map(str,key))])
    assert verify([image(9,key=bytes(16)),valid,valid,image(11,level=3),image(11,lib=1),
                   image(11)+b"\0",image(11)[:-1],image(11)])==[
        (-4,0,0),(2,1,10),(-6,0,10),(-3,0,10),(-3,0,10),
        (-3,0,10),(-3,0,10),(2,1,11)]
    build([])
    assert verify([valid])==[(-5,0,0)]
    build(["-DKM_CABLE_ONLY"])
    assert verify([valid,image(10,key=None),image(10,key=None)])==[(-4,0,0),(2,1,10),(-6,0,10)]
