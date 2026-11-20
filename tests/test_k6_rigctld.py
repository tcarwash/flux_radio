import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import socket
import subprocess
import threading

import pytest
from k6_rigctld import Rig, Server

class MockLink:
    def __init__(self):
        self.hz = 146520000
        self.tunes = []
    def status(self):
        return dict(master=0, watching=0, transmitting=False, standby_idle=True, sleeping=False,
                    rssi_dbm=-105, squelch_open=True, vfos=[
            dict(hz=self.hz, mode="FM", wide=False),
            dict(hz=433500000, mode="CW", wide=False)])
    def tune(self, hz):
        self.tunes.append(hz)
        self.hz = hz
        return self.status()

def test_control_and_refusals():
    link = MockLink()
    r = Rig(link)
    assert r.execute("f") == "146520000\n"
    assert r.execute("F 146550000") == "RPRT 0\n"
    assert r.execute(r"\get_freq") == "146550000\n"
    assert r.execute("m") == "FM\n8000\n"
    assert r.execute("v") == "VFOA\n"
    assert r.execute("t") == "0\n"
    for cmd in ["T 1", "T 0", "M CW 500", "V VFOB"]:
        assert r.execute(cmd) == "RPRT -11\n"
    for cmd in ["F nan", "F inf", "F 200000000", "F 146550001"]:
        assert r.execute(cmd) == "RPRT -1\n"
    assert link.tunes == [146550000]

@pytest.fixture
def server(socket_enabled):
    link = MockLink()
    with Server(("127.0.0.1", 0), Rig(link)) as s:
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        yield s, link
        s.shutdown()
        t.join()

def test_socket(server):
    s, link = server
    with socket.create_connection(s.server_address, timeout=2) as c:
        f = c.makefile("rwb")
        f.write(b"f\nF 433500000\nf\nT 1\n"); f.flush()
        assert [f.readline() for _ in range(4)] == [
            b"146520000\n", b"RPRT 0\n", b"433500000\n", b"RPRT -11\n"]

