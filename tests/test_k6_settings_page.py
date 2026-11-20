"""Exercise exact settings page formatting on a host compiler."""
import ctypes as C
from pathlib import Path
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from k6_settings_page import NAMES
@pytest.fixture(scope='module')
def page(tmp_path_factory):
    d=tmp_path_factory.mktemp('settings-page')
    (d/'settings_ids.h').write_text('\n'.join(f'#define S_{n} {i}' for i,n in enumerate(NAMES)))
    (d/'host.c').write_text('#include "'+str(ROOT/'c/k6/settings_page.c')+'"\nint r(unsigned f,int v,unsigned a,char *o){return render(f,v,a,o);}\n')
    subprocess.run(['cc','-shared','-fPIC','-I'+str(d),str(d/'host.c'),'-o',str(d/'page.so')],check=True)
    return C.CDLL(str(d/'page.so'))
@pytest.mark.parametrize('name,value,aux,expected',[
 ('Step',0,1250,'12.50k'),('TxPr',3,1,'EL'),('TxPr',3,0,'HIGH'),
 ('Tdr',1,0,'ON'),('Wn',0,0,'WIDE'),('ScanMd',2,0,'STOP'),
 ('Rit',-3,0,'-30Hz'),('Rit',3,0,'+30Hz'),('VoxDly',8,0,'1.3S'),
 ('Rptrl',5,0,'500MS'),('AprsPath',0,0,'W1-1,W2-1'),('AprsSsid',15,0,'-15'),
 ('Sql',9,0,'9'),('Rtone',0,1750,'1750Hz'),('Save',0,0,'OFF')])
def test_scalar_format(page,name,value,aux,expected):
    out=C.create_string_buffer(32);n=page.r(NAMES.index(name),value,aux,out)
    assert out.raw[:n].decode()==expected
