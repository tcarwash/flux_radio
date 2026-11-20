"""Portable Flux memory packs and CHIRP interchange (no raw radio writes)."""
import csv
import io
import json
import math
from decimal import Decimal, InvalidOperation
from urllib.request import urlopen

FIELDS = "Location Name Frequency Duplex Offset Tone rToneFreq cToneFreq DtcsCode DtcsPolarity RxDtcsCode CrossMode Mode TStep Skip Power Comment".split()
MODES = {"FM", "NFM", "AM", "NAM", "WFM", "USB", "LSB", "CW", "DV", "DMR", "DIG", "PKT", "Auto"}
URL = "https://hearham.com/api/repeaters/v1"

def hz(value):
    try:
        n = Decimal(str(value)) * 1000000
        if not n.is_finite() or n != n.to_integral_value() or not 0 <= n <= 10**10:
            raise ValueError("Invalid frequency")
        return int(n)
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid frequency") from None

def normalize(row):
    r = {k: str(row[k]).strip() if row.get(k) is not None else "" for k in FIELDS}
    if hz(r["Frequency"]) <= 0:
        raise ValueError("Frequency must be positive")
    r["Duplex"] = r["Duplex"].lower()
    if r["Duplex"] not in ("", "+", "-", "split", "off"):
        raise ValueError("Unsupported duplex: " + r["Duplex"])
    r["Offset"] = r["Offset"] or "0"
    offset = hz(r["Offset"])
    if r["Duplex"] == "-" and offset >= hz(r["Frequency"]):
        raise ValueError("Invalid transmit frequency")
    if r["Duplex"] == "split" and not offset:
        raise ValueError("Invalid split frequency")
    r["Mode"] = r["Mode"] or "FM"
    if r["Mode"] not in MODES:
        raise ValueError("Unsupported mode: " + r["Mode"])
    for k, v in {"rToneFreq":"88.5","cToneFreq":"88.5","DtcsCode":"023","RxDtcsCode":"023","DtcsPolarity":"NN","CrossMode":"Tone->Tone","TStep":"5.00"}.items():
        r[k] = r[k] or v
    if r["Tone"] not in ("", "Tone", "TSQL", "DTCS", "Cross"):
        raise ValueError("Unsupported tone mode")
    return r

def read_csv(data):
    reader = csv.DictReader(io.StringIO(data.lstrip("\ufeff")))
    if not reader.fieldnames or "Frequency" not in reader.fieldnames:
        raise ValueError("Expected CHIRP CSV with Frequency header")
    rows = []
    for i, row in enumerate(reader, 2):
        if not any(row.values()):
            continue
        try:
            rows.append(normalize(row))
        except ValueError as exc:
            raise ValueError(f"CSV line {i}: {exc}") from exc
    return rows

def write_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, FIELDS)
    writer.writeheader()
    for i, row in enumerate(rows):
        writer.writerow(dict(normalize(row), Location=i + 1))
    return stream.getvalue()

def pack(name, rows):
    return {"format":"flux-memory-pack","version":1,"name":name,"channels":[normalize(r) for r in rows]}

def read_pack(data):
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get("format") != "flux-memory-pack" or obj.get("version") != 1 or not isinstance(obj.get("channels"), list):
        raise ValueError("Invalid Flux memory pack")
    if not all(isinstance(r, dict) for r in obj["channels"]):
        raise ValueError("Invalid channel")
    return [normalize(r) for r in obj["channels"]]

def channel(name, frequency, duplex="", offset=0, mode="FM", comment=""):
    return normalize({"Name":name,"Frequency":f"{frequency:.6f}","Duplex":duplex,"Offset":str(offset),"Mode":mode,"Comment":comment})

def builtins():
    return {
        "NOAA Weather": [channel(f"WX{i+1}", f, "off", comment="NOAA weather radio") for i,f in enumerate([162.400,162.425,162.450,162.475,162.500,162.525,162.550])],
        "US Amateur Simplex": [channel(n,f) for n,f in [("2M CALL",146.520),("70CM CALL",446.000),("1.25M CALL",223.500),("6M CALL",52.525)]],
        "US Marine VHF (RX)": [channel(n,f,"off") for n,f in [("CH 16",156.800),("CH 9",156.450),("CH 13",156.650),("CH 22A",157.100),("CH 6",156.300),("CH 70",156.525)]],
        "US Digital Channels": [channel("APRS",144.390,comment="North American APRS"),channel("PACKET",145.050,comment="Check local band plan")],
        "US FRS / GMRS / MURS (RX)": [channel(f"FRS{i+1}",f,"off",mode="NFM") for i,f in enumerate([462.5625+j*.025 for j in range(7)]+[467.5625+j*.025 for j in range(7)]+[462.550+j*.025 for j in range(8)])]+[channel(f"MURS{i+1}",f,"off",mode="NFM") for i,f in enumerate([151.820,151.880,151.940,154.570,154.600])],
    }

def distance(lat, lon, a, b):
    p,q = map(math.radians,(lat,a))
    d = math.sin((q-p)/2)**2 + math.cos(p)*math.cos(q)*math.sin(math.radians(b-lon)/2)**2
    return 3958.8 * 2 * math.asin(math.sqrt(min(1,max(0,d))))

def repeaters(lat, lon, radius, band="all", open_only=True, records=None):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius <= 500):
        raise ValueError("Invalid location or radius (maximum 500 miles)")
    if records is None:
        with urlopen(URL, timeout=20) as response:
            records = json.loads(response.read(32*1024*1024))
    result = []
    for r in records:
        try:
            if r.get("operational") != 1 or r.get("mode") not in ("FM","NFM"):
                continue
            if open_only and str(r.get("restriction") or "").strip().lower() not in ("","open","none"):
                continue
            f, o = int(r["frequency"]), int(r["offset"])
            if not (144000000 <= f <= 148000000 or 420000000 <= f <= 450000000):
                continue
            if band == "2m" and f >= 148000000 or band == "70cm" and f < 420000000:
                continue
            miles = distance(lat,lon,float(r["latitude"]),float(r["longitude"]))
            if miles > radius:
                continue
            row = channel(r["callsign"], f/1e6, "+" if o>0 else "-" if o<0 else "", abs(o)/1e6, r["mode"], f"HearHam #{r['id']} · {r.get('city','')} · {miles:.1f} mi")
            enc, dec = float(r.get("encode") or 0), float(r.get("decode") or 0)
            if enc > 0:
                row.update(Tone="Tone", rToneFreq=str(enc))
            if dec > 0:
                row.update(Tone="TSQL" if enc == dec else "Cross", cToneFreq=str(dec), CrossMode="Tone->Tone" if enc else "->Tone")
            result.append((miles,row))
        except (ValueError,TypeError,KeyError,OverflowError):
            continue
    return [r for _,r in sorted(result,key=lambda item:item[0])]
