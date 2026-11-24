import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_live import LiveLink, LiveUnavailable, request_bytes, decode_status, STATUS, READ, TUNE
from k6_install import crc16_xmodem, cps_frame, InstallError


def status_data():
    return b"FLIV\x01" + struct.pack("<BBBIIHiBBBB", 0, 1, 6, 146520000, 446000000, 812, -92, 0, 3, 1, 0)


class Port:
    def __init__(self, reply=None):
        self.reply = reply
        self.buf = b""
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        if self.reply:
            self.buf += self.reply(data)

    def read(self, n):
        result, self.buf = self.buf[:n], self.buf[n:]
        return result


def test_wire_format_and_status():
    req = request_bytes(TUNE, 7, 146520000)
    assert len(req) == 14 and req[:6] == b"FLXLV1"
    assert req[-2:] == struct.pack(">H", crc16_xmodem(req[:-2]))
    p = Port(lambda d: b"K6 DEBUG\n" + cps_frame(d[6], d[7], b"\0" + status_data()))
    result = LiveLink(p, initial_seq=0).status()
    assert result["vfos"][0]["hz"] == 146520000
    assert result["vfos"][1]["mode"] == "CW"
    assert result["battery_volts"] == 8.12 and result["rssi_dbm"] == -92
    assert p.writes == [request_bytes(STATUS, 1)]


def test_read_bounds_and_busy():
    p = Port(lambda d: cps_frame(d[6], d[7], b"\x00" + bytes(range(64))))
    link = LiveLink(p)
    assert link.read(0xb000, 64) == bytes(range(64))
    assert len(p.writes) == 1
    p.reply = lambda d: cps_frame(d[6],d[7],b"\2")
    with pytest.raises(InstallError, match="lacks live bulk"): link.read(0xb040, 1)
    with pytest.raises(ValueError): link.read(1, 64)
    p.reply = lambda d: cps_frame(d[6], d[7], b"\x01")
    with pytest.raises(InstallError, match="busy"): link.read(0, 64)


def test_no_legacy_handshake_or_end_even_on_timeout():
    p = Port()
    with pytest.raises(LiveUnavailable):
        LiveLink(p, timeout=.001, initial_seq=0).status()
    assert p.writes == [request_bytes(STATUS, 1)]


def test_tune_requires_valid_range_and_returns_status():
    def reply(d):
        return cps_frame(d[6], d[7], b"\0" + (status_data() if d[6] == STATUS else b""))
    p = Port(reply)
    link = LiveLink(p)
    with pytest.raises(ValueError): link.tune(300000000)
    assert not p.writes
    assert link.tune(146520000)["vfos"][0]["hz"] == 146520000
    assert [x[6] for x in p.writes] == [TUNE, STATUS]


def test_status_rejects_bad_version():
    with pytest.raises(InstallError): decode_status(b"FLIV\x02" + status_data()[5:])


def test_catalog_routes_and_explicit_legacy(monkeypatch):
    import k6_store
    import k6_radio_memories
    from contextlib import nullcontext
    import k6_install
    import k6_port_lock
    calls = []
    def read(port, what, *, legacy=False):
        calls.append((port, what, legacy))
        return {"lat": 45, "lon": -122}
    monkeypatch.setattr(k6_radio_memories, "read_radio", read)
    monkeypatch.setattr(k6_port_lock, "serial_lock", lambda _: nullcontext())
    p = Port(lambda d: cps_frame(d[6], d[7], b"\0" + (status_data() if d[6] == STATUS else b"")))
    monkeypatch.setattr(k6_install, "open_port", lambda _: nullcontext(p))
    client = k6_store.create_app().test_client()
    assert client.post("/radio/live/status").status_code == 400
    with client.session_transaction() as session:
        session["radio_port"] = "mock-port"
    client.get("/radio")
    client.get("/memories")
    with client.session_transaction() as session:
        live_token=session["live_radio_token"]
        memory_token=session["memory_token"]
    assert client.post("/radio/live/status").status_code == 403
    assert client.post("/radio/live/status", json={"token":live_token}).json["version"] == 1
    assert client.post("/radio/live/tune", json={"hz": 146520000,"token":live_token}).status_code == 200
    assert client.post("/radio/live/tune", json={"hz": 300000000,"token":live_token}).status_code == 400
    assert client.post("/radio/live/ptt").status_code == 404
    assert client.post("/memories/radio/location",json={"token":memory_token}).status_code == 200
    assert client.post("/memories/radio/location?legacy=1",json={"token":memory_token}).status_code == 200
    import k6_memory_radio
    monkeypatch.setattr(k6_memory_radio,"operate_live_read",lambda port:{"channels":[],"opaque_slots":[7],"pending":True})
    assert client.post("/memories/radio/live-bank",json={"token":memory_token}).json["pending"]
    assert client.post("/memories/radio/live-bank").status_code == 403
    assert calls == [("mock-port", "location", False), ("mock-port", "location", True)]


