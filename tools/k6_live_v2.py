"""Bounded, binary-safe Flux live-v2 request encoding.

The six-byte prefix is followed by 7-bit symbols with bit 7 set, then NUL.
Neither a payload nor its CRC can contain an unescaped v1/legacy handshake,
even if the target times out halfway through a frame. Replies use CPS.
"""
import struct
from k6_install import crc16_xmodem

MAGIC_V2 = b"FLXLV2"
MAX_PAYLOAD = 128
ECHO = 0x70
RAM_BEGIN, RAM_CHUNK, RAM_COMMIT, RAM_ABORT = 0x71, 0x72, 0x73, 0x74
UPLOAD_PROBE, RAM_FAST_BEGIN, RAM_READY = 0x76, 0x77, 0x78
RAM_MAX = 4096
LIB_MAX = 10240
LIB_LEGACY_MAX = 7168
MODULE_CAPS = 0x7c
RUN_COOP, RUN_CANCEL = 0x7e, 0x7f
PTT_LEASE, PTT_RELEASE = 0x80, 0x81
PTT_LEASE_MS = 800
UPLOAD_RATE = 192000
LIB_BEGIN, LIB_FAST_BEGIN, LIB_COMMIT = 0x79, 0x7a, 0x7b

def pack_symbols(raw):
    acc = bits = 0
    out = bytearray()
    for byte in raw:
        acc |= byte << bits
        bits += 8
        while bits >= 7:
            out.append(0x80 | (acc & 127))
            acc >>= 7
            bits -= 7
    if bits:
        out.append(0x80 | acc)
    return bytes(out)

def unpack_symbols(data):
    if len(data) > ((MAX_PAYLOAD+10)*8+6)//7:
        raise ValueError("oversize symbol body")
    acc = bits = 0
    out = bytearray()
    for symbol in data:
        if not symbol & 0x80:
            raise ValueError("invalid symbol")
        acc |= (symbol & 127) << bits
        bits += 7
        if bits >= 8:
            out.append(acc & 255)
            acc >>= 8
            bits -= 8
    if acc or pack_symbols(out) != bytes(data):
        raise ValueError("noncanonical padding")
    return bytes(out)

def request_bytes_v2(cmd, seq, arg=0, payload=b""):
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("live v2 payload limited to 128 bytes")
    body = struct.pack("<BBHI", cmd, seq, len(payload), arg)+payload
    raw = body+struct.pack(">H",crc16_xmodem(MAGIC_V2+body))
    return MAGIC_V2+pack_symbols(raw)+b"\0"

def decode_request(wire):
    if not wire.startswith(MAGIC_V2) or not wire.endswith(b"\0"):
        raise ValueError("missing v2 framing")
    raw = unpack_symbols(wire[len(MAGIC_V2):-1])
    if len(raw)<10:
        raise ValueError("short v2 request")
    cmd,seq,n,arg=struct.unpack_from("<BBHI",raw)
    if n>MAX_PAYLOAD or len(raw)!=10+n:
        raise ValueError("invalid v2 length")
    if crc16_xmodem(MAGIC_V2+raw[:-2])!=int.from_bytes(raw[-2:],"big"):
        raise ValueError("v2 CRC mismatch")
    return cmd,seq,arg,raw[8:-2]
