"""Local radio development workbench: Flask UI backed by the K6 emulator."""
from __future__ import annotations

import argparse
import atexit
import io
import wave
from collections import deque
import json
import threading
import time
from pathlib import Path
from typing import Protocol
import sys


from sim.k6emu.__main__ import K6Board, overlay_module_bundle
from sim.k6emu.aprs_signal import ui_frame
from sim.k6emu.if_signal import pb1_bits
from sim.k6emu.if_capture import parse_capture
from sim.k6emu.rx_dma import MEASURED_FREE_RUNNING_HZ
from sim.k6emu.if_capture import parse_capture
from sim.k6emu.rx_dma import MEASURED_FREE_RUNNING_HZ

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = ROOT / "firmware/k6-aura/out-fd6818b/flux-fd6818b.bin"
if not DEFAULT_IMAGE.is_file():
    DEFAULT_IMAGE = ROOT / "firmware/k6-aura/out-fd6818b/aura-fd6818b-blowfang-host.bin"
TEMPLATES = ROOT / "sim/k6ide_templates"
sys.path.insert(0, str(ROOT / "tools"))
from k6_theme_package import pack as pack_theme, unpack as unpack_theme
from k6_feature_packs import encode_layer
from k6_user_store_migrate import BASE,END,is_packed,objects,locations,compact
DESIGNER_SLOT = 31  # explicit read-only firmware bench slot
DESIGNER_BASE = 0x110000 + DESIGNER_SLOT * 0x4000
KEYS = ("MENU", "UP", "DOWN", "EXIT", "VM", "AB", "BAND", "SIDE1", "SIDE2",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#")


class K6Workbench:
    """Own the virtual flash and board; all Unicorn access is serialized by one lock."""
    def __init__(self, image: Path, spi_path: Path | None = None, cpi: float = 1.5, *, rf_factory=None):
        self.lock = threading.RLock()
        self.image = image.expanduser().resolve()
        self.spi_path = spi_path.expanduser().resolve() if spi_path else None
        self.cpi = cpi
        self.rf_factory = rf_factory
        self.closed = False
        self.profiles = {}
        self.profile = None
        self.input_queue = deque()
        self.active_key = None
        self.key_started = 0
        self.key_released = False
        self.key_sample_start = 0
        self.release_scan_start = 0
        self.next_key_at = 0
        self.spi_data = bytearray(self.spi_path.read_bytes()) if self.spi_path else bytearray(b"\xff" * (2 * 1024 * 1024))
        if len(self.spi_data) != 2 * 1024 * 1024:
            raise ValueError("virtual SPI image must be exactly 2 MiB")
        self.board = None
        self.running = False
        self.started_at = None
        self.error = None
        self._boot()
        self.worker = threading.Thread(target=self._run, name="k6-ide-emulator", daemon=True)
        self.worker.start()
        atexit.register(self.close)

    def _boot(self):
        self.input_queue.clear()
        self.active_key = None
        self.key_released = False
        self.key_sample_start = 0
        self.release_scan_start = 0
        self.next_key_at = 0
        seed = None
        if self.spi_path:
            self.spi_path.parent.mkdir(parents=True, exist_ok=True)
            self.spi_path.write_bytes(self.spi_data)
            seed = self.spi_path
        if self.board:
            self.board.close_rf()
        self.board = K6Board(self.image, spi_image=seed, cpi=self.cpi, collect_mmio=False)
        if self.rf_factory:
            self.board.rf_backend = self.rf_factory()
        if seed is None:
            self.board.flash_data[:] = self.spi_data
        self.running = True
        self.started_at = time.time()
        self.error = None

    def _run(self):
        while not self.closed:
            with self.lock:
                active = self.running and self.board is not None
                if active:
                    try:
                        # Keep virtual time and peripheral servicing frequent.
                        self._service_keys()
                        self.board.run(12_000, 2048)
                        self._service_keys()
                        if self.board.powered_off:
                            self.running = False
                        elif self.board.fault:
                            self.running = False
                            self.error = f"Firmware fault: {self.board.fault.get('kind')}"
                    except Exception as exc:
                        self.running = False
                        self.error = str(exc)
            time.sleep(.001 if active else .04)

    def close(self):
        with self.lock:
            self.closed = True
            self.running = False
            if self.board:
                self.board.close_rf()

    def rf_audio(self):
        """A bounded snapshot of model discriminator audio, not hardware speaker output."""
        with self.lock:
            backend = self.board.rf_backend
            if backend is None:
                raise ValueError("no RF source configured")
            c = backend.chip
            _, pcm = c.pcm_after(0)
            pcm = pcm[-round(c.pcm_rate * 5):]
            if not len(pcm):
                raise ValueError("no RF audio available yet")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(round(c.pcm_rate))
                wav.writeframes(pcm.astype("<i2").tobytes())
            return buf.getvalue()

    def inject_aprs(self, source="AG7SU", destination="APRS", message=">sim test",
                    ssid=7, noise=0.0, gain=1.0, clip=1.0,
                    emphasis_db=0.0, frequency_offset=0.0, seed=0, dma_mode="direct"):
        """Arm a simulated IF packet; firmware must enable DMA to consume it."""
        if not (0 <= int(ssid) <= 15 and 0 <= float(noise) <= 2
                and 0 < float(gain) <= 20 and 0 < float(clip) <= 1
                and -24 <= float(emphasis_db) <= 24
                and -500 <= float(frequency_offset) <= 500
                and len(message) <= 200):
            raise ValueError("APRS signal parameters outside permitted range")
        if dma_mode not in ("direct", "measured"):
            raise ValueError("invalid DMA mode")
        frame = ui_frame(source, destination, message.encode("ascii"), int(ssid))
        samples = pb1_bits(frame, noise=float(noise), gain=float(gain),
                          clip=float(clip), emphasis_db=float(emphasis_db),
                          frequency_offset=float(frequency_offset), seed=int(seed))
        with self.lock:
            self.board.attach_rx_bits(samples, dma_mode=dma_mode)
            return {"frame_bytes": len(frame), "sample_rate_hz": 85800,
                    "dma_mode": dma_mode,
                    "note": "armed only; reception requires firmware DMA activity"}

    def import_if_capture(self, log: str, sample_rate: int = 85800, dma_mode="direct"):
        if (len(log) > 100_000 or not 1000 <= sample_rate <= 200_000
                or dma_mode not in ("direct", "measured")):
            raise ValueError("capture too large or invalid sample rate")
        samples, metadata = parse_capture(log)
        with self.lock:
            self.board.attach_rx_bits(samples, sample_rate=sample_rate, dma_mode=dma_mode)
        return {**metadata, "sample_rate_hz": sample_rate, "dma_mode": dma_mode,
                "note": "rate supplied by operator, not measured from capture"}

    def aprs_rx_status(self):
        with self.lock:
            dma = self.board.rx_dma if self.board else None
            return {"armed": dma is not None,
                    "dma_mode": getattr(dma, "mode", None),
                    "signal_rate_hz": getattr(dma, "signal_rate_hz", None),
                    "dma_rate_hz": dma.sample_rate if dma else None,
                    "transfers": dma.transfers if dma else 0,
                    "exhausted": dma.exhausted if dma else False,
                    "remaining": dma.remaining if dma else 0}

    def reboot(self):
        with self.lock:
            if self.board:
                self.spi_data[:] = self.board.flash_data
            if not self.board or not self.board.gpio_external[("A", 15)]:
                self._boot()

    def set_running(self, running: bool):
        with self.lock:
            if running and self.board.powered_off:
                raise RuntimeError("turn on the radio power switch first")
            if running and self.board and self.board.fault:
                raise RuntimeError("target faulted; reboot before resuming")
            self.running = bool(running)

    def step(self, instructions: int = 5000):
        with self.lock:
            self.running = False
            if self.board:
                self.board.run(instructions, 256)
                if self.board.fault:
                    self.error = f"Firmware fault: {self.board.fault.get('kind')}"

    # Key transitions are queued in arrival order. Firmware debounces across
    # consecutive scans. Release after two observed samples, and allow two
    # idle scans before the next press. A timed fallback covers other consumers.
    def _service_keys(self):
        now = self.board.cycles
        if self.active_key is not None:
            if (self.active_key, False) in self.input_queue:
                self.input_queue.remove((self.active_key, False))
                self.key_released = True
            if self.key_released and (self.board.key_sample_epoch - self.key_sample_start >= 2 or now - self.key_started >= 9_600_000):
                self.board.press_key(self.active_key, False)
                self.active_key = None
                self.next_key_at = now + 7_680_000
                self.release_scan_start = self.board.key_scan_epoch
            return
        if now < self.next_key_at and self.board.key_scan_epoch - self.release_scan_start < 3:
            return
        while self.input_queue:
            key, down = self.input_queue.popleft()
            if down:
                self.board.press_key(key, True)
                self.active_key = key
                self.key_started = now
                self.key_sample_start = self.board.key_sample_epoch
                self.key_released = False
                return

    def press(self, key: str, down: bool):
        if key not in KEYS:
            raise ValueError("unknown K6 key")
        with self.lock:
            if not self.board or not self.running:
                raise RuntimeError("target is not running")
            if len(self.input_queue) >= 64:
                raise ValueError("key input queue full")
            self.input_queue.append((key, down))
            self._service_keys()

    def select_profile(self, profile):
        with self.lock:
            if profile not in self.profiles:
                raise ValueError("unknown firmware profile")
            if self.profile == profile:
                return
            if self.spi_path and self.board:
                self.spi_path.write_bytes(self.board.flash_data)
            spec = self.profiles[profile]
            self.image = spec['image']
            self.spi_path = spec['spi']
            self.spi_data = bytearray(self.spi_path.read_bytes())
            self.profile = profile
            self._boot()

    def switch(self, name: str, on: bool):
        with self.lock:
            if name == "power":
                if on and self.board.powered_off:
                    self.spi_data[:] = self.board.flash_data
                    self._boot()
                else:
                    self.board.set_gpio("A", 15, 0 if on else 1)
                    if not on and not self.board.powered_off and not self.error:
                        self.running = True
            elif name == "ptt":
                self.board.set_gpio("A", 10, 0 if on else 1)
            else:
                raise ValueError("unknown switch")

    def preview_theme(self, token=None, blob=None):
        """Stage only virtual SPI. Never replace a module or unknown data."""
        if (self.profile and "designer" not in self.profiles[self.profile]["packs"]) or (not self.profile and "designer" not in self.image.name):
            raise ValueError("boot a Designer bench firmware image first")
        if token == "demo":
            layers = [bytearray(8192) for _ in range(3)]
            for y in range(10, 31, 2):
                layers[1][y*128+127] = 1
            for y in range(42, 63, 2):
                layers[2][y*128+127] = 1
            for y in (15, 23, 47, 55):
                layers[0][y*128+1] = 1
            # Five distinct battery states; all retain an outline, with
            # progressive fill. Draw a lightning-like zigzag inside.
            battery = bytearray()
            for bars in range(5):
                for y in range(8):
                    row = 0
                    for x in range(17):
                        on = (x in (1,15) and 1 <= y <= 6) or (y in (1,6) and 1 <= x <= 15)
                        on |= x == 0 and y in (3,4)
                        on |= 3 <= x < 3+bars*3 and 2 <= y <= 5 and (x+y)%3 != 0
                        if on: row |= 1 << (23-x)
                    battery.extend(row.to_bytes(3,"big"))
            token = "custom-11-c0f0fcfefcf0c000-b" + battery.hex() + "-l" + ".".join(map(encode_layer,layers))
        if blob is None and token is not None:
            blob = pack_theme(token)
        if blob is not None:
            blob = pack_theme(unpack_theme(blob))
        with self.lock:
            if self.board.gpio_external[("A",15)]:
                raise ValueError("turn on virtual radio first")
            self.spi_data[:] = self.board.flash_data
            if is_packed(self.spi_data[BASE:END]):
                catalog=objects(self.spi_data[BASE:END]);old=catalog.get(DESIGNER_SLOT)
                if old is not None and old[:3]!=b'K6T':raise ValueError('slot 31 is occupied by non-theme data')
                if blob is None:catalog.pop(DESIGNER_SLOT,None)
                else:catalog[DESIGNER_SLOT]=blob
                self.spi_data[BASE:END]=compact(catalog)[0]
                # Select logical slot 31 in saved settings via normal radio menu.
                self._boot();return {"slot":DESIGNER_SLOT,"bytes":len(blob or b''),"mode":"theme" if blob else "factory"}
            at = DESIGNER_BASE
            old = bytes(self.spi_data[at:at+0x4000])
            if old != b"\xff"*0x4000 and old[:4] not in (b"K6T\x01",b"K6T\x02",b"K6T\x03",b"K6T\x04",b"K6T\x05",b"K6T\x06",b"K6T\x07",b"K6T\x08",b"K6T\x09"):
                raise ValueError("slot 31 is occupied by non-theme data")
            self.spi_data[at:at+0x4000] = b"\xff"*0x4000
            if blob is not None:
                self.spi_data[at:at+len(blob)] = blob
            self._boot()
            return {"slot": DESIGNER_SLOT, "bytes": len(blob) if blob else 0,
                    "mode": "theme" if blob else "factory"}

    def modules(self):
        flash = self.board.flash_data if self.board else self.spi_data
        found = []
        # K6 signed library images carry a 16-byte header and 64-bit authenticator.
        raw=flash[BASE:END];packed=is_packed(raw)
        try:directory=locations(raw) if packed else {}
        except ValueError:return []
        for slot in range(32):
            if packed:
                if slot not in directory:continue
                at=BASE+directory[slot][0]
            else:at=BASE+slot*0x4000
            header = bytes(flash[at:at + 16])
            if header[:4] in (b"K6T\x01",b"K6T\x02",b"K6T\x03",b"K6T\x04",b"K6T\x05",b"K6T\x06",b"K6T\x07",b"K6T\x08",b"K6T\x09"):
                found.append({"slot": slot, "name": "Designer theme", "bytes": 12+int.from_bytes(header[4:6],"little"),
                              "serial": 0, "type": "protected data"})
                continue
            if len(header) < 16 or header[:3] != b"K6M" or header[13] != 1:
                continue
            code_len = int.from_bytes(header[4:6], "little")
            code = bytes(flash[at + 16:at + 16 + min(code_len, 80)])
            name = "Module"
            if code.startswith(b"K6NM"):
                name = code[4:36].split(b"\0")[0].decode("ascii", "replace") or name
            found.append({"slot": slot, "name": name, "bytes": code_len + 24,
                          "serial": int.from_bytes(header[8:12], "little"),
                          "type": "module" if not header[13] & 6 else "protected data"})
        return found

    def load(self, bundle: Path, key_file: Path | None, slots: set[int] | None):
        with self.lock:
            if not self.board:
                raise RuntimeError("board is not booted")
            import sys
            tool_dir = str(ROOT / "tools")
            if tool_dir not in sys.path:
                sys.path.insert(0, tool_dir)
            import k6_install
            key_path = None if self.profile and self.profiles[self.profile].get("cable_only") else Path(k6_install.find_key(str(key_file) if key_file else None,
                                                create=False, log=lambda _: None))
            installed = overlay_module_bundle(self.board.flash_data, bundle, key_path, slots)
            self.spi_data[:] = self.board.flash_data
            if self.spi_path:
                self.spi_path.write_bytes(self.spi_data)
            if not self.board.gpio_external[("A", 15)]:
                self._boot()  # the firmware scans the module table at startup
            return installed

    def unload(self, slots: list[int]):
        if not slots or any(slot < 0 or slot >= 32 for slot in slots):
            raise ValueError("select one or more slots from 0 through 31")
        with self.lock:
            if is_packed(self.board.flash_data[BASE:END]):
                catalog=objects(self.board.flash_data[BASE:END])
                for slot in slots:
                    old=catalog.get(slot,b'')
                    if len(old)<16 or old[:3]!=b'K6M' or old[13]!=1:raise ValueError(f'slot {slot} is empty or protected')
                for slot in slots:del catalog[slot]
                self.spi_data[:]=self.board.flash_data;self.spi_data[BASE:END]=compact(catalog)[0];self._boot();return
            for slot in slots:
                at = 0x110000 + slot * 0x4000
                header = self.board.flash_data[at:at + 16]
                if header[:3] != b"K6M" or not (header[13] & 1) or (header[13] & 6):
                    raise ValueError(f"slot {slot} is empty or protected; only modules can be unloaded")
            for slot in slots:
                at = 0x110000 + slot * 0x4000
                self.board.flash_data[at:at + 0x4000] = b"\xff" * 0x4000
            self.spi_data[:] = self.board.flash_data
            if self.spi_path:
                self.spi_path.write_bytes(self.spi_data)
            if not self.board.gpio_external[("A", 15)]:
                self._boot()

    def state(self):
        with self.lock:
            b = self.board
            return {"model": "Baofeng UV-K6 / FD6818B", "image": str(self.image),
                    "running": self.running, "error": self.error, "fault": b.fault if b else None,
                    "profile": self.profile, "profiles": [{"id": k, "label": v["label"], "packs": v["packs"], "bytes": v["image"].stat().st_size} for k,v in self.profiles.items()],
                    "pending_keys": len(self.input_queue), "active_key": self.active_key,
                    "key_scans": b.key_scan_epoch, "key_samples": b.key_sample_epoch,
                    "powered_off": b.powered_off, "power_switch_on": not b.gpio_external[("A", 15)],
                    "instructions": b.instructions if b else 0,
                    "virtual_ms": round(b.cycles / 96_000, 1) if b else 0,
                    "pc": f"0x{b.last_pc:08x}" if b else "—",
                    "uart": bytes(b.uart_tx[-3000:]).decode("utf-8", "replace") if b else "",
                    "rf_backend": b.rf_backend.status() if b and b.rf_backend else None,
                    "modules": self.modules(), "keys": list(KEYS)}

    def screen_svg(self):
        with self.lock:
            if self.board.powered_off:
                return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 64"><rect width="128" height="64" fill="#dddcc8"/></svg>'
            ram, reverse = self.board.screen.snapshot()
        pixels = []
        for y in range(64):
            for x in range(128):
                col = 4 + (x if reverse else 127 - x)
                if ram[y // 8][col] & (1 << (y & 7)):
                    pixels.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 64" '
                'shape-rendering="crispEdges"><rect width="128" height="64" fill="#dddcc8"/>'
                '<g fill="#182319">' + ''.join(pixels) + '</g></svg>')


class RadioAdapter(Protocol):
    """Backend contract for one emulated radio model; UI routes stay model-neutral."""
    def state(self) -> dict: ...
    def screen_svg(self) -> str: ...
    def rf_audio(self) -> bytes: ...
    def press(self, key: str, down: bool): ...
    def switch(self, name: str, on: bool): ...
    def inject_aprs(self, **kwargs): ...
    def aprs_rx_status(self) -> dict: ...
    def import_if_capture(self, log: str) -> dict: ...

    def reboot(self): ...
    def set_running(self, running: bool): ...
    def step(self, instructions: int = 5000): ...
    def load(self, bundle: Path, key_file: Path | None, slots: set[int] | None): ...
    def unload(self, slots: list[int]): ...


RADIO_MODELS = {"k6-fd6818b": K6Workbench}


def module_bundle_catalog(include_experimental=False):
    """List checked-in and locally built bundles that the workbench can load."""
    manifests = []
    for parent in (ROOT / "local-modules", ROOT / "firmware/k6-aura/out-fd6818b"):
        if parent.is_dir():
            manifests.extend(parent.glob("*/manifest.json"))
    bundles = []
    preferred = {"surf", "cardlink", "explore_scan", "aprs", "aprs-beacon", "aprs-gps-sync", "aprs-kiss-rx"}
    for manifest_path in sorted(manifests):
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("format") != 1:
                continue
            modules = [entry for entry in manifest.get("library", [])
                       if entry.get("kind") == "module"]
            if not modules:
                continue
            # The workbench's default picker is a curated set of current modules.
            # Historical diagnostics and experiments remain accessible explicitly.
            if manifest_path.parent.name in {"vfo", "libls", "vfo-module-bundle"}:
                continue
            if not include_experimental and manifest_path.parent.name not in preferred:
                continue
            contents = ", ".join(
                f"{entry.get('name', 'Module')} ({'slot ' + str(entry['slot']) if entry.get('slot') is not None else 'automatic slot'})"
                for entry in modules)
            bundles.append({
                "path": str(manifest_path.parent.resolve()),
                "label": f"{contents} · {manifest_path.parent.name}",
                "modules": len(modules),
            })
        except (OSError, ValueError, TypeError):
            continue
    return bundles


def create_app(workbench: RadioAdapter, *, sotto_lab=None):
    from flask import Flask, jsonify, render_template, request
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config["workbench"] = workbench
    app.config["sotto_lab"] = sotto_lab
    @app.get("/")
    def index():
        return render_template("index.html", state=workbench.state(), sotto_lab_enabled=sotto_lab is not None)

    @app.get("/api/state")
    def api_state():
        return jsonify(workbench.state())

    @app.get("/api/sotto/state")
    def api_sotto_state():
        if sotto_lab is None:
            return jsonify(enabled=False, error="Sotto lab is not enabled")
        return jsonify(sotto_lab.state())

    @app.post("/api/sotto/run")
    def api_sotto_run():
        if sotto_lab is None:
            return jsonify(ok=False, error="Sotto lab is not enabled"), 404
        try:
            body = request.get_json(force=True)
            if not isinstance(body, dict):
                raise ValueError("Expected an object of bounded test parameters")
            allowed = {"fixture", "cn_db", "offset_hz", "prefix_every",
                       "adc_cycles", "spi_us", "spi_byte_us"}
            if set(body) - allowed:
                raise ValueError("Unknown Sotto lab settings")
            report = sotto_lab.run(**body)
            return jsonify(ok=report["passed"], report=report), 200 if report["passed"] else 422
        except (ValueError, TypeError, RuntimeError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.get("/api/sotto/audio.wav")
    def api_sotto_audio():
        if sotto_lab is None:
            return jsonify(ok=False, error="Sotto lab is not enabled"), 404
        from flask import Response
        try:
            return Response(sotto_lab.wav(), mimetype="audio/wav",
                            headers={"Cache-Control": "no-store"})
        except (ValueError, RuntimeError) as exc:
            return jsonify(ok=False, error=str(exc)), 409

    @app.get("/api/rf/audio.wav")
    def api_rf_audio():
        from flask import Response
        try:
            return Response(workbench.rf_audio(), mimetype="audio/wav",
                            headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/profile")
    def api_profile():
        try:
            workbench.select_profile(str(request.get_json(force=True)["profile"]))
            return jsonify(ok=True)
        except (ValueError, KeyError, OSError) as exc:
            return jsonify(ok=False,error=str(exc)),400

    @app.get("/api/module-bundles")
    def api_module_bundles():
        return jsonify(bundles=module_bundle_catalog(
            include_experimental=request.args.get("all") == "1"))

    @app.get("/api/screen.svg")
    def api_screen():
        from flask import Response
        return Response(workbench.screen_svg(), mimetype="image/svg+xml")

    @app.post("/api/key")
    def api_key():
        body = request.get_json(force=True)
        try:
            workbench.press(str(body["key"]).upper(), bool(body["down"]))
            return jsonify(ok=True)
        except (ValueError, RuntimeError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/switch")
    def api_switch():
        body = request.get_json(force=True)
        try:
            workbench.switch(str(body["name"]), bool(body["on"]))
            return jsonify(ok=True)
        except (ValueError, RuntimeError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/reboot")
    def api_reboot():
        workbench.reboot()
        return jsonify(ok=True)

    @app.post("/api/run")
    def api_run():
        body = request.get_json(force=True)
        try:
            workbench.set_running(bool(body["running"]))
            return jsonify(ok=True)
        except (RuntimeError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/step")
    def api_step():
        workbench.step()
        return jsonify(ok=True)

    @app.post("/api/if-capture/import")
    def api_if_capture_import():
        try:
            body = request.get_json(force=True)
            return jsonify(ok=True, **workbench.import_if_capture(
                str(body["log"]), int(body.get("sample_rate", 85800)),
                str(body.get("dma_mode", "direct"))))
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.get("/api/aprs/rx")
    def api_aprs_rx_status():
        return jsonify(workbench.aprs_rx_status())

    @app.post("/api/aprs/rx")
    def api_aprs_rx():
        try:
            body = request.get_json(force=True)
            return jsonify(ok=True, **workbench.inject_aprs(**body))
        except (ValueError, TypeError, UnicodeError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/aprs/import")
    def api_aprs_import():
        try:
            body = request.get_json(force=True)
            if len(body.get("log", "")) > 100000:
                raise ValueError("capture log too large")
            return jsonify(ok=True, **workbench.import_if_capture(body["log"]))
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/theme/preview")
    def api_theme_preview():
        try:
            body = request.get_json(force=True)
            if body.get("file") is not None:
                raw = body["file"]
                if not isinstance(raw,str) or len(raw)>16384:
                    raise ValueError("theme file exceeds 8 KiB")
                result = workbench.preview_theme(blob=bytes.fromhex(raw))
            else:
                result = workbench.preview_theme(token=body.get("theme"))
            return jsonify(ok=True, **result)
        except (ValueError, TypeError, RuntimeError) as exc:
            return jsonify(ok=False,error=str(exc)),400

    @app.post("/api/modules/load")
    def api_load():
        body = request.get_json(force=True)
        try:
            slots = {int(x) for x in body.get("slots", [])} or None
            installed = workbench.load(Path(body["bundle"]),
                Path(body["key_file"]) if body.get("key_file") else None, slots)
            return jsonify(ok=True, installed=[{"slot": s, "name": n, "bytes": size}
                                               for s, n, size in installed])
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/api/modules/unload")
    def api_unload():
        try:
            body = request.get_json(force=True)
            workbench.unload([int(x) for x in body["slots"]])
            return jsonify(ok=True)
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
    return app


def main():
    parser = argparse.ArgumentParser(description="Flux radio simulator workbench")
    parser.add_argument("--model", choices=RADIO_MODELS, default="k6-fd6818b",
                        help="radio emulator backend (currently K6 FD6818B)")
    parser.add_argument("--profiles", type=Path, help="JSON manifest of allowed firmware profiles and separate virtual SPI images")
    parser.add_argument("--profile", help="initial profile ID")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--spi-image", type=Path, help="persistent 2 MiB virtual SPI flash dump")
    parser.add_argument("--host", default="0.0.0.0",
                        help="HTTP bind address (default: all interfaces for remote browser access)")
    parser.add_argument("--port", type=int, default=5056)
    parser.add_argument("--cpi", type=float, default=1.5)
    parser.add_argument("--sotto-lab-tree", type=Path,
                        help="opt-in offline Sotto ARM/virtual-IQ voice lab; development checkout only")
    from sim.k6emu.rf_options import add_rf_args, rf_options
    add_rf_args(parser)
    args = parser.parse_args()
    profiles = {}
    if args.profiles:
        manifest = args.profiles.resolve()
        for k,v in json.loads(manifest.read_text()).items():
            profiles[k] = {**v, "image": (manifest.parent / v["image"]).resolve(), "spi": (manifest.parent / v["spi"]).resolve()}
            if not profiles[k]["image"].is_file() or profiles[k]["spi"].stat().st_size != 2*1024*1024:
                parser.error(f"invalid profile {k}")
        args.profile = args.profile or next(iter(profiles))
        if args.profile not in profiles: parser.error("unknown initial profile")
        args.image, args.spi_image = profiles[args.profile]["image"], profiles[args.profile]["spi"]
    if not args.image.is_file():
        parser.error(f"firmware image not found: {args.image}")
    rf_factory = None
    if args.rf_source:
        from sim.k6emu.rf.backend import create_backend
        rf_factory = lambda: create_backend(args.rf_source, **rf_options(args))
    try:
        board = RADIO_MODELS[args.model](args.image, args.spi_image, args.cpi, rf_factory=rf_factory)
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        parser.error(str(exc))
    board.profiles = profiles
    board.profile = args.profile
    print(f"Flux radio simulator at http://{args.host}:{args.port} ({'RX-only IQ backend' if args.rf_source else 'virtual RF only'})")
    print(f"On this host: http://127.0.0.1:{args.port}")
    print("On your LAN, use this host's LAN address; Ctrl-C stops the workbench.")
    try:
        sotto_lab = None
        if args.sotto_lab_tree:
            from sim.k6ide_sotto import SottoVoiceLab
            sotto_lab = SottoVoiceLab(args.sotto_lab_tree)
        create_app(board, sotto_lab=sotto_lab).run(host=args.host, port=args.port,
                                                  threaded=True, debug=False)
    finally:
        if sotto_lab is not None:
            sotto_lab.close()
        board.close()


if __name__ == "__main__":
    main()
