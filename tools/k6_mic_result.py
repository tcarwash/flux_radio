"""Decode the boot-local MIC A/B result mailbox (MAB1, 3-stage PCM metrics).

This format is emitted via the module API say callback after TX key-up and
queried through live serial command 0x86. Reading never invokes a module.
No result is a valid state: the mailbox is cleared at the start of a new run.
"""
import base64
import binascii
import struct

STAGES=("B31", "W31", "W08")
# v2 captures time windows relative to first *valid* ISR PCM sample; the
# sampler's first three warm-up ISR ticks are not represented in the windows.
TIME_BIN_END_MS=(20,40,60,80,100,120,200,320,480,640,800,1000)


def decompress_pcm(code: int)->int:
    """Decode the v2 4-bit mantissa/4-bit exponent lower-bound estimate."""
    if code<16:return code
    return (16+(code&15))<<((code>>4)-1)



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
    if len(data)!=33 or data[0] not in (1,2):
        raise ValueError("MIC mailbox format/version invalid")
    expected=struct.unpack_from("<H",data,31)[0]
    if crc16_xmodem(data[:31])!=expected:
        raise ValueError("MIC A/B mailbox checksum mismatch")
    if data[0]==2:
        clipped,vox,n=struct.unpack_from("<HHH",data,25)
        if n<7600 or n>8100 or clipped>n or vox>1023:
            raise ValueError("MIC TIME sample/VOX/clip totals invalid")
        windows=[]
        start=0
        for i,end in enumerate(TIME_BIN_END_MS):
            windows.append({"start_ms":start,"end_ms":end,
                            "mean_abs_pcm":decompress_pcm(data[1+i*2]),
                            "tone_1khz_pcm_peak":decompress_pcm(data[2+i*2])})
            start=end
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":2,"measurement":"time_resolved_mic_pcm",
                "sampling_rate_hz_nominal":8000,
                "quantization":"approximate logarithmic PCM counts (up to 6.25% step)",
                "window_origin":"first valid PCM after 3 ISR warm-up ticks",
                "clipped_samples_total":clipped,"vox_max":vox,"samples":n,
                "windows":windows}
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
