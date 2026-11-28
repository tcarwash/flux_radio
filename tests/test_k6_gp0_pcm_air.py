"""Offline end-to-end PRE-SOTTO PCM air-transfer contract.

Host-compiled actual C diagnostic module runs against a deterministic bounded
PCM-ring/FSK simulator, and its *exact transmitted 66-byte buffers* are
unwhitened, CRC-checked and reconstructed into an identical signed16 waveform.
Also tests ARMv6-M build, radio safety guards, loss/replay/malformed data.
No radio/RTL/serial access or firmware flashing.
"""
from __future__ import annotations
import ctypes as C
import hashlib
from pathlib import Path
import subprocess
import sys
import wave
import numpy as np
import pytest

from tools.k6_gp0_pcm_air import (
    parse_physical,parse_framed_bytes,assemble,write_wav_new,PACKETS,
    N_SAMPLES,FS,WIRE_BYTES,dewhiten,
)

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    tmp=tmp_path_factory.mktemp("gp0-pcm-air")
    so=tmp/"libpcm_air.so"
    subprocess.run(["cc","-std=c99","-O2","-Wall","-Wextra","-fPIC","-shared",
        "-I"+str(ROOT/"c/k6"),"-I"+str(ROOT/"c/k6/mod"),
        str(ROOT/"c/k6/mod/sotto_gp0_pcm_air_tx.c"),
        str(ROOT/"tests/data/k6/sotto_gp0_pcm_air_host.c"),
        "-o",str(so)],check=True,capture_output=True,text=True)
    lib=C.CDLL(str(so))
    lib.gpraw_host_run.argtypes=[C.c_int]
    lib.gpraw_host_run.restype=C.c_int
    lib.gpraw_host_faults.restype=C.c_int
    lib.gpraw_host_packet_count.restype=C.c_uint
    lib.gpraw_host_elapsed_us.restype=C.c_uint32
    lib.gpraw_host_packet.argtypes=[C.c_uint]
    lib.gpraw_host_packet.restype=C.c_void_p
    return lib


def capture_frames(lib):
    return [C.string_at(lib.gpraw_host_packet(i),66) for i in
            range(lib.gpraw_host_packet_count())]


def test_real_c_capture_and_on_air_packet_marshal_full_pcm(host,tmp_path):
    assert host.gpraw_host_run(0)==PACKETS
    assert host.gpraw_host_faults()==0
    assert host.gpraw_host_packet_count()==PACKETS
    # 0.8s MICIN bias settle, 0.2s PCM + 9.7s FSK within 25s TX lease.
    assert 10_000_000<host.gpraw_host_elapsed_us()<24_000_000
    frames=capture_frames(host)
    assert all(len(p)==WIRE_BYTES and p[-2:]==b"\x5a\xa5" for p in frames)
    parsed=[parse_physical(f) for f in frames]
    assert all(p is not None for p in parsed)
    assert [p["seq"] for p in parsed]==list(range(PACKETS))
    metadata,pcm=assemble(parsed)
    assert metadata["sample_count"]==1600
    assert metadata["sample_rate_hz"]==FS
    assert 197<=metadata["capture_wall_duration_ms"]<=210
    assert metadata["unique_packets"]==PACKETS
    expected=np.array([(i*97%14001)-7000 for i in range(N_SAMPLES)],dtype='<i2')
    assert pcm==expected.tobytes()
    assert metadata["pcm_sha256"]==hashlib.sha256(pcm).hexdigest()
    assert abs(metadata["measured_pcm_production_rate_samples_per_wall_sec"]-8000)<180
    wav=tmp_path/"pre_sotto.wav"
    write_wav_new(wav,pcm)
    with wave.open(str(wav)) as f:
        assert f.getframerate()==8000 and f.getnchannels()==1
        assert f.getsampwidth()==2 and f.getnframes()==1600
        assert f.readframes(N_SAMPLES)==pcm
    with pytest.raises(FileExistsError):write_wav_new(wav,pcm)
    assert [p["seq"] for p in parse_framed_bytes(b"".join(frames))]==list(range(PACKETS))


@pytest.mark.parametrize("mode,expected",[
    (2,-13), # no DMA producer: bounded capture timeout / dekey
    (3,-11), # wrong bench frequency: no TX lease
    (4,-4),  # programming cable/PTT conflict: no TX lease
    (5,-6),  # bad microphone route: dekey
    (6,-21), # FSK callback failure: dekey and abort
    (7,-22), # Mic/DMA stop confirmation fails: never begin RF data
    (8,-17), # PTT released during slow packet drain: stop, dekey
])
def test_safe_failure_never_claims_complete_pcm(host,mode,expected):
    assert host.gpraw_host_run(mode)==expected
    assert host.gpraw_host_faults()==0
    assert host.gpraw_host_packet_count()<PACKETS


