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
    if len(data)!=33 or data[0] not in (1,2,3,4,5,6,7,8,9):
        raise ValueError("MIC mailbox format/version invalid")
    expected=struct.unpack_from("<H",data,31)[0]
    if crc16_xmodem(data[:31])!=expected:
        raise ValueError("MIC A/B mailbox checksum mismatch")
    if data[0]==9:
        error=data[1]
        if error not in (5,6,9,11,12,17,18,19,20,21,22,23,24,25):
            raise ValueError("MIC PCM DMA failure code invalid")
        reason={5:"PA-muted TX lease refused",6:"RFIC mic route-only service missing or refused",
                9:"physical PTT released or interrupted",11:"DMA ring overrun",
                12:"DMA capture time limit",17:"GP0 output enable refused",
                18:"GP0 selector 12 write/readback refused",
                19:"TIM17 already owned",20:"DMA1 channel1 already owned",
                21:"DMA sample rate outside expected hardware bounds",
                22:"GP0 selector restoration failed",23:"GP0 output restoration failed",
                24:"bad GP0 PCM bit-count or sample rate",25:"incomplete GP0 PCM sample windows"}[error]
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":9,"measurement":"gp0_dma_mic_8khz_pcm",
                "completed":False,"error_code":error,"reason":reason}
    if data[0]==8:
        vox,fs10,total=struct.unpack_from("<HHH",data,1)
        if vox>1023 or not 3500<=fs10<=13000 or total!=1824:
            raise ValueError("MIC PCM DMA VOX/rate/sample count invalid")
        windows=[]
        for i in range(4):
            a1,a2,rms8,clips=struct.unpack_from("<HHBB",data,7+i*6)
            windows.append({"index":i,"pcm_samples":256,"duration_ms":32,
                            "pcm_tone_1khz_peak":a1,"pcm_tone_2khz_peak":a2,
                            "pcm_rms_approx":rms8*64,"pcm_clipped_samples":clips})
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":8,"measurement":"gp0_dma_mic_8khz_pcm",
                "capture":"PA-muted TX, RFIC mic route only; GP0 PB1 DMA to 8kHz signed PCM",
                "gp0_selector":12,"dma_decimated_samples_per_second":fs10*10,
                "pcm_samples_total":total,"pcm_warmup_samples_discarded":800,
                "vox_max":vox,"windows":windows,
                "note":"Window spectra are from real 8kHz reconstructed PCM, not proof of intelligible speech or live codec scheduling"}
    if data[0]==7:
        error,completed=data[1],data[2]
        if error not in (5,6,9,11,12,17,18,19,20,21,22,23) or completed>4:
            raise ValueError("MIC GP0 DMA failure code/windows invalid")
        reason={5:"PA-muted TX lease refused",6:"RFIC mic route-only service missing or refused",
                9:"physical PTT released or interrupted",11:"DMA ring overrun",
                12:"DMA capture time limit",17:"GP0 output enable refused",
                18:"GP0 selector 12 write/readback refused",
                19:"TIM17 already owned",20:"DMA1 channel1 already owned",
                21:"DMA sample rate outside expected hardware bounds",
                22:"GP0 selector restoration failed",23:"GP0 output restoration failed"}[error]
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":7,"measurement":"gp0_dma_tx_mic_spectrum",
                "completed":False,"error_code":error,"reason":reason,
                "completed_windows":completed,"expected_windows":4}
    if data[0]==6:
        vox,fs10,rawks=struct.unpack_from("<HHH",data,1)
        if vox>1023 or not 3500<=fs10<=13000 or not 2200<=rawks<=8500:
            raise ValueError("MIC GP0 DMA VOX/sample-rate out of bounds")
        windows=[]
        for i in range(4):
            a1,a2,rms,high,diff,voxq=data[7+i*6:13+i*6]
            windows.append({"index":i,"nominal_duration_ms":round(4096/(fs10*10)*1000,2),
                            "amplitude_1khz_q":a1,"amplitude_2khz_q":a2,
                            "rms_raw_density":rms,
                            "gp0_raw_high_fraction":round(high/255,4),
                            "mean_density_change":diff,
                            "approx_vox":voxq*4})
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":6,"measurement":"gp0_dma_tx_mic_spectrum",
                "capture":"PA-muted TX; RFIC mic ADC enabled; no PA11 sampler",
                "gp0_selector":12,"raw_dma_ksamples_per_second":rawks,
                "decimated_samples_per_second":fs10*10,
                "vox_max":vox,"windows":windows,
                "scale":"amplitude bins are relative 8-bit normalized DFT scores, not calibrated PCM",
                "note":"1k/2k generator contrast required; detected digital activity alone is not recovered voice"}
    if data[0]==5:
        error,completed=data[1],data[2]
        if error not in (4,5,6,9,10,11,17,18,19,20,21,22,23,24) or completed>9:
            raise ValueError("MIC ROUTES failure code/stage count invalid")
        reason={4:"physical PTT unavailable or UART attached",
                5:"PA-muted TX lease refused",6:"microphone stream start failed",
                9:"PTT released or capture interrupted",10:"PCM block timeout",
                11:"PCM ring overrun",17:"AF output select write/readback refused",
                18:"AF4 restoration refused before GP0",
                19:"GP0 output enable refused",20:"GP0 select write/readback refused",
                21:"raw PA11 counter reset unexpectedly",22:"capture time limit exceeded",
                23:"GP0 select restoration failed",24:"GP0 output restoration failed"}[error]
        return {"available":True,"storage":"boot-local RAM","survives_reboot":False,
                "version":5,"measurement":"af_bypass_and_gp0_tx_mic_probe",
                "completed":False,"error_code":error,"reason":reason,
                "completed_stages":completed,"expected_stages":9}
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
