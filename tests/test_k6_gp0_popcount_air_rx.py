"""Standalone pre-decimator raw group receiver, RX only, no SDR needed."""
import subprocess
import sys
from pathlib import Path
import numpy as np
import pytest
from tools.k6_gp0_popcount_air import (parse_physical,PACKETS,N_SAMPLES,FS,assemble)
from tools.k6_gp0_popcount_air_rx import downconvert_u8iq,capture_iq
ROOT=Path(__file__).resolve().parents[1]


def test_direct_script_usage_from_unrelated_directory(tmp_path):
    script=ROOT/'tools/k6_gp0_popcount_air_rx.py'
    p=subprocess.run([sys.executable,str(script),'--help'],cwd=tmp_path,capture_output=True,text=True,timeout=15)
    assert p.returncode==0 and '--fine-offset-hz' in p.stdout


def test_raw_sync_receiver_can_demodulate_new_da_packet(host=None):
    # Use exact C frame bytes via library fixture from sibling host test.
    from modem import pager
    from tools.k6_fsk_sotto_sync_rx import find_frames
    import ctypes as C
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        so=Path(td)/'raw.so'
        subprocess.run(['cc','-O2','-shared','-fPIC','-I'+str(ROOT/'c/k6'),
             '-I'+str(ROOT/'c/k6/mod'),str(ROOT/'c/k6/mod/gp0_popcount_air_tx.c'),
             str(ROOT/'tests/data/k6/gp0_popcount_air_host.c'),'-o',str(so)],check=True,capture_output=True)
        lib=C.CDLL(str(so))
        lib.gpraw_host_run.argtypes=[C.c_int];lib.gpraw_host_run.restype=C.c_int
        lib.gpraw_host_packet.argtypes=[C.c_uint];lib.gpraw_host_packet.restype=C.c_void_p
        assert lib.gpraw_host_run(0)==PACKETS
        frame=C.string_at(lib.gpraw_host_packet(0),66)
    sync=np.unpackbits(np.frombuffer(bytes([0x55])*16+pager.SYNC+frame,dtype=np.uint8))
    fs=128000;baud=4800
    n=int(np.ceil(len(sync)*fs/baud))
    idx=np.minimum(np.arange(n)*baud//fs,len(sync)-1)
    freq=(sync[idx].astype(float)*2-1)*2400
    iq=np.concatenate((np.zeros(fs//10,np.complex64),
                       np.exp(2j*np.pi*np.cumsum(freq/fs)).astype(np.complex64),
                       np.zeros(fs//10,np.complex64)))
    def adapter(raw):
        p=parse_physical(raw)
        return dict(msg_id=p['token']%255,seq=p['seq'],frames=[]) if p else None
    r=find_frames(iq,fs=fs,payload_bytes=66,
                  rate_factors=(.998,1.,1.002),min_correlation=.75,
                  unpack_packet=adapter)
    assert r['crc_valid']==1 and frame in r['byte_payloads']


def test_capture_frontend_rejects_invalid_iq():
    with pytest.raises(ValueError):downconvert_u8iq(np.array([0],dtype=np.uint8),offset_hz=1)
    class NoReceiver:
        def read(self,n):return bytes(n)
    with pytest.raises(ValueError):capture_iq(NoReceiver(),3,fine_offset_hz=-31300)
