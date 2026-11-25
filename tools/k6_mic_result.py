"""Decode boot-local microphone result mailbox (MAB1 transport, v1/v2/v3).

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
RAW_BIN_END_MS=(20,40,80,120,200,1000)


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
    if len(data)!=33 or data[0] not in (1,2,3,4):
        raise ValueError("MIC mailbox format/version invalid")
    expected=struct.unpack_from("<H",data,31)[0]
    if crc16_xmodem(data[:31])!=expected:
        raise ValueError("MIC A/B mailbox checksum mismatch")
    if data[0]==4:
        max_vox=struct.unpack_from("<H",data,28)[0]
        completed=data[30]
        if completed!=9 or max_vox>1023:
            raise ValueError("MIC ROUTES stages/VOX invalid")
        rows=[]
        for idx,mode in enumerate((4,9,5,1)):
            pcm,tone,high=data[1+idx*3:4+idx*3]
            rows.append({"path":"af_to_pa11","af_select":mode,
                        "steady_pcm_mean_abs":decompress_pcm(pcm),
                        "steady_pcm_1khz_peak":decompress_pcm(tone),
                        "pa11_raw_high_fraction":round(high/255,4)})
        for idx,sel in enumerate((9,11,12,13,14)):
            high,trans,vox=data[13+idx*3:16+idx*3]
            rows.append({"path":"gp0_to_pb1","gp0_output_select":sel,
                        "pb1_high_fraction":round(high/255,4),
                        "pb1_transition_fraction":round(trans/255,4),
                        "approx_vox_max":vox*4})
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":4,"measurement":"af_bypass_and_gp0_tx_mic_probe",
                "capture":"PA-muted TX lease; physical PTT required",
                "stage_window_ms":200,"stage_settle_ms":40,"startup_discard_ms":120,
                "stage_count":completed,"vox_max":max_vox,"stages":rows,
                "note":"PB1 asynchronous bit sampling is a coarse activity probe, not a decoded audio signal"}
    if data[0]==3:
        isr_peak,vox,n=struct.unpack_from("<HHH",data,25)
        if n!=8000 or vox>1023 or not 100<=isr_peak<=12000:
            raise ValueError("MIC RAW sample/VOX/ISR totals invalid")
        bins=[]
        begin=0
        for i,end in enumerate(RAW_BIN_END_MS):
            ones,edges,variation,pcm=data[1+i*4:5+i*4]
            bins.append({"start_ms":begin,"end_ms":end,
                         "raw_one_bit_fraction":round(ones/255,4),
                         "raw_transition_fraction":round(edges/255,4),
                         "raw_density_variation":round(variation/255,4),
                         "mean_abs_pcm":decompress_pcm(pcm),
                         "encoded_raw":list(data[1+i*4:5+i*4])})
            begin=end
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":3,"measurement":"pa11_raw_bit_density_and_pcm",
                "sampling_rate_hz_nominal":8000,"raw_bits_per_tick":48,
                "quantization":"raw counts mapped 0..255 per 48 raw samples; PCM approximate logarithmic",
                "window_origin":"first valid PCM after three warm-up ISR ticks",
                "isr_peak_cycles":isr_peak,"isr_deadline_cycles":12000,
                "vox_max":vox,"samples":n,"windows":bins}
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
