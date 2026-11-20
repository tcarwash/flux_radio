"""Exercise the exact overlay C implementation on host, with canonical tone tables."""
import ctypes as C
from pathlib import Path
import re, subprocess, pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def page(tmp_path_factory):
    d=tmp_path_factory.mktemp('channel-page');rust=ROOT/'firmware/k6-aura/build/aura-fd6818b-port/src/app/settings.rs'
    if not rust.exists():pytest.skip('generated firmware source required')
    s=rust.read_text()
    h=''
    for name,n in [('DCS',105),('CTCSS',50)]:
        r=re.search(r'const '+name+r'_TABLE: \[u16; '+str(n)+r'\] = \[(.*?)\];',s,re.S).group(1).replace('0o','0')
        h+=f'static const uint16_t {name.lower()}[{n}]={{'+r+'};\n'
    (d/'channel_tones.h').write_text(h)
    (d/'host.c').write_text('#include "'+str(ROOT/'c/k6/channel_page.c')+'"\nint r(unsigned char*a,unsigned f,char*out){return render(a,f,out);}\nint s(unsigned char*a,unsigned f,unsigned op,int v){return scalar(a,f,op,v);}\n')
    subprocess.run(['cc','-shared','-fPIC','-I'+str(d),str(d/'host.c'),'-o',str(d/'page.so')],check=True)
    return C.CDLL(str(d/'page.so'))
def record():
    a=(C.c_uint8*39)();a[:4]=[0x00,0x00,0x35,0x43];a[38]=1;return a
def render(page,a,f):
    out=C.create_string_buffer(32);n=page.r(a,f,out);return out.raw[:n].decode()
def test_frequency(page):assert render(page,record(),0)=='433.50000'
@pytest.mark.parametrize('field,maximum',[(1,2),(5,3),(9,19)])
def test_clamp(page,field,maximum):
    a=record();page.s(a,field,3,maximum);assert page.s(a,field,1,0)==maximum;assert page.s(a,field,2,0)==maximum-1;assert page.s(a,field,3,maximum)==maximum
@pytest.mark.parametrize('field,mask',[(6,64),(7,4),(8,8)])
def test_flags(page,field,mask):
    a=record();a[15]=0x80;assert page.s(a,field,1,0)==1 and a[15]==0x80|mask;assert page.s(a,field,2,0)==0 and a[15]==0x80
@pytest.mark.parametrize('field',[3,4])
def test_tones(page,field):
    a=record();assert page.s(a,field,0,0)==-1;assert page.s(a,field,1,0)==0;assert render(page,a,field)=='67.0Hz';assert page.s(a,field,2,0)==-1;assert page.s(a,field,2,0)==259;assert render(page,a,field)=='D754I';assert page.s(a,field,1,0)==-1;assert render(page,a,field)=='OFF'
def test_power(page):
    a=record();page.s(a,5,3,3);assert render(page,a,5)=='EL';a[38]=0;assert render(page,a,5)=='HIGH'
