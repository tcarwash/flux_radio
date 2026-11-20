"""Single-job, token-bound cooperative module runner for the local Catalog.

The radio executes native code; cancellation is a request that code must poll.
Never call a second serial command to cancel, and never report timeout as a
confirmed stop. A job owns the serial port lock from preflight through cleanup.
"""
from __future__ import annotations

import secrets
import threading
import time
from types import SimpleNamespace

from k6_install import InstallError, open_port
from k6_live import LiveLink, LiveUnavailable
from k6_port_lock import serial_lock


def verify_cooperative_slot(link, slot: int, *, key_file: str = "") -> dict:
    """Read and authenticate the exact module envelope before allowing launch.

    API level 25 does not prove a native module actually polls cancellation;
    users must still trust the installed code. This gate is deliberately
    narrower than the CLI and never supports legacy fixed-slot firmware.
    """
    from k6_install import k6mod
    if type(slot) is not int or not (0 <= slot <= 8 or 16 <= slot <= 31):
        raise ValueError("Select a removable library module slot (0–8 or 16–31).")
    s = link.status()
    if not s["standby_idle"] or s["sleeping"] or s["transmitting"]:
        raise InstallError("The radio must be idle in awake RX standby.")
    features = set(link.capabilities()["features"])
    if "coop_cancel" not in features:
        raise InstallError("Radio firmware does not support cooperative cancellation.")
    info = link.info()
    if info["protocol"] < 5:
        raise InstallError("Cooperative Catalog runs require the packed library.")
    caps = link.module_capabilities()
    if caps["module_api_max"] < 25:
        raise InstallError("Radio module API level 25 is required.")
    key = None
    if not caps["cable_only"]:
        if not key_file:
            raise InstallError("This keyed radio requires its configured module key.")
        key = k6mod.read_key(key_file)
        verified = link.module_capabilities(key)
        if not verified.get("authenticated"):
            raise InstallError("Cannot authenticate this radio's module key.")
    directory = link.library_directory()
    if not directory or slot not in directory:
        raise InstallError("Selected slot is empty or unavailable.")
    _, stored = directory[slot]
    if not (24 <= stored <= 16384):
        raise InstallError("Invalid module envelope length.")
    head = link.library_read(slot, 16)
    if head[:4] != b"K6M" + bytes([k6mod.ABI]) or head[13] != k6mod.LIB_FLAG or head[15] != slot:
        raise InstallError("Selected library entry is not an executable module.")
    size = int.from_bytes(head[4:6], "little")
    entry = int.from_bytes(head[6:8], "little")
    total = 16 + size + 8
    if size == 0 or total != stored or entry % 2 or entry >= size or head[12] != 25:
        raise InstallError("A verified API-25 executable module is required.")
    image = link.library_read(slot, total)
    if image[:16] != head or image[-8:] != k6mod.image_tag(key, image[:-8]):
        raise InstallError("Installed module envelope signature or readback mismatch.")
    return {"slot": slot, "level": 25, "bytes": total,
            "name": k6mod.module_name(image[16:]) or f"Slot {slot}"}


