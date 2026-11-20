"""Catalog cooperative runner: launch isolation, token owner and unknown state."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_catalog_coop import CoopJobs, verify_cooperative_slot
from k6_install import InstallError, k6mod
from k6_live import LiveUnavailable
import k6_catalog_coop


class Radio:
    def __init__(self, *, level=25, flags=None, keyed=False, intact=True, blocking=True):
        self.level = level
        self.keyed = keyed
        self.blocking = blocking
        self.started = threading.Event()
        self.exited = threading.Event()
        self.stop_delivery = None
        key = b"0123456789abcdef" if keyed else None
        self.image = bytearray(k6mod.image(b"\x2a\x20\x70\x47", 0, 123, key,
                                           lib=26, level=level))
        if flags is not None:
            self.image[13] = flags
        if not intact:
            self.image[-1] ^= 1

    def status(self):
        return {"standby_idle":True,"sleeping":False,"transmitting":False,
                "vfos":[{"hz":147790000},{"hz":147180000}]}

    def capabilities(self):
        return {"features":["coop_cancel","lib_mutate","module_caps"]}

    def info(self):
        return {"protocol":5}

    def module_capabilities(self, key=None):
        return {"module_api_max":25,"cable_only":not self.keyed,
                "authenticated":bool(key) and self.keyed}

    def library_directory(self):
        return {26:(0x2000,len(self.image))}

    def library_read(self, slot, n):
        assert slot == 26
        return bytes(self.image[:n])

    def run_module_cooperative(self, slot, *, timeout, allow_higher_api,
                               should_cancel, on_report):
        assert slot == 26 and allow_higher_api and 1 <= timeout <= 120
        self.started.set()
        on_report("COOP: started")
        deadline = time.monotonic()+1
        while self.blocking and time.monotonic()<deadline:
            if should_cancel():
                self.stop_delivery = True
                on_report("COOP: cancelled")
                self.exited.set()
                return dict(slot=26,load=4,returned=True,result=-125,
                            reports=["COOP: started","COOP: cancelled"],
                            cancelled=True,cancel_requested=True,cancel_acknowledged=True)
            time.sleep(.005)
        self.exited.set()
        if self.blocking:
            raise LiveUnavailable("Completion not received; may still be running")
        return dict(slot=26,load=4,returned=True,result=42,
                    reports=["COOP: started"],cancelled=False,
                    cancel_requested=False,cancel_acknowledged=False)


def test_coop_slot_preflight_checks_crc_level_and_type(monkeypatch):
    assert verify_cooperative_slot(Radio(),26)["level"]==25
    for radio in (Radio(level=24),Radio(flags=3),Radio(intact=False)):
        with pytest.raises(InstallError):
            verify_cooperative_slot(radio,26)
    with pytest.raises(ValueError):
        verify_cooperative_slot(Radio(),10)
    with pytest.raises(ValueError):
        verify_cooperative_slot(Radio(),True)
    busy=Radio()
    busy.status=lambda:dict(standby_idle=False,sleeping=False,transmitting=True)
    with pytest.raises(InstallError,match="idle"):
        verify_cooperative_slot(busy,26)
    keyed=Radio(keyed=True)
    with pytest.raises(InstallError,match="requires its configured module key"):
        verify_cooperative_slot(keyed,26)
    monkeypatch.setattr(k6mod,"read_key",lambda _:b"0123456789abcdef")
    assert verify_cooperative_slot(keyed,26,key_file="/fake/key")["bytes"]==28


def patch_radio(monkeypatch, radio):
    held = threading.Event()
    exited = threading.Event()
    @contextmanager
    def lock(port):
        assert port == "mock-port"
        assert not held.is_set()
        held.set()
        try:
            yield
        finally:
            held.clear()
            exited.set()
    monkeypatch.setattr(k6_catalog_coop,"serial_lock",lock)
    monkeypatch.setattr(k6_catalog_coop,"open_port",lambda _:nullcontext(object()))
    monkeypatch.setattr(k6_catalog_coop,"LiveLink",lambda _port,timeout=2:radio)
    return held,exited


def wait_finished(jobs, owner, job_id, timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        snapshot=jobs.snapshot(owner,job_id)
        if snapshot["phase"] not in ("preflight","running"):return snapshot
        time.sleep(.01)
    pytest.fail("runner did not finish")


def test_coop_runner_owns_lock_and_confirms_cancel(monkeypatch):
    radio=Radio()
    held,unlocked=patch_radio(monkeypatch,radio)
    jobs=CoopJobs()
    first=jobs.start(owner="alice",port="mock-port",slot=26,timeout=5)
    assert radio.started.wait(2)
    assert held.is_set()
    with pytest.raises(RuntimeError,match="active or unresolved"):
        jobs.start(owner="alice",port="mock-port",slot=26)
    with pytest.raises(KeyError):
        jobs.cancel("bob",first["id"])
    assert jobs.cancel("alice",first["id"])["cancel_requested"]
    done=wait_finished(jobs,"alice",first["id"])
    assert done["phase"]=="cancelled" and done["result"]["cancelled"]
    assert done["cancel_acknowledged"]
    assert done["reports"]==["COOP: started","COOP: cancelled"]
    assert unlocked.wait(2)
    assert not held.is_set()
    assert jobs.current("bob")=={"phase":"idle"}
    assert jobs.current("alice")["id"]==first["id"]
    assert "key_file" not in done and "cancel" not in done


def test_coop_lost_completion_stays_unknown_until_idle_recheck(monkeypatch):
    radio=Radio()
    held,unlocked=patch_radio(monkeypatch,radio)
    jobs=CoopJobs()
    first=jobs.start(owner="alice",port="mock-port",slot=26,timeout=1)
    assert radio.started.wait(2)
    done=wait_finished(jobs,"alice",first["id"])
    assert done["phase"]=="unknown"
    assert "may still be running" in done["error"]
    assert unlocked.wait(2)
    with pytest.raises(RuntimeError,match="active or unresolved"):
        jobs.start(owner="alice",port="mock-port",slot=26)
    with pytest.raises(RuntimeError,match="No completed uncertain"):
        jobs.recheck_unknown("bob","mock-port")
    recovered=jobs.recheck_unknown("alice","mock-port")
    assert recovered["phase"]=="recovered"
    assert "not observed" in recovered["message"]
    radio.blocking=False
    second=jobs.start(owner="alice",port="mock-port",slot=26)
    complete=wait_finished(jobs,"alice",second["id"])
    assert complete["phase"]=="finished"
    assert complete["result"]["result"]==42


def test_coop_failed_preflight_does_not_claim_execution(monkeypatch):
    radio=Radio(level=24)
    held,unlocked=patch_radio(monkeypatch,radio)
    jobs=CoopJobs()
    first=jobs.start(owner="alice",port="mock-port",slot=26)
    done=wait_finished(jobs,"alice",first["id"])
    assert done["phase"]=="failed"
    assert "API-25" in done["error"]
    assert unlocked.wait(2)
    assert not radio.started.is_set()


def test_catalog_coop_routes_require_csrf_trust_and_owner(monkeypatch):
    import k6_store
    monkeypatch.setenv("FLUX_CATALOG_COOP_ENABLED","1")
    radio=Radio()
    patch_radio(monkeypatch,radio)
    app=k6_store.create_app()
    alice=app.test_client()
    bob=app.test_client()
    assert alice.post("/radio/coop/start",json={"slot":26}).status_code==403
    with alice.session_transaction() as session:
        session["radio_port"]="mock-port"
    alice.get("/radio")
    with alice.session_transaction() as session:
        token=session["live_radio_token"]
    assert alice.post("/radio/coop/start",json={"slot":26,"token":token}).status_code==400
    assert alice.post("/radio/coop/start",json={"slot":True,"confirm":"run",
                "higher_api_ack":True,"token":token}).status_code==400
    assert alice.post("/radio/coop/start",json={"slot":26,"confirm":"run",
                "higher_api_ack":"yes","token":token}).status_code==400
    started=alice.post("/radio/coop/start",json={"slot":26,"timeout":5,
               "confirm":"run","higher_api_ack":True,"token":token})
    assert started.status_code==202
    job=started.json["id"]
    assert radio.started.wait(2)
    assert bob.post("/radio/coop/cancel",json={"id":job,"token":token}).status_code==403
    assert alice.post("/radio/coop/start",json={"slot":26,"timeout":5,
               "confirm":"run","higher_api_ack":True,"token":token}).status_code==409
    assert alice.post("/radio/coop/cancel",json={"id":"wrong","token":token}).status_code==404
    assert alice.post("/radio/coop/recover",json={"token":token}).status_code==400
    assert alice.post("/radio/coop/cancel",json={"id":job,"token":token}).status_code==200
    done=wait_finished(app.config["cooperative_jobs"],token,job)
    assert done["phase"]=="cancelled"
    assert alice.post("/radio/coop/status",json={"id":job,"token":token}).json["cancel_acknowledged"]
    assert alice.post("/radio/coop/current",json={"token":token}).json["id"]==job
    assert alice.post("/radio/coop/ptt",json={"token":token}).status_code==404
    assert alice.get("/radio/coop/current").status_code==405
    assert b"Run selected module" in alice.get("/radio").data
    assert b"radio-coop.js" in alice.get("/radio").data


def test_catalog_coop_disabled_by_default_and_loopback_only(monkeypatch):
    import k6_store
    monkeypatch.delenv("FLUX_CATALOG_COOP_ENABLED",raising=False)
    monkeypatch.delenv("FLUX_CATALOG_COOP_REMOTE",raising=False)
    app=k6_store.create_app()
    client=app.test_client()
    with client.session_transaction() as sess:
        sess["radio_port"]="mock-port"
    page=client.get("/radio")
    assert b"Native module controls are disabled by default" in page.data
    assert b'id="coop-start"' not in page.data
    with client.session_transaction() as sess:
        token=sess["live_radio_token"]
    assert client.post("/radio/coop/current",json={"token":token}).status_code==403
    monkeypatch.setenv("FLUX_CATALOG_COOP_ENABLED","1")
    assert client.post("/radio/coop/current",json={"token":token},
                       environ_overrides={"REMOTE_ADDR":"198.51.100.7"}).status_code==403
    assert client.post("/radio/coop/current",json={"token":token},
                       environ_overrides={"REMOTE_ADDR":"127.0.0.1"},
                       headers={"Host":"radio.example.com"}).status_code==403
    assert client.post("/radio/coop/current",json={"token":token}).json=={"phase":"idle"}
    # Isolate IPv6 host parsing from Flask's per-domain cookie jar.
    from flask import session
    with app.test_request_context("/radio/coop/current", method="POST",
        base_url="http://[::1]:5000", json={"token":"ipv6-test"},
        environ_overrides={"REMOTE_ADDR":"::1"}):
        session["live_radio_token"]="ipv6-test"
        assert app.view_functions["radio_cooperative"]("current").get_json()=={"phase":"idle"}
    assert b'id="coop-start"' in client.get("/radio").data