@pytest.mark.skipif(not shutil.which("rigctl"), reason="Hamlib not installed")
def test_real_hamlib_net_backend(server):
    s, link = server
    cmd = ["rigctl", "-m", "2", "-r", f"127.0.0.1:{s.server_address[1]}"]
    result = subprocess.run(cmd + ["F", "146550000", "f", "m", "v", "t"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "146550000" in result.stdout and "FM" in result.stdout
    assert "VFOA" in result.stdout
    assert link.tunes == [146550000]


class CatLink(MockLink):
    master = 0
    mode = "FM"
    wide = False
    def status(self):
        s = super().status()
        s["master"] = self.master
        s["vfos"][self.master].update(mode=self.mode, wide=self.wide)
        return s
    def set_mode(self, mode, wide):
        self.mode, self.wide = mode, wide
        return self.status()
    def select_vfo(self,index):
        self.master=index
        return self.status()


def test_optional_mode_vfo():
    link=CatLink()
    r=Rig(link,["status","tune","mode","vfo"])
    assert r.execute("M USB 2400")=="RPRT 0\n"
    assert r.execute("m")=="USB\n2400\n"
    assert r.execute("V VFOB")=="RPRT 0\n"
    assert r.execute("v")=="VFOB\n"
    assert r.execute("M FM 12345")=="RPRT -1\n"
    assert r.execute("T 1")=="RPRT -11\n"

@pytest.mark.skipif(not shutil.which("rigctl"), reason="Hamlib not installed")
def test_real_hamlib_mode_and_vfo(socket_enabled):
    link = CatLink()
    with Server(("127.0.0.1", 0), Rig(link, ["status", "tune", "mode", "vfo"])) as s:
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        try:
            result = subprocess.run(
                ["rigctl", "-m", "2", "-r", f"127.0.0.1:{s.server_address[1]}",
                 "M", "USB", "2400", "m", "V", "VFOB", "v"],
                capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            assert "USB" in result.stdout and "VFOB" in result.stdout, result.stdout
            assert link.mode == "USB" and link.master == 1
        finally:
            s.shutdown()
            t.join()

def test_rx_telemetry_and_invalid_context():
    link=MockLink()
    rig=Rig(link)
    assert rig.execute("l STRENGTH")=="-12\n"
    assert rig.execute(r"\get_dcd")=="1\n"
    assert rig.execute("L SQL 0.5")=="RPRT -11\n"
    assert rig.execute("l RAWSTR")=="RPRT -11\n"
    original=link.status
    for invalid in (dict(watching=1),dict(transmitting=True),dict(sleeping=True),
                    dict(standby_idle=False)):
        link.status=lambda:dict(original(),**invalid)
        assert rig.execute("l STRENGTH")=="RPRT -9\n"
        assert rig.execute(r"\get_dcd")=="RPRT -9\n"
    link.status=lambda:dict(original(),squelch_open=False,rssi_dbm=-83)
    assert rig.execute("l STRENGTH")=="10\n"
    assert rig.execute(r"\get_dcd")=="0\n"

@pytest.mark.skipif(not shutil.which("rigctl"), reason="Hamlib not installed")
def test_real_hamlib_rx_meter(server):
    s,_=server
    cmd=["rigctl","-m","2","-r",f"127.0.0.1:{s.server_address[1]}"]
    result=subprocess.run(cmd+["l","STRENGTH","get_dcd"],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0, result.stderr
    assert result.stdout.splitlines()==["-12","1"],result.stdout


def test_capability_advertisement_is_consistent_with_setters():
    link=CatLink()
    for features,can_tune,can_vfo in [
        (["status"],False,False),
        (["status","tune"],True,False),
        (["status","tune","vfo"],True,True),
    ]:
        rig=Rig(link,features)
        capabilities=rig.execute(r"\dump_state")
        assert f"has_set_freq={int(can_tune)}" in capabilities
        assert f"has_set_vfo={int(can_vfo)}" in capabilities
        assert rig.execute("F 146550000")==("RPRT 0\n" if can_tune else "RPRT -11\n")
        assert rig.execute("V VFOB")==("RPRT 0\n" if can_vfo else "RPRT -11\n")
        for cmd in ("T 1","T 0",r"\set_ptt 1",r"\set_ptt 0",
                    r"\set_powerstat 0","L SQL 5"):
            assert rig.execute(cmd)=="RPRT -11\n"


def test_discovery_on_real_hamlib_does_not_advertise_ptt(socket_enabled):
    link=CatLink()
    with Server(("127.0.0.1",0),Rig(link,["status","tune","mode","vfo"])) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            with socket.create_connection(server.server_address,timeout=2) as sock:
                sock.sendall(b"\\dump_state\n")
                buf=b""
                while b"done\n" not in buf:
                    part=sock.recv(8192)
                    assert part
                    buf+=part
                data=buf.decode("ascii")
                assert "has_set_vfo=1" in data and "has_set_freq=1" in data
                assert "ptt_type=0x0" in data
                assert "0x0\n0x0\n0x40000000" in data
            result=subprocess.run(
                ["rigctl","-m","2","-r",
                 f"127.0.0.1:{server.server_address[1]}","t"],
                capture_output=True,text=True,timeout=10)
            assert result.returncode==0 and result.stdout.strip()=="0"
        finally:
            server.shutdown()
            worker.join(timeout=2)


def test_bad_commands_do_not_mutate_radio():
    link=CatLink()
    rig=Rig(link,["status","tune","mode","vfo"])
    initial=link.status()
    for command in ("F 433500000.5","F 0","F nan","F -1",
                    "M FM 2000","M BOGUS 0","V Other",
                    "T 1","T 0",r"\set_ptt 1",
                    "Z 123","l UNKNOWN","l ? UNKNOWN"):
        assert rig.execute(command) in ("RPRT -1\n","RPRT -11\n")
    assert link.status()==initial
