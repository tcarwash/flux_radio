"""Read-only Flux/Aura memory layout reader. Never writes SPI."""
import struct
from types import SimpleNamespace
from k6_install import open_port, open_cable, crc16_xmodem, InstallError
from k6_port_lock import serial_lock
from k6_memories import normalize

DCS = "023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 114 115 116 122 125 131 132 134 143 145 152 155 156 162 165 172 174 205 212 223 225 226 243 244 245 246 251 252 255 261 263 265 266 271 274 306 311 315 325 331 332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446 452 454 455 462 464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 645 654 662 664 703 712 723 731 732 734 743 754".split()

def decode_location(sector):
    # WearLeveledRegion<212>, 16-byte bitmap; first set bit selects current slot.
    if len(sector) != 4096:
        raise ValueError("Incomplete settings sector")
    slot = next((i for i in range(19) if sector[i//8] & (1 << (i%8))), None)
    if slot is None:
        raise ValueError("No current settings record")
    start = 16 + slot * 214
    payload = sector[start:start+212]
    if crc16_xmodem(payload) != int.from_bytes(sector[start+212:start+214], "little"):
        raise ValueError("Settings CRC failed; unsupported layout or unsaved settings")
    lat, lon = struct.unpack_from("<ii", payload, 170)
    if not (-9000000 <= lat <= 9000000 and -18000000 <= lon <= 18000000):
        raise ValueError("No valid saved APRS location. Set LAT/LON or run GPS Sync on the radio.")
    return {"lat": lat / 100000, "lon": lon / 100000}

def bcd(raw):
    if any(b >> 4 > 9 or b & 15 > 9 for b in raw):
        raise ValueError("Invalid BCD frequency")
    return int("".join(f"{b:02x}" for b in reversed(raw))) * 10

def tone(value):
    if value == 0: return "", "", "N"
    if 1 <= value <= 210:
        return "DTCS", DCS[(value-1)%105], "R" if value > 105 else "N"
    if 251 <= value <= 3000:
        return "Tone", f"{value/10:.1f}", "N"
    raise ValueError("Unsupported subaudio code")

def decode_channels(data):
    if len(data) != 32000:
        raise ValueError("Incomplete channel table")
    rows, warnings = [], []
    for slot in range(1000):
        raw = data[slot*32:slot*32+32]
        if raw == b"\xff"*32: continue
        try:
            rx, tx = bcd(raw[:4]), bcd(raw[4:8])
            if not rx: raise ValueError("Empty RX frequency")
            rkind, rval, rpol = tone(int.from_bytes(raw[8:10], "little"))
            tkind, tval, tpol = tone(int.from_bytes(raw[10:12], "little"))
            name = raw[20:].split(b"\0",1)[0].split(b"\xff",1)[0].decode("utf-8")
            mode = "NFM" if raw[15]&64 else "FM"
            decoder = int.from_bytes(raw[16:20], "little")
            if decoder & 0xffffff00 == 0xbf6d0000:
                mode = {1:"AM",2:"USB",3:"CW",4:"CW"}.get(decoder&255,mode)
            row = dict(Location=str(slot), Name=name, Frequency=f"{rx/1e6:.6f}",
                       Duplex="off" if not tx else "+" if tx>rx else "-" if tx<rx else "",
                       Offset=f"{abs(tx-rx)/1e6:.6f}" if tx else "0", Mode=mode,
                       Skip="" if raw[15]&4 else "S", Power={0:"High",1:"Mid",2:"Low"}.get(raw[14],""))
            if tkind == "Tone": row["rToneFreq"] = tval
            if rkind == "Tone": row["cToneFreq"] = rval
            if tkind == "DTCS": row["DtcsCode"] = tval
            if rkind == "DTCS": row["RxDtcsCode"] = rval
            row["DtcsPolarity"] = tpol+rpol
            if tkind == "Tone" and not rkind: row["Tone"] = "Tone"
            elif tkind == rkind == "Tone" and tval == rval: row["Tone"] = "TSQL"
            elif tkind == rkind == "DTCS" and tval == rval: row["Tone"] = "DTCS"
            elif tkind or rkind:
                row.update(Tone="Cross", CrossMode=tkind+"->"+rkind)
            rows.append(normalize(row))
        except (ValueError, UnicodeError) as exc:
            warnings.append(f"Memory {slot}: {exc}")
    return {"channels": rows, "warnings": warnings}

def read_radio(port, what):
    if what not in ("location", "channels"): raise ValueError("Unknown radio read")
    with serial_lock(port):
        with open_port(SimpleNamespace(port=port)) as serial:
            link = open_cable(serial, lambda *_: None, wait=5)
            if link is None:
                raise InstallError("No Flux cable session; put the radio in its supported cable-ready screen.")
            try:
                info = link.info()
                if info["protocol"] not in (1, 2, 3, 4, 5):
                    raise InstallError("Unsupported cable protocol for this reader")
                if link.read(0xb000,64,window=1)[:4] != b"AURA":
                    raise InstallError("Unrecognized Aura storage marker")
                if what == "location":
                    return decode_location(link.read(0x9000,4096,window=4))
                # Check the settings record CRC as a layout sanity check even if position is unset.
                sector = link.read(0x9000,4096,window=4)
                try: decode_location(sector)
                except ValueError as exc:
                    if not str(exc).startswith("No valid saved APRS location"): raise
                return decode_channels(link.read(0,32000,window=4))
            finally:
                link.end()