def test_run_collects_framed_reports_and_completion_without_retry():
    from k6_live import RUN, RUN_EVENT
    def reply(d):
        seq = d[7]
        return (cps_frame(RUN, seq, b"\0") +
                cps_frame(RUN_EVENT, (seq + 1) % 256, b"\0unrelated") +
                cps_frame(RUN_EVENT, seq, b"\0gp0 baseline") +
                cps_frame(RUN_EVENT, seq, b"\0gp0 correlation") +
                cps_frame(RUN_EVENT, seq, b"\1" + struct.pack("<BiBi", 25, 865, 1, -6)))
    port = Port(reply)
    lines = []
    result = LiveLink(port, initial_seq=0).run_module(25, on_report=lines.append)
    assert result == dict(slot=25, load=865, returned=True, result=-6,
                          reports=["gp0 baseline", "gp0 correlation"])
    assert lines == result["reports"]
    assert port.writes == [request_bytes(RUN, 1, 25)]


def test_run_refusal_timeout_and_higher_api():
    from k6_live import RUN, RUN_EVENT
    p = Port(lambda d: cps_frame(RUN, d[7], b"\2"))
    link = LiveLink(p, initial_seq=0, timeout=.001)
    with pytest.raises(ValueError):
        link.run_module(32)
    assert not p.writes
    with pytest.raises(InstallError, match="Unsupported"):
        link.run_module(25)
    assert len(p.writes) == 1
    p.reply = lambda d: cps_frame(RUN, d[7], b"\0")
    with pytest.raises(LiveUnavailable, match="Do not retry"):
        link.run_module(25, timeout=.001, allow_higher_api=True)
    assert p.writes[-1] == request_bytes(RUN, 2, 0x119)
    assert len(p.writes) == 2
    p.reply = lambda d: b""
    with pytest.raises(LiveUnavailable, match="acknowledgment lost"):
        link.run_module(25)
    assert len(p.writes) == 3
    p.reply = lambda d: (cps_frame(RUN, d[7], b"\0") +
                         cps_frame(RUN_EVENT, d[7], b"\1" + struct.pack("<BiBi", 25, -200, 0, 0)))
    assert link.run_module(25)["result"] is None


def test_caps_and_cat_commands():
    from k6_live import CAPS, MODE, VFO
    def reply(d):
        return cps_frame(d[6],d[7],b"\0" + (
            b"FLCP\x01" + struct.pack("<I",127) if d[6] == CAPS else
            status_data() if d[6] == STATUS else b""))
    p=Port(reply)
    link=LiveLink(p,initial_seq=0)
    assert link.capabilities()["features"] == ["status","read","tune","run","mode","vfo","health"]
    link.set_mode("USB",True)
    link.select_vfo(1)
    assert p.writes == [request_bytes(CAPS,1),request_bytes(MODE,2,0x102),
                        request_bytes(STATUS,3),request_bytes(VFO,4,1),request_bytes(STATUS,5)]
    with pytest.raises(ValueError):
        link.set_mode("LSB")
    with pytest.raises(ValueError):
        link.select_vfo(2)
    assert len(p.writes)==5


