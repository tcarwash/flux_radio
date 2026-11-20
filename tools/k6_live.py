#!/usr/bin/env python3
"""Non-rebooting Flux live control. Requires a K6_LIVE_SERIAL=1 host image."""
import argparse
import json
import secrets
import struct
import sys
import time
from types import SimpleNamespace

from k6_install import CpsLink, Cable, InstallError, crc16_xmodem, open_port
from k6_port_lock import serial_lock

MAGIC = b"FLXLV1"
STATUS, READ, TUNE = 0x60, 0x61, 0x62
RUN, RUN_EVENT = 0x66, 0x67
CAPS, MODE, VFO = 0x68, 0x69, 0x6a
BURST, BURST_DATA, BURST_END, INFO = 0x6b, 0x6c, 0x6d, 0x6e
WATERMARK = 0x6f
RAM_STATUS = 0x75
BAUDS = (115200, 192000, 500000)
CHIP_BYTES = 0x200000
FEATURES = ("status", "read", "tune", "run", "mode", "vfo", "health", "bulk_read", "info", "v2", "ram_run", "ram_status", "fast_upload")
MODES = ("FM", "AM", "USB", "CW", "CWF")
ERRORS = {1: "Radio busy; return to standby, release PTT or disable the conflicting mode.",
          2: "Unsupported command, address or frequency.", 3: "SPI read failed."}


class LiveBulkError(InstallError):
    """Incomplete or corrupt download; radio returns to baseline by itself."""


class LiveUploadError(InstallError):
    """Failed data-only excursion; baseline recovery was verified."""


class LiveUnavailable(InstallError):
    """No live protocol response. Never implicitly open the rebooting loader."""


def request_bytes(cmd, seq, arg=0):
    body = MAGIC + struct.pack("<BBI", cmd, seq, arg)
    return body + struct.pack(">H", crc16_xmodem(body))


def decode_status(d):
    if len(d) != 26 or d[:5] != b"FLIV\x01":
        raise InstallError("Unsupported live status format")
    master, watching, flags, a, b, cv, rssi, ma, mb, wa, wb = struct.unpack("<BBBIIHiBBBB", d[5:])
    if master > 1 or watching > 1 or ma >= len(MODES) or mb >= len(MODES):
        raise InstallError("Invalid live status response")
    return dict(version=1, master=master, watching=watching, transmitting=bool(flags & 1),
                squelch_open=bool(flags & 2), standby_idle=bool(flags & 4),
                sleeping=bool(flags & 8), battery_volts=cv / 100, rssi_dbm=rssi,
                vfos=[dict(hz=hz, mode=MODES[mod], wide=bool(wide), memory=bool(flags & bit))
                      for hz, mod, wide, bit in ((a, ma, wa, 16), (b, mb, wb, 32))])