def test_strict_packet_crc_whitening_alignment_replay_and_missing_frames(host):
    assert host.gpraw_host_run(0)==PACKETS
    frames=capture_frames(host)
    one=bytearray(frames[0])
    one[12]^=1
    assert parse_physical(bytes(one)) is None
    assert parse_physical(b"\xff"*66) is None
    assert parse_physical(b"\x00"*64) is None
    assert parse_physical(frames[0][:64]) is not None
    assert parse_physical(b"\x00\xf8"+frames[0][:64]) is not None
    # Deliberately reordered packet lists produce identical waveforms.
    parsed=[parse_physical(f) for f in frames]
    m,a=assemble(parsed)
    n,b=assemble(list(reversed(parsed)))
    assert a==b and m["pcm_sha256"]==n["pcm_sha256"]
    with pytest.raises(ValueError,match="missing"):
        assemble(parsed[:-1])
    with pytest.raises(ValueError,match="66-byte aligned"):
        parse_framed_bytes(frames[0]+b"x")
    # Strict same-run identity: another session must not be stitched in.
    bad=dict(parsed[5],token=parsed[5]["token"]^1)
    with pytest.raises(ValueError,match="identity"):
        assemble(parsed[:5]+[bad]+parsed[6:])
    mutated=dict(parsed[7],payload=b"x"*len(parsed[7]["payload"]))
    with pytest.raises(ValueError,match="conflicting duplicate"):
        assemble(parsed+[mutated])


def test_real_phy_receiver_recovers_first_pcm_packet_from_synthetic_fsk(host):
    from modem import pager
    from tools.k6_fsk_sotto_sync_rx import find_frames
    assert host.gpraw_host_run(0)==PACKETS
    raw=capture_frames(host)[0]
    bits=np.unpackbits(np.frombuffer(bytes([0x55])*16+pager.SYNC+raw,dtype=np.uint8))
    fs=128000;baud=4800
    n=int(np.ceil(len(bits)*fs/baud))
    inds=np.minimum(np.arange(n)*baud//fs,len(bits)-1)
    freq=(bits[inds].astype(float)*2-1)*2400
    iq=np.exp(2j*np.pi*np.cumsum(freq/fs)).astype(np.complex64)
    iq=np.concatenate([np.zeros(fs//10,np.complex64),iq,
                       np.zeros(fs//10,np.complex64)])
    def adapter(packet):
        found=parse_physical(packet)
        if found is None:return None
        return dict(msg_id=found["token"]&255,seq=found["seq"],frames=[])
    r=find_frames(iq,fs=fs,payload_bytes=66,
                  rate_factors=(.998,1.,1.002),min_correlation=.75,
                  unpack_packet=adapter)
    assert r["crc_valid"]==1
    assert len(r["byte_payloads"])==1
    assert r["byte_payloads"][0]==raw
    assert parse_physical(r["byte_payloads"][0])["seq"]==0


def test_all_67_packets_cross_synthetic_fsk_iq_and_crc_to_exact_pcm(host):
    """No radio or SDR: real C module -> full 67 packet synthetic FSK ->
    same blind frame sync receiver used for live SDR -> independent D9 parser.
    """
    from modem import pager
    from tools.k6_fsk_sotto_sync_rx import find_frames
    assert host.gpraw_host_run(0)==PACKETS
    tx_frames=capture_frames(host)
    fs=128000;baud=4800
    iq_chunks=[np.zeros(fs//10,np.complex64)]
    sync=np.unpackbits(np.frombuffer(bytes([0x55])*16+pager.SYNC,dtype=np.uint8))
    for payload in tx_frames:
        bits=np.concatenate((sync,np.unpackbits(np.frombuffer(payload,dtype=np.uint8))))
        n=int(np.ceil(len(bits)*fs/baud))
        indices=np.minimum(np.arange(n)*baud//fs,len(bits)-1)
        freq=(bits[indices].astype(float)*2-1)*2400
        iq_chunks.append(np.exp(2j*np.pi*np.cumsum(freq/fs)).astype(np.complex64))
        iq_chunks.append(np.zeros(fs//50,np.complex64))
    iq_chunks.append(np.zeros(fs//10,np.complex64))
    iq=np.concatenate(iq_chunks)
    def adapter(raw):
        p=parse_physical(raw)
        return dict(msg_id=p["token"]&255,seq=p["seq"],frames=[]) if p else None
    r=find_frames(iq,fs=fs,payload_bytes=66,rate_factors=(.998,1.,1.002),
                  min_correlation=.75,unpack_packet=adapter,max_frame_candidates=120)
    assert r["crc_valid"]==PACKETS
    parsed=[parse_physical(raw) for raw in r["byte_payloads"]]
    meta,pcm=assemble(parsed)
    assert meta["unique_packets"]==PACKETS
    expected=np.array([(i*97%14001)-7000 for i in range(N_SAMPLES)],dtype='<i2')
    assert pcm==expected.tobytes()


def test_arm_pic_module_compiles_with_no_codec_dependency():
    from modem import k6mod
    code,entry=k6mod.build(str(ROOT/"c/k6/mod/sotto_gp0_pcm_air_tx.c"),
                           cflags=k6mod.CFLAGS)
    assert 1000<len(code)<5000
    # Existing runtime 14,976 arena, reserve final 1040 for callback stack.
    scratch=14976-1040-24-len(code)
    assert scratch>=644+1600*2+66
    assert entry==k6mod.bin_entry(code)
