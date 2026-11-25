"""Every live transmitter packet is byte-identical to the independent public wire builder."""
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'tests/data/k6/sotto_live_packet_parity.c'

def test_sotto_live_packet_parity():
    with tempfile.TemporaryDirectory() as td:
        exe=Path(td)/'packet-parity'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        '-I',str(ROOT),str(SRC),'-o',str(exe)],check=True)
        r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
        assert '2097 packet equality checks plus 144-segment epoch/wrap/backpressure' in r.stdout