def test_bulk_negotiation_and_recovery(monkeypatch):
    import k6_live as live
    from k6_live import BURST, BURST_DATA, BURST_END, CAPS
    class BulkPort(Port):
        baudrate = 115200
        def reset_input_buffer(self):
            self.buf = b""
    p = BulkPort()
    attempts = []
    data = bytes((i * 73 + i//256) & 255 for i in range(16384))
    def respond(d):
        cmd,seq,arg=struct.unpack_from("<BBI",d,6)
        assert p.baudrate==115200
        if cmd==CAPS:
            return cps_frame(cmd,seq,b"\0FLCP\x01"+struct.pack("<I",128))
        if cmd==STATUS:
            return cps_frame(cmd,seq,b"\0"+status_data())
        assert cmd==BURST
        start=(arg&0x7fff)*64
        count=(arg>>15)&31
        rate=live.BAUDS[arg>>20]
        attempts.append((start,count,rate))
        ack=cps_frame(cmd,seq,b"\0"+struct.pack("<I",96000000//round(96000000/rate)))
        frames=b"".join(cps_frame(BURST_DATA,seq,struct.pack("<I",start+i*256)+data[start+i*256:start+(i+1)*256]) for i in range(count))
        end=cps_frame(BURST_END,seq,struct.pack("<BB4I",0,count,0,0,0,0))
        # Reject 500k during validation, then lose a frame at 192k in the backup.
        if rate==500000 or (rate==192000 and start>=4096):
            frames=frames[268:]
        return ack+frames+end
    p.reply=respond
    monkeypatch.setattr(live.time,"sleep",lambda _:None)
    link=LiveLink(p,initial_seq=0,timeout=.01)
    assert link.read(0,8192)==data[:8192]
    assert (0,16,500000) in attempts and (0,16,192000) in attempts
    assert any(a>=4096 and r==115200 for a,_,r in attempts)
    assert p.baudrate==115200 and link._download_rate==115200
    assert all(x.startswith(live.MAGIC) for x in p.writes)


def test_bulk_busy_does_not_retry(monkeypatch):
    import k6_live as live
    from k6_live import BURST,CAPS
    class B(Port):
        baudrate=115200
    p=B(lambda d: cps_frame(d[6],d[7],b"\0FLCP\x01"+struct.pack("<I",128) if d[6]==CAPS else b"\1"))
    with pytest.raises(InstallError,match="busy"):
        LiveLink(p).read(0,4096)
    assert [d[6] for d in p.writes]==[CAPS,BURST]


def test_installer_primary_live_routes(tmp_path,monkeypatch):
    import k6_install as ki
    import k6_live
    import k6_port_lock
    from contextlib import nullcontext
    calls=[]
    class FakePort:
        port="mock-port"
        def close(self):calls.append("close")
    class FakeLive:
        def __init__(self,p):calls.append("live")
        def status(self):calls.append("status")
    monkeypatch.setattr(ki,"open_port",lambda _:FakePort())
    monkeypatch.setattr(k6_live,"LiveLink",FakeLive)
    monkeypatch.setattr(k6_port_lock,"serial_lock",lambda _:nullcontext())
    monkeypatch.setattr(ki,"open_cable",lambda *a,**k:pytest.fail("legacy handshake not allowed"))
    monkeypatch.setattr(ki,"backup",lambda link,path,log:calls.append(("backup",path)))
    monkeypatch.setattr(ki,"report_library",lambda link,log,live=False:calls.append(("inventory",live)))
    path=str(tmp_path/"full.bin")
    assert ki.main(["--port","mock-port","backup","-o",path])==0
    assert ki.main(["--port","mock-port","status"])==0
    assert calls==["live","status",("backup",path),"close","live","status",("inventory",True),"close"]


def test_ram_commit_is_not_retried():
    import k6_live as live
    from k6_live_v2 import decode_request,RAM_BEGIN,RAM_CHUNK,RAM_COMMIT
    from modem import k6mod
    env=k6mod.image(b"\x70\x47",0,7,None,level=1)
    def reply(d):
        if d.startswith(b"FLXLV1"):
            cmd,seq,arg=struct.unpack_from("<BBI",d,6)
            if cmd==live.CAPS:
                return cps_frame(cmd,seq,b"\0FLCP\x01"+struct.pack("<I",1024))
            if cmd==live.WATERMARK:
                return cps_frame(cmd,seq,b"\0"+struct.pack("<I",6))
        cmd,seq,arg,payload=decode_request(d)
        if cmd==RAM_COMMIT:return b""
        return cps_frame(cmd,seq,b"\0")
    p=Port(reply)
    with pytest.raises(LiveUnavailable,match="commit acknowledgment lost"):
        LiveLink(p,timeout=.002).run_ram(env)
    frames=[decode_request(x) for x in p.writes if x.startswith(b"FLXLV2")]
    assert [x[0] for x in frames]==[RAM_BEGIN,RAM_CHUNK,RAM_COMMIT]
    assert frames[1][3]==env

@pytest.mark.parametrize("state,load,returned,ret,expected", [
    (0,0,0,0,("unknown",None,None,None)),
    (1,123,0,0,("accepted",123,None,None)),
    (2,123,1,-7,("completed",123,True,-7)),
    (2,123,0,0,("completed",123,False,None)),
])
def test_ram_job_status_is_read_only(state,load,returned,ret,expected):
    from k6_live import RAM_STATUS
    p=Port(lambda d:cps_frame(d[6],d[7],b"\0"+struct.pack("<BiBi",state,load,returned,ret)))
    link=LiveLink(p,initial_seq=0)
    r=link.ram_status(42)
    assert r["job_id"]==42
    assert tuple(r[k] for k in ("state","load","returned","result"))==expected
    assert p.writes==[request_bytes(RAM_STATUS,1,42)]
    for invalid in (0,-1,2**32,True):
        with pytest.raises(ValueError):link.ram_status(invalid)
    assert len(p.writes)==1

@pytest.mark.parametrize("data",[b"",struct.pack("<BiBi",3,10,0,0),
    struct.pack("<BiBi",0,10,0,0),struct.pack("<BiBi",1,10,1,0),
    struct.pack("<BiBi",2,10,0,1)])
def test_ram_job_status_rejects_bad_reply(data):
    p=Port(lambda d:cps_frame(d[6],d[7],b"\0"+data))
    with pytest.raises(InstallError,match="Invalid RAM job status"):
        LiveLink(p).ram_status(42)


class FastUploadPort(Port):
    """Mock a target that checks all fast-phase commands and baud transitions."""
    baudrate=115200
    def __init__(self, fault=None):
        super().__init__(self.respond)
        self.fault=fault
        self.phase=None
        self.offset=0
        self.events=[]
        self.upload=bytearray()

    def reset_input_buffer(self):
        self.buf=b""

    def respond(self,d):
        from k6_live import CAPS,WATERMARK,RUN_EVENT
        from k6_live_v2 import (decode_request,MAGIC_V2,UPLOAD_PROBE,RAM_FAST_BEGIN,
                               RAM_READY,RAM_BEGIN,RAM_CHUNK,RAM_COMMIT)
        if d.startswith(b"FLXLV1"):
            assert self.baudrate==115200
            cmd,seq,arg=struct.unpack_from("<BBI",d,6)
            self.events.append(cmd)
            if cmd==CAPS:
                return cps_frame(cmd,seq,b"\0FLCP\x01"+struct.pack("<I",1024|4096))
            if cmd==WATERMARK:
                return cps_frame(cmd,seq,b"\0"+struct.pack("<I",6))
            assert cmd==STATUS
            self.phase=None
            return b"" if self.fault=="no_recovery" else cps_frame(cmd,seq,b"\0"+status_data())
        fast=not d.startswith(MAGIC_V2)
        assert self.baudrate==(192000 if fast else 115200)
        cmd,seq,arg,p=decode_request(MAGIC_V2+d if fast else d)
        self.events.append(cmd)
        if cmd in (UPLOAD_PROBE,RAM_FAST_BEGIN,RAM_BEGIN):
            self.phase=cmd
            self.offset=0
            self.total=arg
            if cmd==RAM_BEGIN:return cps_frame(cmd,seq,b"\0")
            if self.fault=="busy":return cps_frame(cmd,seq,b"\1")
            return cps_frame(cmd,seq,b"\0"+struct.pack("<I",192000))
        if cmd==RAM_CHUNK:
            assert arg==self.offset
            self.offset+=len(p)
            if self.phase==UPLOAD_PROBE:
                assert p==bytes((i*73+i//256)&255 for i in range(arg,self.offset))
                if self.fault in ("probe","no_recovery"):return b""
            else:
                self.upload.extend(p)
                if self.fault=="stage":return b""
            return cps_frame(cmd,seq,b"\0")
        if cmd==RAM_READY:
            assert self.phase==RAM_FAST_BEGIN and self.offset==self.total
            return b"" if self.fault=="ready" else cps_frame(cmd,seq,b"\0")
        assert cmd==RAM_COMMIT and self.offset==self.total
        code=len(self.upload)-24
        return cps_frame(cmd,seq,b"\0"+struct.pack("<i",code))+cps_frame(
            RUN_EVENT,seq,b"\1"+struct.pack("<BiBi",255,code,1,107))


@pytest.mark.parametrize("fault", [None,"probe","stage","ready","busy","no_recovery"])
def test_fast_upload_negotiation_fail_closed(fault,monkeypatch):
    import k6_live as live
    from k6_live_v2 import UPLOAD_PROBE,RAM_FAST_BEGIN,RAM_READY,RAM_BEGIN,RAM_CHUNK,RAM_COMMIT
    from modem import k6mod
    monkeypatch.setattr(live.time,"sleep",lambda _:None)
    env=k6mod.image(b"\x70\x47"+bytes(range(256)),0,7,None,level=1)
    port=FastUploadPort(fault)
    link=LiveLink(port,timeout=.001,initial_seq=250)
    if fault in (None,"probe"):
        assert link.run_ram(env)["result"]==107
        assert port.upload==env
        assert port.events.count(RAM_COMMIT)==1
        assert port.events.count(UPLOAD_PROBE)==1
        if fault is None:
            assert link._upload_rate==192000
            assert port.events.count(RAM_CHUNK)==32+3
            assert port.events[-2:]==[RAM_READY,RAM_COMMIT]
            assert RAM_BEGIN not in port.events
        else:
            assert link._upload_rate==115200
            assert RAM_BEGIN in port.events and RAM_FAST_BEGIN not in port.events
    else:
        with pytest.raises(InstallError):link.run_ram(env)
        assert RAM_COMMIT not in port.events
        assert RAM_BEGIN not in port.events
        if fault in ("stage","ready"):
            assert port.events.count(RAM_FAST_BEGIN)==1
            assert port.events[-1]==STATUS
        if fault=="busy":
            assert port.events[-1]==UPLOAD_PROBE
    assert port.baudrate==115200


@pytest.mark.parametrize("fault", [None,"lost","refused","wrong_readback","read_error"])
def test_library_mutation_never_retries_and_verifies(fault):
    from k6_live_v2 import LIB_BEGIN,LIB_FAST_BEGIN,LIB_COMMIT
    from modem import k6mod
    env=k6mod.image(b"\x70\x47",0,7,None,level=1,lib=24)
    class Link(LiveLink):
        def __init__(self):
            super().__init__(Port())
            self.calls=[]
            self.bank={}
        def capabilities(self):return {"features":["lib_mutate"]}
        def info(self):return {"protocol":5,"arena":11776}
        def serial_watermark(self):return 6
        def library_directory(self):
            if fault=="read_error" and self.calls[-1:]==[LIB_COMMIT]:
                raise InstallError("read failed")
            return {slot:(8192,len(data)) for slot,data in self.bank.items()}
        def library_read(self,slot,n):
            return (self.bank[slot]+b"\xff"*n)[:n]
        def _stage_image(self,image,features,baseline,begin,fast_begin):
            assert image==env
            assert (begin,fast_begin)==(LIB_BEGIN,LIB_FAST_BEGIN)
            self.calls.append(begin)
        def request_v2(self,cmd,arg,payload=b""):
            assert cmd==LIB_COMMIT and arg==len(env)
            assert self.timeout>=10
            self.calls.append(cmd)
            if fault=="refused":return struct.pack("<i",-4)
            self.bank[24]=env if fault!="wrong_readback" else env[:-1]+b"X"
            if fault=="lost":raise LiveUnavailable("lost ACK")
            return struct.pack("<i",len(env))
    link=Link()
    if fault is None:
        assert link.mutate_library(env)["verified"]
    else:
        with pytest.raises(InstallError):link.mutate_library(env)
    assert link.calls==[LIB_BEGIN,LIB_COMMIT]
    assert link.timeout==1.0


def test_library_read_only_recovery_uses_fresh_directory():
    from modem import k6mod
    key=bytes(range(16))
    install=k6mod.image(b"\x70\x47",0,7,key,level=1,lib=24)
    remove=k6mod.image(b"K6ERASE",0,8,key,level=1,lib=24,remove=True)
    class Recovery(LiveLink):
        def __init__(self):
            super().__init__(Port())
            self.bank={24:install}
            self._library_directory={24:(123,42)}
        def info(self):return {"protocol":5,"arena":11776}
        def library_directory(self):
            assert not hasattr(self,"_library_directory")
            self._library_directory={k:(8192,len(v)) for k,v in self.bank.items()}
            return self._library_directory
        def library_read(self,slot,n):return self.bank[slot][:n]
    link=Recovery()
    assert link.verify_library_mutation(install)["verified"]
    assert not link.verify_library_mutation(remove)["verified"]
    link.bank[24]=install[:-1]+b"X"
    assert not link.verify_library_mutation(install)["verified"]
    link.bank.clear()
    assert link.verify_library_mutation(remove)["verified"]
    assert not link.verify_library_mutation(install)["verified"]
    assert not link.port.writes


@pytest.mark.parametrize("keyed", [True,False])
def test_live_module_capability_proof(keyed):
    from modem import k6mod
    from k6_live_v2 import decode_request,MODULE_CAPS
    from k6_live import CAPS
    key=bytes(range(16))
    record=b"K6CP"+bytes([2,1|(8 if keyed else 16),23,0])+b"12345678"
    seen=[]
    def respond(w):
        if w.startswith(b"FLXLV1"):
            return cps_frame(w[6],w[7],b"\0FLCP\x01"+struct.pack("<I",1<<14))
        cmd,seq,arg,p=decode_request(w)
        assert cmd==MODULE_CAPS and arg==0
        if p:
            assert len(p)==16
            seen.append(p)
            return cps_frame(cmd,seq,b"\0"+record+b"\0"+
                k6mod.siphash24(key,b"K6CAPAU1"+record+p).to_bytes(8,"little"))
        return cps_frame(cmd,seq,b"\0"+record)
    link=LiveLink(Port(respond))
    assert link.module_capabilities()["cable_only"]!=keyed
    if keyed:
        assert link.module_capabilities(key)["authenticated"]
        with pytest.raises(InstallError,match="expected key"):
            link.module_capabilities(b"\xff"*16)
        assert len(seen)==2 and seen[0]!=seen[1]
    else:
        with pytest.raises(InstallError,match="cable-only"):
            link.module_capabilities(key)
        assert not seen


def test_live_library_limit_is_advertised_and_ram_limit_unchanged():
    from k6_live_v2 import RAM_MAX,LIB_MAX
    class L(LiveLink):
        def info(self):return {"protocol":5,"arena":11776}
    link=L(Port())
    assert link.library_limit(["lib_mutate"])==4096
    assert link.library_limit(["lib_mutate","lib_large"])==7168
    assert RAM_MAX==4096 and LIB_MAX==10240
    class Balanced(L):
        def info(self):return {"protocol":5,"arena":14400}
    b=Balanced(Port())
    assert b.library_limit(["lib_mutate","lib_large"])==7168
    assert b.library_limit(["lib_mutate","lib_large","lib_10k"])==10240
    assert link.library_limit(["lib_mutate","lib_10k"])==7680
    with pytest.raises(InstallError):
        link.library_limit([])


def test_live_installer_plan_and_remove(tmp_path,monkeypatch):
    import k6_install as ki
    from modem import k6mod
    class L:
        def __init__(self):
            self.bank={}; self.mutations=[]
        def status(self):return {"standby_idle":True,"transmitting":False,"sleeping":False}
        def module_capabilities(self,key=None):
            return {"cable_only":True,"module_loader":True,"module_api_max":23,"build_id":"0102030405060708"}
        def info(self):return {"protocol":5,"slots":32,"arena":11776,"build_id":bytes.fromhex("0102030405060708")}
        def library_limit(self):return 7168
        def library_directory(self):return {k:(8192,len(v)) for k,v in self.bank.items()}
        def library_read(self,slot,n):return (self.bank.get(slot,b"")+b"\xff"*n)[:n]
        def serial_watermark(self):return 20
        def mutate_library(self,img):
            self.mutations.append(img)
            if img[13]==9:del self.bank[img[15]]
            else:self.bank[img[15]]=img
    link=L()
    quiet=lambda *a:None
    def entry(slot,n=4):
        path=tmp_path/f"module{slot}-{n}.bin";path.write_bytes(b"\x2a\x20\x70\x47"+bytes(n-4))
        return {"kind":"module","name":"test","slot":slot,"path":str(path),"level":1}
    monkeypatch.setattr(k6mod,"bin_entry",lambda _:0)
    good=entry(24)
    for invalid in (entry(25,7200),dict(entry(9),kind="page"),dict(entry(25),level=99),good):
        with pytest.raises(InstallError):
            ki.live_load(link,{"library":[good,invalid]},None,ki.Serials(10),quiet)
        assert not link.mutations
    link.bank[25]=b"K6T\x09"+b"x"*20
    with pytest.raises(InstallError):ki.live_load(link,{"library":[good,entry(25)]},None,ki.Serials(10),quiet)
    assert not link.mutations
    ki.live_load(link,{"library":[good]},None,ki.Serials(10),quiet,required_caps=["module_loader"])
    assert len(link.mutations)==1 and link.bank[24][13]==1
    ki.live_load(link,{"library":[good]},None,ki.Serials(10),quiet)
    assert len(link.mutations)==1  # unchanged signed module not rewritten
    with pytest.raises(InstallError):ki.live_remove(link,[24,25],False,None,ki.Serials(30),True,quiet)
    assert len(link.mutations)==1
    ki.live_remove(link,[],True,None,ki.Serials(30),True,quiet)
    assert set(link.bank)=={25} and link.mutations[-1][13]==9


def test_bench_preflight_readonly_and_identity_mismatch(tmp_path):
    from k6_live_bench import validate
    class L:
        def status(self):
            return {"standby_idle":True,"transmitting":False,"sleeping":False,"vfos":[{}],"master":0}
        def capabilities(self):
            return {"features":["bulk_read","fast_upload"]}
        def info(self):return {"protocol":5,"build_id":b"12345678"}
        def negotiate_download(self):return 500000
        def negotiate_upload(self,features):return 192000
        def read(self,addr,n):return bytes(n)
    link=L();report={"steps":[]}
    with pytest.raises(InstallError,match="Unexpected firmware"):
        validate(link,tmp_path,report,expected_build="bad")
    assert [s["name"] for s in report["steps"]]==["identity"]
    report={"steps":[]}
    validate(link,tmp_path,report,expected_build=b"12345678".hex())
    assert [s["name"] for s in report["steps"]]==["identity","download","upload_probe","calibration","final"]
    assert (tmp_path/"report.json").is_file()


@pytest.mark.parametrize("fault", [None, "midstream", "probe_bad"])
def test_extended_read_stream_negotiates_and_fails_closed(fault, monkeypatch):
    """No newer image: the old 4 KiB protocol must remain fully functional."""
    import k6_live as live
    from k6_live import CAPS, BURST, BURST_LONG, BURST_DATA, BURST_END
    class StreamPort(Port):
        baudrate = 115200
        def reset_input_buffer(self): self.buf = b""
    port = StreamPort()
    pattern = bytes((i*37 + i//256) & 255 for i in range(98304))
    requests = []
    failed = [False]
    def responder(d):
        op,seq,arg = struct.unpack_from("<BBI",d,6)
        assert port.baudrate == 115200
        if op == CAPS:
            return cps_frame(op,seq,b"\x00FLCP\x01"+struct.pack("<I",(1<<7)|(1<<16)))
        if op == STATUS:
            return cps_frame(op,seq,b"\0"+status_data())
        assert op in (BURST, BURST_LONG)
        extended = op == BURST_LONG
        start = (arg & 0x7fff)*64
        count = ((arg>>15)&255) if extended else ((arg>>15)&31)
        rate = live.BAUDS[((arg>>23)&3) if extended else (arg>>20)]
        requests.append((op,start,count,rate))
        ack = cps_frame(op,seq,b"\0"+struct.pack("<I",96000000//round(96000000/rate)))
        frames = [cps_frame(BURST_DATA,seq,struct.pack("<I",start+256*i)+
                   pattern[start+256*i:start+256*(i+1)]) for i in range(count)]
        if extended and not failed[0] and (
            (fault=="probe_bad" and count==32) or
            (fault=="midstream" and count==128)):
            failed[0] = True
            frames[1] = frames[1][:-3] + b"\x00" + frames[1][-2:]
        completion = struct.pack("<BH4I" if extended else "<BB4I",0,count,0,0,0,0)
        return ack+b"".join(frames)+cps_frame(BURST_END,seq,completion)
    port.reply = responder
    monkeypatch.setattr(live.time,"sleep",lambda _:None)
    link = LiveLink(port,timeout=.01)
    assert link.read(0,98304)==pattern
    assert link._download_rate==500000
    assert link._stream_enabled==(fault is None)
    assert any(op==BURST_LONG and count==32 for op,_,count,_ in requests)
    if fault=="midstream":
        assert any(op==BURST_LONG and count==128 for op,_,count,_ in requests)
        assert any(op==BURST and start>=0 for op,start,_,_ in requests[-30:])
    elif fault is None:
        assert sum(op==BURST_LONG and count==128 for op,_,count,_ in requests)==3
    assert port.baudrate==115200
    assert all(data.startswith(live.MAGIC) for data in port.writes)

def test_bench_compare_download_mode_and_data(tmp_path):
    from k6_live_bench import validate
    class FakeLive:
        _stream_enabled=True
        def __init__(self, corrupt=False): self.corrupt=corrupt;self.modes=[]
        def status(self):
            return {"standby_idle":True,"transmitting":False,"sleeping":False,"vfos":[{}],"master":0}
        def capabilities(self):return {"features":["bulk_read","fast_upload","bulk_stream"]}
        def info(self):return {"protocol":5,"build_id":b"12345678"}
        def negotiate_download(self):return 500000
        def negotiate_upload(self,features):return 192000
        def read(self,addr,n):
            if n==262144:
                self.modes.append(self._stream_enabled)
                if self.corrupt and self._stream_enabled:return b"X"*n
            return bytes(n)
    link=FakeLive()
    report={"steps":[]}
    validate(link,tmp_path,report,compare_download=True)
    assert link.modes==[False,True]
    assert link._stream_enabled
    assert "download_comparison" in [x["name"] for x in report["steps"]]
    bad=FakeLive(corrupt=True)
    with pytest.raises(InstallError,match="Extended read differs"):
        validate(bad,tmp_path,{"steps":[]},compare_download=True)
    assert bad._stream_enabled


def test_token_bound_coop_launch_and_ack(monkeypatch):
    import k6_live
    from k6_live_v2 import decode_request, RUN_COOP, RUN_CANCEL
    class CooperativePort(Port):
        def __init__(self, decline=False):
            super().__init__();self.decline=decline
            self.launch=None
        def write(self, data):
            self.writes.append(bytes(data))
            if data.startswith(b"FLXLV1"):
                if data[6] == k6_live.CAPS:
                    self.buf += cps_frame(data[6],data[7],b"\0FLCP\1"+
                                          struct.pack("<I",1<<17))
            elif data.startswith(b"FLXLV2"):
                cmd,seq,arg,token=decode_request(data)
                if cmd==RUN_COOP:
                    assert 0<=arg&31<32
                    assert len(token)==8 and any(token)
                    self.launch=(seq,token)
                    self.buf+=cps_frame(cmd,seq,b"\0")
                elif cmd==RUN_CANCEL:
                    assert self.launch is not None and seq==self.launch[0]
                    assert token==self.launch[1] and arg==0
                    if not self.decline:
                        self.buf+=cps_frame(cmd,seq,b"\0")
                        self.buf+=cps_frame(k6_live.RUN_EVENT,seq,
                            b"\1"+struct.pack("<BiBi",24,186,1,-125))
    p=CooperativePort()
    link=LiveLink(p,initial_seq=0,timeout=.1)
    result=link.run_module_cooperative(24,timeout=.1,cancel_after=0,
                                        allow_higher_api=True)
    assert result["cancel_requested"] and result["cancel_acknowledged"]
    assert result["cancelled"] and result["result"]==-125
    assert [decode_request(w)[0] for w in p.writes if w.startswith(b"FLXLV2")]==[
        RUN_COOP,RUN_CANCEL]
    assert not any(w.startswith(b"PROGRAM") for w in p.writes)

    p=CooperativePort(decline=True)
    with pytest.raises(LiveUnavailable,match="may still be running"):
        LiveLink(p,initial_seq=0,timeout=.02).run_module_cooperative(
            24,timeout=.02,cancel_after=0,allow_higher_api=True)
    assert len([w for w in p.writes if w.startswith(b"FLXLV2")])==2

    # A caller-owned cancellation event is also supported (e.g. the CLI's
    # first Ctrl+C). A timeout is never automatically converted to cancel.
    p=CooperativePort()
    assert LiveLink(p,initial_seq=0,timeout=.1).run_module_cooperative(
        24,timeout=.1,should_cancel=lambda:True,
        allow_higher_api=True)["cancelled"]
    assert len([w for w in p.writes if w.startswith(b"FLXLV2")])==2


def test_coop_is_feature_gated_and_input_validated():
    import k6_live
    p=Port(lambda d:cps_frame(k6_live.CAPS,d[7],b"\0FLCP\1"+struct.pack("<I",0)))
    link=LiveLink(p,initial_seq=0)
    with pytest.raises(InstallError,match="lacks token-bound"):
        link.run_module_cooperative(24)
    with pytest.raises(ValueError,match="delay"):
        link.run_module_cooperative(24,timeout=1,cancel_after=1)
    with pytest.raises(ValueError,match="slot"):
        link.run_module_cooperative(32)
    assert len(p.writes)==1
