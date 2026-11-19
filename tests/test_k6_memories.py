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
