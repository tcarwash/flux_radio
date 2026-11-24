"""Opt-in live-control regression against the compiled firmware (not RF evidence)."""
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink


def test_compiled_live_control(simulated_radio, monkeypatch):
    image = os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image")
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, "time", SimpleNamespace(monotonic=port.monotonic))
    link = LiveLink(port, timeout=3.0)
    before = bytes(port.board.flash_data)
    first = link.status()
    assert first["version"] == 1
    assert link.read(0xb000, 64)[:4] == b"AURA"
    assert link.read(0, 128) == before[:128]
    assert link.status()["vfos"] == first["vfos"]
    target = 146_520_000 if first["vfos"][first["master"]]["hz"] != 146_520_000 else 146_550_000
    tuned = link.tune(target)
    assert tuned["vfos"][tuned["master"]]["hz"] == target
    assert not tuned["transmitting"]
    assert bytes(port.board.flash_data) == before
    port.sleep(6)
    assert link.status()["vfos"][tuned["master"]]["hz"] == target
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)


def test_compiled_live_module_run(simulated_radio, monkeypatch):
    image = os.environ.get('K6_SIM_LIVE_IMAGE')
    if not image:
        pytest.skip('set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image')
    from modem import k6mod
    from k6_install import LIB_BASE, LIB_SLOT, InstallError
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, 'time', SimpleNamespace(monotonic=port.monotonic))
    code, entry = k6mod.build(str(Path(__file__).resolve().parents[1] / 'c/k6/mod/hello.c'))
    entries = {}
    for slot, level, page, corrupt in [(24, 1, False, False), (25, 3, False, False), (26, 1, True, False), (27, 1, False, True)]:
        envelope = k6mod.image(code, entry, 100 + slot, None, level=level, lib=slot, page=page)
        if corrupt:
            envelope = envelope[:-1] + bytes([envelope[-1] ^ 1])
        entries[slot] = envelope
    if os.environ.get('K6_SIM_LIVE_PACKED') == '1':
        from k6_user_store_migrate import compact, BASE, END
        port.board.flash_data[BASE:END] = compact(entries)[0]
    else:
        for slot, envelope in entries.items():
            offset = LIB_BASE + slot * LIB_SLOT
            port.board.flash_data[offset:offset+len(envelope)] = envelope
    before = bytes(port.board.flash_data)
    link = LiveLink(port, timeout=3.0, initial_seq=0)
    status = link.status()
    assert status['standby_idle'] and not status['transmitting']
    for slot in (23, 25, 26):
        with pytest.raises(InstallError, match='Unsupported'):
            link.run_module(slot)
    result = link.run_module(24, timeout=5)
    assert result['returned'] and result['result'] == 0x6b, result
    assert result['load'] == len(code)
    assert len(result['reports']) == 2 and result['reports'][0].startswith('hello world from RAM')
    assert 'sum of squares' in result['reports'][1]
    assert link.run_module(25, timeout=5, allow_higher_api=True)['result'] == 0x6b
    bad = link.run_module(27, timeout=5)
    assert not bad['returned'] and bad['load'] < 0
    after = link.status()
    assert after['standby_idle'] and after['vfos'] == status['vfos'] and not after['transmitting']
    assert bytes(port.board.flash_data) == before
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)


def test_compiled_cat_controls(simulated_radio, monkeypatch):
    image = os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image")
    from k6_live import MODE, VFO
    from k6_install import InstallError
    port = simulated_radio(Path(image).read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_live, "time", SimpleNamespace(monotonic=port.monotonic))
    link = LiveLink(port, timeout=3.0)
    before = bytes(port.board.flash_data)
    first = link.status()
    features = link.capabilities()["features"]
    assert {"mode", "vfo", "run"} <= set(features)
    initial = first["master"]
    for m in ("AM","USB","CW","FM"):
        result=link.set_mode(m, m == "FM")
        assert result["vfos"][initial]["mode"] == m
        assert result["vfos"][initial]["wide"] == (m == "FM")
        assert not result["transmitting"]
    changed = link.select_vfo(1-initial)
    assert changed["master"] == 1-initial
    assert changed["watching"] == 1-initial
    assert not changed["transmitting"]
    assert link.select_vfo(initial)["master"] == initial
    for op,arg in [(MODE,5),(MODE,0x200),(VFO,2)]:
        with pytest.raises(InstallError, match="Unsupported"):
            link.request(op,arg)
    assert bytes(port.board.flash_data) == before
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)


