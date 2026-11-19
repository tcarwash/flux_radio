import json
import shutil
import subprocess
from pathlib import Path
import pytest
from modem.k6mod import image_tag

@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_web_cable_integrity():
    root=Path(__file__).resolve().parents[1]
    js="const k=require('./web/k6-installer/k6web.js'); console.log(JSON.stringify([[],[1,2,3],Array.from({length:256},(_,i)=>i)].map(x=>k.hex(k.imageTag(null,Uint8Array.from(x))))));"
    out=json.loads(subprocess.check_output(['node','-e',js],cwd=root))
    assert out==[image_tag(None, b).hex() for b in (b'',b'\x01\x02\x03',bytes(range(256)))]
