import sys
from pathlib import Path
import pytest
from host_link import gc_sections
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from k6_install import Cable, CMD_CAPS, InstallError, k6mod

KEY = bytes(range(16))
RECORD = b'K6CP' + bytes([2, 7, 22, 0]) + bytes(range(1,9))

def response(key, nonce, record=RECORD):
    return record + b'\0' + k6mod.siphash24(key, b'K6CAPAU1'+record+nonce).to_bytes(8,'little')

def test_fresh_proof_and_replay(monkeypatch):
    import k6_install
    nonces = iter([b'a'*16,b'b'*16])
    monkeypatch.setattr(k6_install.secrets,'token_bytes',lambda n: next(nonces))
    cable = object.__new__(Cable)
    seen = []
    def call(cmd, data):
        assert cmd == CMD_CAPS
        seen.append(data)
        return response(KEY, seen[0])
    cable.call = call
    assert cable.authenticated_capabilities(KEY)['scanner']
    with pytest.raises(InstallError, match='expected module key'):
        cable.authenticated_capabilities(KEY)

@pytest.mark.parametrize('mode',['wrong-key','tampered','unkeyed','legacy'])
def test_proof_rejected(mode):
    cable = object.__new__(Cable)
    def call(cmd,data):
        if mode=='wrong-key': return response(b'x'*16,data)
        if mode=='tampered':
            r=response(KEY,data)
            return r[:5]+bytes([1])+r[6:]
        if mode=='unkeyed': return RECORD+bytes([251])+bytes(8)
        return RECORD
    cable.call=call
    with pytest.raises(InstallError):
        cable.authenticated_capabilities(KEY)


@pytest.mark.parametrize('keyed', [True, False])
def test_c_proof_matches_host(tmp_path, keyed):
    import subprocess
    import shutil
    import ctypes
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('requires host C compiler')
    root = Path(__file__).resolve().parents[1]
    # Link only the proof and its dependencies, using section garbage collection.
    source = tmp_path / 'proof.c'
    source.write_text('#include "k6_mod.c"\nint main(void) { uint8_t record[16]={75,54,67,80,2,7,22,0,1,2,3,4,5,6,7,8}, nonce[16]={0}, tag[8]={0}; int rc=km_capability_proof(record,nonce,tag); if(rc) return 5; for(int i=0;i<8;i++) printf("%02x",tag[i]); return 0; }\n')
    exe = tmp_path / 'proof'
    flags = ['-DKM_KEY='+','.join(map(str,KEY))] if keyed else []
    subprocess.run([cc,'-include','stdio.h','-I'+str(root/'c/k6'),'-ffunction-sections','-fdata-sections',gc_sections(),*flags,str(source),'-o',str(exe)],check=True)
    result = subprocess.run([str(exe)],capture_output=True,text=True)
    if keyed:
        assert result.returncode == 0
        assert result.stdout == response(KEY,bytes(16))[17:].hex()
    else:
        assert result.returncode == 5 and result.stdout == ''
