"""Cross-language C TNC / Python host wire-format conformance, no hardware."""
import ctypes as C
import sys
from pathlib import Path

from test_k6_flux_kiss_module import lib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import k6_kiss_tnc as host


def test_python_control_frame_decoded_by_firmware_c(lib):
    lib.rx_reset()
    raw=host.control(host.SET,(1).to_bytes(2,'little'))
    messages=[]
    for b in raw:
        result=lib.rx_feed(b)
        if result==1:
            messages.append((lib.rx_type(),bytes(lib.rx_byte(i) for i in range(lib.rx_len()))))
    assert messages==[(host.HARDWARE,host.PREFIX+bytes([host.SET,1,0]))]


def test_firmware_list_mode_decodes_on_host(lib):
    def to_host(payload):
        encoded=C.create_string_buffer(96)
        raw=C.create_string_buffer(payload)
        n=lib.kiss_encode(encoded,96,host.HARDWARE,raw,len(payload))
        assert n
        frames=host.KISSDecoder().feed(encoded.raw[:n])
        assert len(frames)==1
        return host.decode_control(frames[0])

    mode=C.create_string_buffer(24)
    n=lib.list_body(mode,1,1,75,b"APRS-1200",9)
    head=C.create_string_buffer(8)
    h=lib.resp_hdr(head,1,0)
    parsed=host.parse_mode_reply(to_host(head.raw[:h]+mode.raw[:n]))
    assert parsed["id"]==1
    assert parsed["name"]=="APRS-1200"
    assert parsed["packet_tx"] and not parsed["packet_rx"] and not parsed["voice"]
    assert parsed["max_payload"]==75

    end=C.create_string_buffer(24)
    n=lib.list_body(end,0xffff,0,0,None,0)
    assert host.parse_mode_reply(to_host(head.raw[:h]+end.raw[:n])) is None


def test_firmware_mode_registry_matches_host_suggested_identifier(lib):
    n=C.c_uint();id_=C.c_uint();flags=C.c_uint();maxp=C.c_uint()
    label=C.create_string_buffer(12)
    assert lib.backend(0,C.byref(id_),C.byref(flags),C.byref(maxp),C.byref(n),label)
    assert id_.value in host.MODE_LABELS
    assert label.raw[:n.value].decode('ascii')==host.MODE_LABELS[id_.value]