class LiveLink(CpsLink):
    def __init__(self, port, timeout=1.0, *, initial_seq=None):
        super().__init__(port, log=lambda *_: None, timeout=timeout)
        self.seq = secrets.randbelow(256) if initial_seq is None else initial_seq
        self._bulk_cap = None
        self._download_rate = None
        self._upload_rate = None

    def request(self, cmd, arg=0):
        return self._exchange(cmd, lambda seq: request_bytes(cmd,seq,arg))

    def request_v2(self, cmd, arg=0, payload=b""):
        from k6_live_v2 import request_bytes_v2
        return self._exchange(cmd, lambda seq: request_bytes_v2(cmd,seq,arg,payload))

    def _exchange(self, cmd, encode):
        self.seq = (self.seq + 1) % 256
        self.port.write(encode(self.seq))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            frame = self._frame(deadline)
            if frame is None:
                break
            c, seq, d = frame
            if (c, seq) != (cmd, self.seq):
                continue
            if not d:
                raise InstallError("Empty live reply")
            if d[0]:
                raise InstallError(ERRORS.get(d[0], "Unknown live radio error"))
            return d[1:]
        raise LiveUnavailable("No live response. Use a live-enabled Flux image in standby; no reset was requested.")

    def status(self):
        return decode_status(self.request(STATUS))

    def tune(self, hz):
        if not (136_000_000 <= hz <= 174_000_000 or 400_000_000 <= hz <= 480_000_000) or hz % 10:
            raise ValueError("Expected 136–174 or 400–480 MHz at 10 Hz resolution.")
        if self.request(TUNE, hz):
            raise InstallError("Unexpected tune reply")
        return self.status()

    def capabilities(self):
        d = self.request(CAPS)
        if len(d) != 9 or d[:5] != b"FLCP\x01":
            raise InstallError("Unexpected capability response")
        bits = struct.unpack("<I", d[5:])[0]
        return dict(version=1, flags=bits, features=[f for i,f in enumerate(FEATURES) if bits & (1 << i)])

    def set_mode(self, mode, wide=False):
        if mode not in MODES or not isinstance(wide, bool):
            raise ValueError("Invalid Flux mode or bandwidth")
        if self.request(MODE, MODES.index(mode) | (0x100 if wide else 0)):
            raise InstallError("Unexpected mode response")
        return self.status()

    def select_vfo(self, index):
        if index not in (0, 1) or isinstance(index, bool):
            raise ValueError("VFO must be 0 or 1")
        if self.request(VFO, index):
            raise InstallError("Unexpected VFO response")
        return self.status()

    def health(self):
        d = self.request(0x65)
        if len(d) != 12:
            raise InstallError("Invalid health response length")
        temp, vref, cv = struct.unpack("<3i", d)
        if temp <= 0 or vref <= 0:
            raise InstallError("Invalid health ADC sample")
        return dict(temp_sum=temp, vref_sum=vref, temp_index=temp/vref, battery_cv=cv)

    def run_module(self, slot, *, timeout=30.0, allow_higher_api=False, on_report=None):
        """Queue exactly once; collect framed reports and completion without more writes."""
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 32:
            raise ValueError("Module slot must be an integer from 0 to 31")
        if not 0 < timeout <= 3600:
            raise ValueError("Module wait timeout must be in (0, 3600] seconds")
        try:
            if self.request(RUN, slot | (0x100 if allow_higher_api else 0)):
                raise InstallError("Unexpected launch acknowledgment")
        except LiveUnavailable as exc:
            raise LiveUnavailable("Launch acknowledgment lost; execution unknown. Do not retry blindly.") from exc
        return self._collect_module(slot, timeout, on_report)

    def serial_watermark(self):
        d = self.request(WATERMARK)
        if len(d)!=4:
            raise InstallError("Bad live replay watermark")
        return struct.unpack("<I",d)[0]

    def ram_status(self, job_id):
        """Read latest verified RAM job by signed serial; never launch a job."""
        if isinstance(job_id, bool) or not isinstance(job_id, int) or not 0 < job_id <= 0xffffffff:
            raise ValueError("Job ID must be a nonzero uint32")
        d = self.request(RAM_STATUS, job_id)
        if len(d) != 10:
            raise InstallError("Invalid RAM job status length")
        state, load, returned, ret = struct.unpack("<BiBi", d)
        if (state not in (0, 1, 2) or returned not in (0, 1)
                or (state == 0 and (load or returned or ret))
                or (state != 0 and load <= 0)
                or (state != 2 and returned) or (not returned and ret)):
            raise InstallError("Invalid RAM job status")
        return dict(job_id=job_id, state=("unknown", "accepted", "completed")[state],
                    load=load if state else None, returned=bool(returned) if state == 2 else None,
                    result=ret if returned else None)

    def _recover_upload(self):
        self.port.baudrate = 115200
        # No writes while a bounded data frame or baseline READY/COMMIT
        # wait may still be active. There has been no execution request.
        time.sleep(1.0)
        self.buf = self.text = b""
        if hasattr(self.port, "reset_input_buffer"):
            self.port.reset_input_buffer()
        self.status()

    def _fast_stage(self, cmd, data):
        from k6_live_v2 import MAGIC_V2, RAM_CHUNK, MAX_PAYLOAD, UPLOAD_RATE, request_bytes_v2
        bad = self.bad_response_crcs
        try:
            ack = self.request_v2(cmd, len(data))
        except (InstallError, OSError) as exc:
            if str(exc) in (ERRORS[1], ERRORS[2]):
                raise  # explicit rejection, no rate change
            # The radio may have switched even though its ACK was lost.
            self._recover_upload()
            raise LiveUploadError("Fast upload BEGIN failed; baseline recovered") from exc
        try:
            if ack != struct.pack("<I", UPLOAD_RATE) or self.bad_response_crcs != bad:
                raise InstallError("Invalid fast upload baud acknowledgment")
            self.port.baudrate = UPLOAD_RATE
            # Target guards its switch for 30 ms after transmitting ACK.
            time.sleep(.05)
            for offset in range(0, len(data), MAX_PAYLOAD):
                p = data[offset:offset+MAX_PAYLOAD]
                # No ASCII magic during the data-only baud excursion.
                d = self._exchange(RAM_CHUNK, lambda seq:
                    request_bytes_v2(RAM_CHUNK, seq, offset, p)[len(MAGIC_V2):])
                if d or self.bad_response_crcs != bad:
                    raise InstallError("Fast upload chunk integrity failure")
        except (InstallError, OSError, ValueError) as exc:
            self._recover_upload()
            raise LiveUploadError("Fast data upload failed; baseline recovered; no commit sent") from exc
        finally:
            self.port.baudrate = 115200
        time.sleep(.05)

    def negotiate_upload(self, features=None):
        """Validate 4 KiB of non-mutating data at the known uplink rate."""
        from k6_live_v2 import UPLOAD_PROBE, UPLOAD_RATE, RAM_MAX
        if self._upload_rate is not None:
            return self._upload_rate
        if features is None:
            features = self.capabilities()["features"]
        if "fast_upload" not in features or getattr(self.port,"baudrate",None)!=115200:
            self._upload_rate = 115200
            return self._upload_rate
        pattern = bytes((i*73+i//256)&255 for i in range(RAM_MAX))
        try:
            self._fast_stage(UPLOAD_PROBE,pattern)
        except LiveUploadError:
            # Only a verified data-only recovery permits fallback.
            self._upload_rate = 115200
        else:
            self.status()
            self._upload_rate = UPLOAD_RATE
        return self._upload_rate

    def run_ram(self, image, *, timeout=30.0, on_report=None, baseline=False):
        """Upload a complete typed standalone envelope and commit once.
        The host never retries or sends an abort after an uncertain commit.
        """
        from k6_live_v2 import RAM_BEGIN,RAM_CHUNK,RAM_COMMIT,RAM_MAX,MAX_PAYLOAD,RAM_FAST_BEGIN,RAM_READY
        image=bytes(image)
        if not 25<=len(image)<=RAM_MAX or image[:4]!=b"K6M\x01":
            raise ValueError("Expected standalone K6M image, at most 4096 bytes")
        code,entry,serial,level,flags,tlm,slot=struct.unpack_from("<HHIBBBB",image,4)
        if code+24!=len(image) or entry>=code or entry&1 or level not in (1,2) or flags or tlm or slot or not serial:
            raise ValueError("RAM execution requires standalone API 1–2 image with exact length")
        if not 0<timeout<=3600:
            raise ValueError("Module wait timeout must be in (0,3600] seconds")
        features=self.capabilities()["features"]
        if "ram_run" not in features:
            raise InstallError("Firmware lacks live RAM execution")
        if serial<=self.serial_watermark():
            raise InstallError("Module serial is not newer than radio's replay watermark")
        rate=115200 if baseline else self.negotiate_upload(features)
        if rate!=115200:
            # Staging is volatile, but never replay a failed real upload.
            # An explicit baseline READY acknowledgment is required before
            # any execution command may leave the host.
            self._fast_stage(RAM_FAST_BEGIN,image)
            try:
                if self.request_v2(RAM_READY,len(image)):
                    raise InstallError("Invalid RAM READY acknowledgment")
            except (InstallError, OSError) as exc:
                self._recover_upload()
                raise LiveUploadError("RAM READY failed; baseline recovered; no commit sent") from exc
        else:
            if self.request_v2(RAM_BEGIN,len(image)):
                raise InstallError("Invalid RAM BEGIN acknowledgment")
            for offset in range(0,len(image),MAX_PAYLOAD):
                if self.request_v2(RAM_CHUNK,offset,image[offset:offset+MAX_PAYLOAD]):
                    raise InstallError("Invalid RAM CHUNK acknowledgment")
        try:
            d=self.request_v2(RAM_COMMIT,len(image))
        except LiveUnavailable as exc:
            raise LiveUnavailable(f"RAM commit acknowledgment lost; job {serial}, execution unknown. Do not retry blindly; query ram-status.") from exc
        if len(d)!=4:
            raise InstallError(f"Malformed RAM commit acknowledgment; job {serial}, execution unknown")
        result=struct.unpack("<i",d)[0]
        if result!=code:
            raise InstallError(f"RAM image verification refused ({result})" if result<=0 else "RAM commit length mismatch; execution unknown")
        try:
            result=self._collect_module(255,timeout,on_report)
        except LiveUnavailable as exc:
            raise LiveUnavailable(f"RAM job {serial}: {exc} Query ram-status.") from exc
        result["job_id"]=serial
        result["slot"]=None
        result["ram"]=True
        return result

    def _collect_module(self, slot, timeout, on_report):
        seq = self.seq
        deadline = time.monotonic() + timeout
        reports = []
        while time.monotonic() < deadline:
            frame = self._frame(deadline)
            if frame is None:
                break
            cmd, received_seq, data = frame
            if (cmd, received_seq) != (RUN_EVENT, seq):
                continue
            if not data:
                raise InstallError("Empty module event; execution state unknown")
            if data[0] == 0 and len(data) <= 61:
                line = data[1:].decode("utf-8", "replace")
                reports.append(line)
                if on_report is not None:
                    on_report(line)
            elif data[0] == 1 and len(data) == 11:
                got_slot, load, returned, ret = struct.unpack("<BiBi", data[1:])
                if got_slot != slot or returned not in (0, 1) or (returned and load <= 0):
                    raise InstallError("Invalid module completion; execution state unknown")
                return dict(slot=slot, load=load, returned=bool(returned),
                            result=ret if returned else None, reports=reports)
            else:
                raise InstallError("Invalid module event; execution state unknown")
        raise LiveUnavailable("Module completion not received; it may still be running. Do not retry blindly.")

    def info(self):
        if not hasattr(self, "_info"):
            d = self.request(INFO)
            if len(d) != 16 or d[:4] != b"BLWF" or d[4] not in (2,4,5) or d[5]!=32:
                raise InstallError("Unexpected live library info")
            self._info = dict(protocol=d[4],slots=d[5],arena=struct.unpack_from("<H",d,6)[0],
                              build_id=d[8:] if any(d[8:]) else None)
        return self._info

    # The library parsers are transport-independent; reuse the exact packed
    # directory, fixed-slot and typed theme validation from the installer.
    library_directory = Cable.library_directory
    library_read = Cable.library_read
    slot_head = Cable.slot_head
    theme_at = Cable.theme_at

    def _bulk_supported(self):
        if self._bulk_cap is None:
            try:
                self._bulk_cap = "bulk_read" in self.capabilities()["features"]
            except InstallError as exc:
                if str(exc) != ERRORS[2]:
                    raise
                self._bulk_cap = False
        return self._bulk_cap

    def _burst(self, start, count, rate):
        if start % 64 or not 0 <= start < CHIP_BYTES or not 1 <= count <= (4 if rate == 115200 else 16) or start+count*256 > CHIP_BYTES:
            raise ValueError("Invalid bulk read bounds")
        seq = (self.seq + 1) % 256
        arg = start//64 | (count << 15) | (BAUDS.index(rate) << 20)
        bad = self.bad_response_crcs
        try:
            ack = self.request(BURST, arg)
        except LiveUnavailable as exc:
            raise LiveBulkError("Bulk acknowledgment lost") from exc
        actual = 96000000 // round(96000000/rate)
        if ack != struct.pack("<I", actual):
            raise LiveBulkError("Bad bulk baud acknowledgment")
        data = bytearray()
        try:
            if rate != 115200:
                self.port.baudrate = rate
            while True:
                f = self._frame(time.monotonic()+2)
                if f is None or f[1] != seq:
                    raise LiveBulkError("Missing or mismatched bulk frame")
                if f[0] == BURST_END:
                    if len(f[2]) != 18:
                        raise LiveBulkError("Malformed bulk completion")
                    result, sent, *errors = struct.unpack("<BB4I", f[2])
                    if result:
                        raise InstallError(ERRORS.get(result,"Bulk read refused"))
                    if sent != count or len(data) != count*256 or any(errors) or self.bad_response_crcs != bad:
                        raise LiveBulkError("Bulk length, UART or CRC verification failed")
                    break
                if f[0] != BURST_DATA or len(f[2]) != 260 or len(data) >= count*256:
                    raise LiveBulkError("Unexpected bulk frame")
                if struct.unpack_from("<I", f[2])[0] != start + len(data):
                    raise LiveBulkError("Out-of-order bulk frame")
                data.extend(f[2][4:])
        finally:
            if rate != 115200:
                self.port.baudrate = 115200
            # Radio waits for the last byte to leave, then restores the rate.
            # Do not transmit anything while the radio is transitioning.
            time.sleep(.04)
        return bytes(data)

    def _recover_download(self):
        if hasattr(self.port, "baudrate"):
            self.port.baudrate = 115200
        # Radio sends at most 4096 bytes and returns without host input.
        # No reset or legacy handshake, including on a lost acknowledgment.
        time.sleep(1.0)
        self.buf = self.text = b""
        if hasattr(self.port, "reset_input_buffer"):
            self.port.reset_input_buffer()
        self.status()

    def negotiate_download(self, addr=0):
        if self._download_rate is not None:
            return self._download_rate
        if not self._bulk_supported():
            return 115200
        self._download_rate = 115200
        if getattr(self.port, "baudrate", None) != 115200:
            return self._download_rate
        # A reference read at the base rate, compared byte-for-byte.
        start = min(addr & ~255, CHIP_BYTES-4096)
        ref = b"".join(self._burst(start+i*1024,4,115200) for i in range(4))
        for rate in reversed(BAUDS[1:]):
            try:
                if self._burst(start,16,rate) != ref:
                    raise LiveBulkError("Bulk probe differs from base-rate data")
                self.status()  # verify the return to 115200 before accepting
            except (LiveBulkError, LiveUnavailable, OSError):
                self._recover_download()
            else:
                self._download_rate = rate
                break
        return self._download_rate

    def read(self, addr, n, window=1, progress=None):
        if addr < 0 or addr % 64 or n < 0 or addr+n > CHIP_BYTES:
            raise ValueError("Live reads require 64-byte alignment within SPI")
        old_range = addr+n <= 0xb040 or (addr >= 0xf000 and addr+n <= 0x10000)
        if not n:
            return b""
        if (n >= 4096 or not old_range) and self._bulk_supported():
            rate = self.negotiate_download(addr) if n >= 4096 else 115200
            start = addr & ~255
            end = (addr+n+255) & ~255
            out = bytearray()
            while start+len(out) < end:
                offset = start+len(out)
                count = min(4 if rate==115200 else 16, (end-offset)//256)
                try:
                    out.extend(self._burst(offset,count,rate))
                except (LiveBulkError, OSError):
                    self._recover_download()
                    if rate == 115200:
                        raise
                    self._download_rate = rate = 115200
                    continue  # retry only the read, never a mutation or launch
                if progress:
                    progress(min(n,max(0,start+len(out)-addr)),n)
            return bytes(out[addr-start:addr-start+n])
        if not old_range:
            raise InstallError("Firmware lacks live bulk SPI read; update it or explicitly use legacy backup")
        blocks = bytearray()
        for offset in range(addr, addr+n, 64):
            block = self.request(READ, offset)
            if len(block) != 64:
                raise InstallError("Short live SPI read")
            blocks.extend(block)
            if progress: progress(min(n,len(blocks)),n)
        return bytes(blocks[:n])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("caps")
    sub.add_parser("health")
    sub.add_parser("upload-probe", help="validate upload speed without executing or writing a module")
    backup = sub.add_parser("backup", help="non-rebooting full SPI backup")
    backup.add_argument("-o", "--output", required=True)
    mode = sub.add_parser("mode")
    mode.add_argument("mode", choices=MODES)
    mode.add_argument("--wide", action="store_true")
    vfo = sub.add_parser("vfo")
    vfo.add_argument("index", type=int, choices=(0,1))
    tune = sub.add_parser("tune")
    tune.add_argument("hz", type=int)
    run = sub.add_parser("run", help="launch an installed foreground module; wait for reports and completion")
    run.add_argument("slot", type=int)
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--allow-higher-api", action="store_true",
                     help="explicitly allow modules above level 2 (may transmit or write hardware)")
    ram = sub.add_parser("run-ram", help="compile and execute a temporary API 1–2 C module without writing SPI")
    ram.add_argument("source")
    auth = ram.add_mutually_exclusive_group(required=True)
    auth.add_argument("--key-file", help="keyed firmware: sign with this existing key")
    auth.add_argument("--cable-only", action="store_true", help="physical-cable-only firmware uses CRC, not authentication")
    ram.add_argument("--timeout",type=float,default=30)
    ram.add_argument("--baseline-upload",action="store_true",help="skip fast upload negotiation")
    job = sub.add_parser("ram-status", help="query latest boot-local RAM job by signed serial")
    job.add_argument("job_id", type=int)
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=0.5)
    a = p.parse_args()
    if a.cmd == "watch" and a.interval < 0.2:
        p.error("Minimum watch interval is 0.2 s")
    try:
        with serial_lock(a.port), open_port(SimpleNamespace(port=a.port)) as port:
            link = LiveLink(port)
            if a.cmd == "watch":
                while True:
                    print(json.dumps(link.status()), flush=True)
                    time.sleep(a.interval)
            elif a.cmd == "backup":
                from k6_install import backup as save_backup
                save_backup(link, a.output)
            elif a.cmd == "caps":
                print(json.dumps(link.capabilities(), indent=2))
            elif a.cmd == "health":
                print(json.dumps(link.health(), indent=2))
            elif a.cmd == "upload-probe":
                features=link.capabilities()["features"]
                start=time.monotonic()
                rate=link.negotiate_upload(features)
                print(json.dumps(dict(baud=rate, fast_upload_supported="fast_upload" in features,
                                      probe_seconds=round(time.monotonic()-start,3)),indent=2))
            elif a.cmd == "mode":
                print(json.dumps(link.set_mode(a.mode,a.wide), indent=2))
            elif a.cmd == "vfo":
                print(json.dumps(link.select_vfo(a.index), indent=2))
            elif a.cmd == "ram-status":
                print(json.dumps(link.ram_status(a.job_id), indent=2))
            elif a.cmd == "run-ram":
                from k6_install import k6mod, Serials
                level=k6mod.source_level(a.source)
                if level not in (1,2):
                    raise ValueError("Temporary live execution is currently restricted to API levels 1–2")
                code,entry=k6mod.build(a.source)
                key=None if a.cable_only else k6mod.read_key(a.key_file)
                if key==k6mod.PLACEHOLDER:
                    raise ValueError("Do not sign with the key placeholder")
                serials=Serials()
                serials.advance_after(link.serial_watermark())
                serial=serials.next()
                if serial>0xffffffff:
                    raise ValueError("Module replay watermark exhausted")
                image=k6mod.image(code,entry,serial,key,level=level)
                print(f"RAM job ID: {serial}",file=sys.stderr,flush=True)
                result=link.run_ram(image,timeout=a.timeout,baseline=a.baseline_upload,
                                    on_report=lambda line:print(line,file=sys.stderr,flush=True))
                print(json.dumps(result,indent=2))
                if not result["returned"]:
                    p.exit(1,"Temporary module did not execute to completion\n")
            elif a.cmd == "run":
                result = link.run_module(a.slot, timeout=a.timeout,
                                         allow_higher_api=a.allow_higher_api,
                                         on_report=lambda line: print(line, file=sys.stderr, flush=True))
                print(json.dumps(result, indent=2))
                if not result["returned"]:
                    p.exit(1, "Module did not execute to completion\n")
            elif a.cmd == "tune":
                print(json.dumps(link.tune(a.hz), indent=2))
            else:
                print(json.dumps(link.status(), indent=2))
    except KeyboardInterrupt:
        pass
    except (InstallError, OSError, ValueError) as exc:
        p.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