def test_compiled_live_bulk_reads(simulated_radio, monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image")
    from k6_live import BURST, BURST_LONG
    from k6_install import InstallError
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    # Simulator models data and register writes, not electrical baud fidelity.
    port.baudrate=115200
    link=LiveLink(port,timeout=3)
    if "bulk_read" not in link.capabilities()["features"]:
        pytest.skip("requires K6_SERIAL_TRANSPORT=1")
    # Invalid long count/rate/bounds must not change baud or read outside flash.
    for arg in (0, (129<<15)|(2<<23), (1<<15)|(1<<23),
                (1<<15)|(2<<23)|(1<<25), 0x7fff|(128<<15)|(2<<23)):
        with pytest.raises(InstallError,match="Unsupported"):
            link.request(BURST_LONG,arg)
    assert link.status()["standby_idle"]
    assert link.info()["protocol"] in (2,4,5)
    addr=0x1f8000
    pattern=bytes((i*73+i//256)&255 for i in range(32768))
    port.board.flash_data[addr:addr+len(pattern)]=pattern
    before=bytes(port.board.flash_data)
    assert link.read(addr,32768)==pattern
    assert link._stream_enabled and "bulk_stream" in link.capabilities()["features"]
    assert any(req[6]==BURST_LONG for req in port.requests if req.startswith(k6_live.MAGIC))
    # The long protocol can send the entire 32KiB in one bounded window.
    assert link._burst(addr,128,500000,stream=True)==pattern
    assert link._download_rate==500000
    assert port.baudrate==115200
    assert link.read(0x1fffc0,64)==pattern[-64:]
    for arg in (0, 17<<15, 5<<15, 3<<20|1<<15, 0x7fff|1<<15):
        with pytest.raises(InstallError,match="Unsupported"):
            link.request(BURST,arg)
    # Invalid long count/rate/bounds must not change baud or read outside flash.
    for arg in (0, (129<<15)|(2<<23), (1<<15)|(1<<23),
                (1<<15)|(2<<23)|(1<<25), 0x7fff|(128<<15)|(2<<23)):
        with pytest.raises(InstallError,match="Unsupported"):
            link.request(BURST_LONG,arg)
    assert link.status()["standby_idle"]
    assert bytes(port.board.flash_data)==before
    assert all(x.startswith(k6_live.MAGIC) for x in port.requests)


def test_compiled_live_v2(simulated_radio, monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE to a K6_LIVE_SERIAL=1 image")
    from k6_live_v2 import request_bytes_v2, ECHO
    from k6_install import InstallError
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    link=LiveLink(port,timeout=3,initial_seq=0)
    if "v2" not in link.capabilities()["features"]:
        pytest.skip("requires live v2")
    before=bytes(port.board.flash_data)
    for data in [b"",b"PROGRAMBF-K6BLWF PROGRAMBF-K6AURA FLXLV1",bytes(range(128)),bytes(range(128,256))]:
        assert link.request_v2(ECHO,payload=data)==data
    with pytest.raises(InstallError,match="Unsupported"):
        link.request_v2(ECHO,1,b"abc")
    # Bad CRC produces no frame, and a subsequent ordinary request is safe.
    wire=bytearray(request_bytes_v2(ECHO,220,payload=b"abc"))
    wire[-3]^=1
    port.write(wire)
    port.sleep(.2)
    assert link.status()["standby_idle"]
    wire=request_bytes_v2(ECHO,221,payload=b"PROGRAMBF-K6BLWF")
    port.write(wire[:12])
    port.sleep(.2)
    port.write(wire[12:])  # orphaned high-bit body cannot enter a loader
    assert link.status()["standby_idle"]
    assert bytes(port.board.flash_data)==before
    assert all(x.startswith((k6_live.MAGIC,b"FLXLV2")) or x==wire[12:] for x in port.requests)


def test_compiled_live_ram_module(simulated_radio, monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:
        pytest.skip("set K6_SIM_LIVE_IMAGE")
    from modem import k6mod
    from k6_install import InstallError
    from k6_live_v2 import RAM_BEGIN,RAM_CHUNK,RAM_COMMIT
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    link=LiveLink(port,timeout=3,initial_seq=0)
    if "ram_run" not in link.capabilities()["features"]:
        pytest.skip("requires expanded stack live RAM")
    before=bytes(port.board.flash_data)
    status=link.status()
    code,entry=k6mod.build(str(Path(__file__).resolve().parents[1]/"c/k6/mod/hello.c"))
    floor=link.serial_watermark()
    has_cache="ram_status" in link.capabilities()["features"]
    if has_cache:
        assert link.ram_status(floor+1)["state"]=="unknown"
    env=k6mod.image(code,entry,floor+1,None,level=1)
    bad=env[:-1]+bytes([env[-1]^1])
    with pytest.raises(InstallError,match="verification refused"):
        link.run_ram(bad)
    assert link.serial_watermark()==floor
    assert link.status()["standby_idle"]
    # A partial staging job expires without arming or consuming a serial.
    assert link.request_v2(RAM_BEGIN,len(env))==b""
    assert link.request_v2(RAM_CHUNK,0,env[:20])==b""
    port.sleep(.8)
    assert link.status()["standby_idle"]
    with pytest.raises(InstallError,match="Unsupported"):
        link.request_v2(RAM_COMMIT,len(env))
    assert link.serial_watermark()==floor
    result=link.run_ram(env,timeout=5)
    assert result["returned"] and result["ram"] and result["slot"] is None, result
    assert result["result"]==0x6b and result["load"]==len(code),result
    assert len(result["reports"])==2 and result["reports"][0].startswith("hello world from RAM"),result
    assert link.serial_watermark()==floor+1
    assert result["job_id"]==floor+1
    if has_cache:
        # New host sequence simulates reconnect; no execution request needed.
        reopened=LiveLink(port,timeout=3,initial_seq=200)
        assert reopened.ram_status(floor+1)==dict(job_id=floor+1,
            state="completed",load=len(code),returned=True,result=0x6b)
        assert reopened.ram_status(floor+2)["state"]=="unknown"
    with pytest.raises(InstallError,match="watermark"):
        link.run_ram(env)
    # Bypass the host-side replay check; target independently rejects replay.
    assert link.request_v2(RAM_BEGIN,len(env))==b""
    for i in range(0,len(env),128):
        assert link.request_v2(RAM_CHUNK,i,env[i:i+128])==b""
    assert int.from_bytes(link.request_v2(RAM_COMMIT,len(env)),"little",signed=True)==-6
    if has_cache:
        assert link.ram_status(floor+1)["result"]==0x6b
        # A newer accepted job replaces the one-entry cache.
        newer=k6mod.image(code,entry,floor+2,None,level=1)
        assert link.run_ram(newer,timeout=5)["job_id"]==floor+2
        assert link.ram_status(floor+1)["state"]=="unknown"
        assert link.ram_status(floor+2)["result"]==0x6b
    after=link.status()
    assert after["standby_idle"] and after["vfos"]==status["vfos"] and not after["transmitting"]
    assert bytes(port.board.flash_data)==before


def test_compiled_live_fast_upload(simulated_radio,monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:pytest.skip("set K6_SIM_LIVE_IMAGE")
    from modem import k6mod
    from k6_install import InstallError
    from k6_live_v2 import (RAM_FAST_BEGIN,RAM_CHUNK,RAM_READY,RAM_COMMIT,
                           request_bytes_v2,MAGIC_V2)
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    port.baudrate=115200
    link=LiveLink(port,timeout=3,initial_seq=248)
    if "fast_upload" not in link.capabilities()["features"]:pytest.skip("requires fast_upload")
    before=bytes(port.board.flash_data)
    floor=link.serial_watermark()
    assert link.negotiate_upload()==192000
    assert port.baudrate==115200
    assert port.board.regs[0x4001380c]==round(96000000/115200)
    assert link.serial_watermark()==floor
    code,entry=k6mod.build(str(Path(__file__).resolve().parents[1]/"c/k6/mod/hello.c"))
    env=k6mod.image(code,entry,floor+1,None,level=1)

    # Stage directly, but attempt COMMIT before READY. Must not execute.
    link._fast_stage(RAM_FAST_BEGIN,env)
    assert port.board.regs[0x4001380c]==round(96000000/115200)
    with pytest.raises(InstallError,match="Unsupported"):
        link.request_v2(RAM_COMMIT,len(env))
    assert link.serial_watermark()==floor
    assert link.ram_status(floor+1)["state"]=="unknown"

    # Corrupt a high-speed body. It must expire without arming/executing,
    # drain back to normal and accept a later read-only status.
    assert link.request_v2(RAM_FAST_BEGIN,len(env))==b"\x00\xee\x02\x00"
    port.baudrate=192000
    port.sleep(.05)
    assert port.board.regs[0x4001380c]==500
    bad=bytearray(request_bytes_v2(RAM_CHUNK,(link.seq+1)%256,0,env[:128])[len(MAGIC_V2):])
    bad[-3]^=1
    port.write(bad)
    port.sleep(.25)
    port.baudrate=115200
    assert port.board.regs[0x4001380c]==round(96000000/115200)
    assert link.status()["standby_idle"]
    assert link.serial_watermark()==floor

    result=link.run_ram(env,timeout=5)
    assert result["result"]==0x6b and result["job_id"]==floor+1
    assert link.ram_status(floor+1)["result"]==0x6b
    assert port.baudrate==115200
    assert port.board.regs[0x4001380c]==round(96000000/115200)
    assert not link.status()["transmitting"]
    assert bytes(port.board.flash_data)==before


def test_compiled_live_library_mutation(simulated_radio,monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:pytest.skip("set K6_SIM_LIVE_IMAGE")
    from modem import k6mod
    from k6_install import InstallError
    from k6_live_v2 import LIB_BEGIN,LIB_FAST_BEGIN,LIB_COMMIT,RAM_COMMIT
    from k6_user_store_migrate import compact,objects,BASE,END
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    port.baudrate=115200
    link=LiveLink(port,timeout=3,initial_seq=0)
    features=link.capabilities()["features"]
    if "lib_mutate" not in features:pytest.skip("requires live packed mutation")
    code,entry=k6mod.build(str(Path(__file__).resolve().parents[1]/"c/k6/mod/hello.c"))
    floor=link.serial_watermark()
    def env(serial,slot=24,**kw):return k6mod.image(code,entry,serial,None,level=1,lib=slot,**kw)
    other=env(floor+1,25,page=True)
    port.board.flash_data[BASE:END]=compact({25:other})[0]
    outside=bytes(port.board.flash_data[:BASE])+bytes(port.board.flash_data[END:])
    state=link.status()
    first=env(floor+2)
    # Wrong commit kind cannot launch a staged library image.
    link._stage_image(first,[],True,LIB_BEGIN,LIB_FAST_BEGIN)
    with pytest.raises(InstallError,match="Unsupported"):link.request_v2(RAM_COMMIT,len(first))
    assert link.serial_watermark()==floor
    assert objects(port.board.flash_data[BASE:END])=={25:other}
    # Corrupt signature and protected slot rejected on device, not just host.
    bad=first[:-1]+bytes([first[-1]^1])
    for invalid in (bad,env(floor+2,25),env(floor+2,9)):
        link._stage_image(invalid,[],True,LIB_BEGIN,LIB_FAST_BEGIN)
        assert int.from_bytes(link.request_v2(LIB_COMMIT,len(invalid)),"little",signed=True)<0
        assert objects(port.board.flash_data[BASE:END])=={25:other}
    # Rejected protected-slot envelope may consume its verified replay serial.
    floor=link.serial_watermark()
    first=env(floor+1)
    assert link.mutate_library(first,baseline=True)["verified"]
    assert objects(port.board.flash_data[BASE:END])=={24:first,25:other}
    assert link.run_module(24,timeout=5)["result"]==0x6b
    # Exercise a full new staging allocation and multiple NOR sectors.
    limit=10240 if "lib_10k" in features else 7168
    assert link.library_limit()==limit
    caps=link.module_capabilities()
    assert caps["cable_only"] and caps["module_loader"]
    assert caps["build_id"]==link.info()["build_id"].hex()
    second=k6mod.image(code+bytes(limit-24-len(code)),entry,floor+2,None,level=1,lib=24)
    assert len(second)==limit
    assert link.mutate_library(second)["verified"]
    assert objects(port.board.flash_data[BASE:END])=={24:second,25:other}
    delete=k6mod.image(b"K6ERASE",0,floor+3,None,level=1,lib=24,remove=True)
    assert link.mutate_library(delete)["removed"]
    assert objects(port.board.flash_data[BASE:END])=={25:other}
    assert bytes(port.board.flash_data[:BASE])+bytes(port.board.flash_data[END:])==outside
    assert link.status()["vfos"]==state["vfos"]
    assert not link.status()["transmitting"]


def test_compiled_live_coop_cancel_token(simulated_radio,monkeypatch):
    image=os.environ.get("K6_SIM_LIVE_IMAGE")
    if not image:pytest.skip("set K6_SIM_LIVE_IMAGE")
    from modem import k6mod
    from k6_live_v2 import RUN_COOP, RUN_CANCEL, decode_request, request_bytes_v2
    from k6_user_store_migrate import compact,BASE,END,objects
    from k6_install import InstallError
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(
        monotonic=port.monotonic,sleep=port.sleep))
    port.baudrate=115200
    link=LiveLink(port,timeout=3.0,initial_seq=0)
    features=set(link.capabilities()["features"])
    if "coop_cancel" not in features:
        pytest.skip("requires cooperative cancellation feature")
    # This test writes a *packed* module store and must not run against the
    # ordinary non-packed host image, whose same slot is deliberately invalid.
    if "lib_large" not in features:
        pytest.skip("requires packed user store (K6_USER_STORE=1)")
    assert link.module_capabilities()["module_api_max"]==25
    code,entry=k6mod.build(str(Path(__file__).resolve().parents[1]/
                                  "c/k6/mod/live_cancel_demo.c"))
    env=k6mod.image(code,entry,100,None,level=25,lib=26)
    port.board.flash_data[BASE:END]=compact({26:env})[0]
    before=bytes(port.board.flash_data)
    initial=link.status()
    assert initial["standby_idle"] and not initial["transmitting"]
    wrong=[False]
    def send_invalid(line):
        if "COOP: started" in line:
            start=next(decode_request(w) for w in port.requests
                       if w.startswith(b"FLXLV2") and decode_request(w)[0]==RUN_COOP)
            seq=start[1];token=start[3]
            port.write(request_bytes_v2(RUN_CANCEL,seq,0,
                       bytes([token[0]^1])+token[1:]))
            wrong[0]=True
    result=link.run_module_cooperative(26,timeout=6,cancel_after=.15,
                                      allow_higher_api=True,on_report=send_invalid)
    assert wrong[0] and result["cancelled"] and result["result"]==-125,result
    assert result["cancel_acknowledged"] and result["cancel_requested"]
    assert "COOP: started" in result["reports"] and "COOP: cancelled" in result["reports"]
    assert link.status()["vfos"]==initial["vfos"]
    assert not link.status()["transmitting"]
    assert bytes(port.board.flash_data)==before
    assert objects(port.board.flash_data[BASE:END])=={26:env}
