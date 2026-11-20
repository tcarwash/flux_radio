import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_memories as m
import k6_store

def test_chirp_roundtrip():
    row=m.channel("TEST",146.94,"-",.6)
    row.update(Tone="Cross",CrossMode="Tone->DTCS",rToneFreq="100.0",RxDtcsCode="125",DtcsPolarity="NR")
    out=m.read_csv(m.write_csv([row]))[0]
    assert out == dict(row,Location="1")

def test_builtin_rx_only():
    for name,rows in m.builtins().items():
        if name.startswith("NOAA") or "(RX)" in name:
            assert all(r["Duplex"]=="off" for r in rows)

def test_repeater_filter():
    r=dict(id=1,callsign="W7TEST",latitude=45,longitude=-122,frequency=146940000,offset=-600000,mode="FM",operational=1,encode="100",decode="0")
    rows=m.repeaters(45,-122,10,records=[r,dict(r,restriction="private"),dict(r,latitude=50)])
    assert len(rows)==1
    assert rows[0]["Tone"]=="Tone" and rows[0]["Offset"]=="0.6"
    assert m.repeaters(45,-122,10,band="70cm",records=[r])==[]

def test_routes():
    import io
    client=k6_store.create_app().test_client()
    row=m.channel("WX",162.55,"off")
    response=client.post("/memories/export",json={"format":"csv","channels":[row]})
    assert response.status_code==200
    imported=client.post("/memories/import",data={"file":(io.BytesIO(response.data),"test.csv")})
    assert imported.json["channels"][0]["Duplex"]=="off"
    assert client.post("/memories/export",json={"channels":[{}]}).status_code==400
    assert client.get("/memories/repeaters?lat=100&lon=0&radius=10").status_code==400

def test_radio_location_crc_and_wear_level():
    import struct
    from k6_radio_memories import decode_location
    from k6_install import crc16_xmodem
    import pytest
    sector = bytearray(b"\xff"*4096)
    sector[0] = 0xfe
    payload = bytearray(212)
    struct.pack_into("<ii", payload, 170, 4560000, -12240000)
    start = 16 + 214
    sector[start:start+212] = payload
    sector[start+212:start+214] = crc16_xmodem(payload).to_bytes(2,"little")
    assert decode_location(sector) == {"lat":45.6,"lon":-122.4}
    sector[start] ^= 1
    with pytest.raises(ValueError,match="CRC"):
        decode_location(sector)

def test_radio_channels_and_slots():
    from k6_radio_memories import decode_channels
    import struct
    data=bytearray(b"\xff"*32000)
    raw=bytearray(32)
    raw[:4]=bytes.fromhex("00009414")
    raw[4:8]=bytes.fromhex("00003414")
    struct.pack_into("<HH",raw,8,885,1000)
    raw[14]=2
    raw[15]=0x44
    raw[20:24]=b"TEST"
    data[0:32]=raw
    data[999*32:1000*32]=raw
    result=decode_channels(data)
    assert not result["warnings"]
    r=result["channels"][0]
    assert (r["Location"],r["Name"],r["Frequency"],r["Duplex"],r["Offset"]) == ("0","TEST","149.400000","-","6.000000")
    assert r["Tone"]=="Cross" and r["rToneFreq"]=="100.0" and r["cToneFreq"]=="88.5"
    assert r["Mode"]=="NFM" and r["Power"]=="Low"
    assert result["channels"][1]["Location"]=="999"

def test_radio_read_routes_mocked(monkeypatch):
    import k6_radio_memories
    calls=[]
    def read(port,what):
        calls.append((port,what))
        return {"lat":45,"lon":-122} if what=="location" else {"channels":[],"warnings":[]}
    monkeypatch.setattr(k6_radio_memories,"read_radio",read)
    client=k6_store.create_app().test_client()
    assert client.post("/memories/radio/location").status_code==400
    with client.session_transaction() as session:
        session["radio_port"]="mock-port"
    page=client.get("/memories")
    assert b"Read location from radio" in page.data
    assert b"Read memories from radio" in page.data
    assert b"Advanced channel fields" in page.data
    assert b"Merge into radio" in page.data
    assert b"Replace radio memories" in page.data
    with client.session_transaction() as session:
        token=session["memory_token"]
    assert client.post("/memories/radio/location").status_code==403
    assert client.post("/memories/radio/location",json={"token":token}).json=={"lat":45,"lon":-122}
    assert client.post("/memories/radio/channels",json={"token":token}).json["channels"]==[]
    assert calls==[("mock-port","location"),("mock-port","channels")]
    assert client.post("/memories/radio/write").status_code==404


def test_read_location_packed_protocol_is_read_only(monkeypatch):
    from contextlib import nullcontext
    import struct
    import k6_radio_memories as r
    from k6_install import crc16_xmodem
    payload=bytearray(212)
    struct.pack_into("<ii",payload,170,4560000,-12240000)
    sector=bytearray(b"\xff"*4096)
    sector[16:228]=payload
    sector[228:230]=crc16_xmodem(payload).to_bytes(2,"little")
    calls=[]
    class Link:
        def info(self):
            return {"protocol":5}
        def read(self,addr,n,window):
            calls.append((addr,n))
            if (addr,n)==(0xb000,64):return b"AURA"+b"\xff"*60
            if (addr,n)==(0x9000,4096):return bytes(sector)
            raise AssertionError("unexpected read")
        def end(self):calls.append("end")
    monkeypatch.setattr(r,"serial_lock",lambda p:nullcontext())
    monkeypatch.setattr(r,"open_port",lambda a:nullcontext(object()))
    monkeypatch.setattr(r,"open_cable",lambda *a,**kw:Link())
    assert r.read_radio("mock-port","location")=={"lat":45.6,"lon":-122.4}
    assert calls==[(0xb000,64),(0x9000,4096),"end"]
