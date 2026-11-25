"""Decode the boot-local MIC A/B result mailbox (MAB1, 3-stage PCM metrics).

This format is emitted via the module API say callback after TX key-up and
queried through live serial command 0x86. Reading never invokes a module.
No result is a valid state: the mailbox is cleared at the start of a new run.
"""
import base64
import binascii
import struct

STAGES=("B31", "W31", "W08")


def crc16_xmodem(data: bytes)->int:
    value=0
    for byte in data:
        value^=byte<<8
        for _ in range(8):
            value=((value<<1)^((0x1021 if value&0x8000 else 0)))&0xFFFF
    return value


def decode_mic_result(response: bytes) -> dict:
    """Response with live protocol status removed: 0 => none, 1 + b64 => data."""
    if response==b"\0":
        return {"available":False,"storage":"boot-local RAM","survives_reboot":False}
    if len(response)!=45 or response[0]!=1:
        raise ValueError("MIC A/B mailbox status or length invalid")
    encoded=response[1:]
    try:
        data=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error) as exc:
        raise ValueError("MIC A/B mailbox base64 invalid") from exc
    if len(data)!=33 or data[0]!=1:
        raise ValueError("MIC A/B mailbox format/version invalid")
    expected=struct.unpack_from("<H",data,31)[0]
    if crc16_xmodem(data[:31])!=expected:
        raise ValueError("MIC A/B mailbox checksum mismatch")
    rows=[]
    for i,stage in enumerate(STAGES):
        tone,clipped,mean,vox,n=struct.unpack_from("<HHHHH",data,1+i*10)
        if n < 2400 or clipped>n:
            raise ValueError(f"MIC A/B {stage} sample count or clip count invalid")
        rows.append({"stage":stage,"gain":31 if i<2 else 8,
                     "register_written":i!=0,"tone_1khz_pcm_peak":tone,
                     "clipped_samples":clipped,"mean_abs_pcm":mean,
                     "vox_max":vox,"samples":n})
    return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
            "version":1,"stages":rows}