class CoopJobs:
    """Only one live native launch per Catalog server, with a private owner."""

    def __init__(self):
        self._lock = threading.Lock()
        self._job = None

    @staticmethod
    def _public(job):
        public = {k: v for k, v in job.items() if k not in (
            "owner", "cancel", "thread", "key_file")}
        public["reports"] = list(job["reports"])
        return public

    def current(self, owner):
        with self._lock:
            if not self._job or not owner or self._job["owner"] != owner:
                return {"phase": "idle"}
            return self._public(self._job)

    def start(self, *, owner: str, port: str, slot: int, key_file: str = "",
              timeout: float = 30.0):
        if not owner or not port:
            raise ValueError("Select a serial port in Device settings.")
        if type(slot) is not int or not (0 <= slot <= 8 or 16 <= slot <= 31):
            raise ValueError("Invalid module slot.")
        if type(timeout) not in (int, float) or not 1 <= timeout <= 120:
            raise ValueError("Completion timeout must be 1–120 seconds.")
        with self._lock:
            if self._job and (self._job["phase"] in ("preflight", "running", "unknown")
                              or self._job["thread"].is_alive()):
                raise RuntimeError("A live module run is active or unresolved. Recheck the radio before another launch.")
            job = {"id": secrets.token_urlsafe(18), "owner": owner, "port": port,
                   "key_file": key_file, "slot": slot, "timeout": timeout,
                   "phase": "preflight", "message": "Verifying radio and module…",
                   "reports": [], "cancel_requested": False,
                   "cancel_acknowledged": False, "result": None, "error": None,
                   "started": time.time(), "ended": None,
                   "cancel": threading.Event()}
            self._job = job
            worker = threading.Thread(target=self._run, args=(job,), daemon=True,
                                      name="flux-catalog-cooperative-run")
            job["thread"] = worker
            worker.start()
            return self._public(job)

    def snapshot(self, owner, job_id):
        with self._lock:
            if not self._job or self._job["owner"] != owner or self._job["id"] != job_id:
                raise KeyError("Unknown cooperative run.")
            return self._public(self._job)

    def cancel(self, owner, job_id):
        with self._lock:
            if not self._job or self._job["owner"] != owner or self._job["id"] != job_id:
                raise KeyError("Unknown cooperative run.")
            if self._job["phase"] in ("preflight", "running"):
                self._job["cancel"].set()
                self._job["cancel_requested"] = True
                self._job["message"] = "Stop requested; waiting for module-owned cleanup…"
            return self._public(self._job)

    def recheck_unknown(self, owner, port):
        """Require an explicit successful live-status query before another run.

        A responsive single-core radio in idle standby is evidence that its
        event loop resumed; it does not retrospectively prove module cleanup.
        """
        with self._lock:
            job = self._job
            if (not job or job["owner"] != owner or job["port"] != port
                    or job["phase"] != "unknown" or job["thread"].is_alive()):
                raise RuntimeError("No completed uncertain job to recheck.")
        with serial_lock(port), open_port(SimpleNamespace(port=port)) as serial:
            radio = LiveLink(serial, timeout=2)
            status = radio.status()
            if not status["standby_idle"] or status["transmitting"] or status["sleeping"]:
                raise InstallError("Radio is not in verified idle RX standby.")
            if "coop_cancel" not in radio.capabilities()["features"]:
                raise InstallError("Radio live capability verification failed.")
        with self._lock:
            if self._job is not job or job["phase"] != "unknown":
                raise RuntimeError("Another job superseded the uncertain run.")
            job["phase"] = "recovered"
            job["message"] = ("Radio is responsive in idle RX. Previous module return "
                              "was not observed; inspect the radio before proceeding.")
            return self._public(job)

    def _set(self, job, **values):
        with self._lock:
            job.update(values)

    def _report(self, job, line):
        with self._lock:
            job["reports"].append(line[:100])
            del job["reports"][:-80]

    def _run(self, job):
        launched = False
        try:
            with serial_lock(job["port"]), open_port(SimpleNamespace(port=job["port"])) as port:
                link = LiveLink(port, timeout=2)
                metadata = verify_cooperative_slot(link, job["slot"], key_file=job["key_file"])
                if job["cancel"].is_set():
                    self._set(job, phase="finished", message="Cancelled before launch.",
                              result={"launched": False})
                    return
                self._set(job, phase="running", module=metadata,
                          message="Running cooperative module; serial port reserved.")
                launched = True
                result = link.run_module_cooperative(
                    job["slot"], timeout=job["timeout"], allow_higher_api=True,
                    should_cancel=job["cancel"].is_set,
                    on_report=lambda line: self._report(job, line))
                self._set(job, phase="cancelled" if result["cancelled"] else "finished",
                          message=("Module confirmed cancelled and returned."
                                   if result["cancelled"] else "Module returned normally."),
                          cancel_requested=result["cancel_requested"],
                          cancel_acknowledged=result["cancel_acknowledged"],
                          result=result)
        except Exception as exc:
            # A malformed device response or unexpected worker error must
            # never strand a job in "running" or assert confirmed cleanup.
            # Once a launch is sent, even an acknowledgment or timeout failure
            # can leave native code running. The host must not imply otherwise.
            phase = "unknown" if launched else "failed"
            self._set(job, phase=phase, error=str(exc),
                      message=("Execution status unknown; inspect the radio before retrying."
                               if launched else "Preflight failed; module not launched."))
        finally:
            self._set(job, ended=time.time())
