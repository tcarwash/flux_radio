"""Live serial UI transport: 16 CRC-checked pixel chunks and guarded UI keys."""
from pathlib import Path
import struct
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_install import InstallError, cps_frame
from k6_live import (
    CAPS, SCREENSHOT, SCREEN_PAGE, SCREEN_END, VIRTUAL_KEY,
    LiveLink, LiveUnavailable, KEY_NAMES,
)
from test_k6_live_control import Port


def response(request, *, lost=None, duplicate=None, malformed=None, corrupt=False):
    cmd,seq=request[6],request[7]
    if cmd==CAPS:
        return cps_frame(CAPS,seq,b"\x00FLCP\x01"+struct.pack("<I",(1<<19)|(1<<20)))
    if cmd==VIRTUAL_KEY:
        return cps_frame(VIRTUAL_KEY,seq,b"\x00")
    if cmd!=SCREENSHOT:
        raise AssertionError(cmd)
    frames=[cps_frame(SCREENSHOT,seq,b"\0FLSC\x01\x80\x40\x08\x40")]
    for i in range(16):
        if i==lost:
            continue
        addr=(seq<<8)|i
        data=bytes([i])*64
        if i==malformed:
            data=data[:-1]
        frame=cps_frame(SCREEN_PAGE,addr,data)
        if corrupt and i==7:
            frame=frame[:-1]+bytes([frame[-1]^1])
        frames.append(frame)
        if i==duplicate:
            frames.append(frame)
    frames.append(cps_frame(SCREEN_END,seq,b"\0\x10"))
    return b"".join(frames)


def test_screenshot_page_order_and_exact_bytes():
    link=LiveLink(Port(response),initial_seq=0,timeout=.05)
    pixels=link.screenshot()
    assert len(pixels)==1024
    assert pixels==b"".join(bytes([i])*64 for i in range(16))


@pytest.mark.parametrize("fault,kw",[
    ("missing",{"lost":9}),
    ("duplicate",{"duplicate":9}),
    ("short",{"malformed":9}),
    ("crc",{"corrupt":True}),
])
def test_incomplete_or_bad_screenshot_is_refused(fault,kw):
    link=LiveLink(Port(lambda data:response(data,**kw)),initial_seq=0,timeout=.05)
    with pytest.raises((InstallError,LiveUnavailable)):
        link.screenshot()


def test_screen_disabled_on_old_firmware():
    def reply(req):
        return cps_frame(CAPS,req[7],b"\x00FLCP\x01"+struct.pack("<I",0))
    port=Port(reply)
    link=LiveLink(port,initial_seq=0)
    with pytest.raises(InstallError,match="lacks live screenshot"):
        link.screenshot()
    assert [x[6] for x in port.writes]==[CAPS]


def test_logical_keys_request_through_existing_framing():
    p=Port(response)
    link=LiveLink(p,initial_seq=0)
    assert link.virtual_key("MENU")=={"key":"MENU","gesture":"short","queued":True}
    assert link.virtual_key("DOWN",long=True)["gesture"]=="long"
    assert [x[6] for x in p.writes]==[CAPS,VIRTUAL_KEY,CAPS,VIRTUAL_KEY]
    assert int.from_bytes(p.writes[1][8:12],"little")==KEY_NAMES.index("MENU")
    assert int.from_bytes(p.writes[3][8:12],"little")==KEY_NAMES.index("DOWN")+0x100


@pytest.mark.parametrize("bad",["PTT","REBOOT","POWEROFF","",21,"CTRL-C"])
def test_unknown_and_transmit_keys_never_reach_radio(bad):
    p=Port(response)
    with pytest.raises(ValueError):
        LiveLink(p).virtual_key(bad)
    assert p.writes==[]


def test_virtual_key_error_from_radio_is_reported():
    p=Port(lambda r: response(r) if r[6]==CAPS else cps_frame(VIRTUAL_KEY,r[7],b"\x02"))
    with pytest.raises(InstallError,match="Unsupported command"):
        LiveLink(p).virtual_key("1",long=True)


def test_lcd_bit_order_matches_st7565_graphics_buffer():
    # Page-major 8-pixel vertical groups, low bit = top row.
    from PIL import Image
    raw=bytearray(1024)
    raw[0]=1        # x0, y0 black pixel
    raw[128+1]=128  # x1, y15 black pixel
    img=Image.new("L",(128,64),255)
    px=img.load()
    for page in range(8):
        for x in range(128):
            value=raw[page*128+x]
            for bit in range(8):
                px[x,page*8+bit]=0 if value&(1<<bit) else 255
    assert img.getpixel((0,0))==0
    assert img.getpixel((1,15))==0
    assert img.getpixel((1,14))==255


def test_optin_overlay_and_old_protocol_unchanged():
    src=(ROOT/"firmware/k6-aura/live-serial.rs").read_text()
    builder=(ROOT/"firmware/k6-aura/build-fd6818b.sh").read_text()
    app_overlay=(ROOT/"firmware/k6-aura/apply-remote-ui.py").read_text()
    assert "#[cfg(k6_remote_ui)]" in src
    assert "MODULE_PAGE_BUFFER_STORAGE" in src
    assert "page*140+half*64" in src
    assert "K6_REMOTE_UI" in builder
    assert "apply-remote-ui.py" in builder
    assert "self.keypad.queue_virtual_key" in app_overlay
    assert "self.radio.ptt_asserted()" in app_overlay
