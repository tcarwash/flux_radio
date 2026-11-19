"""Experimental, build-bound system bank provisioning over an exclusive CPS session.

Only the demo slot is registered today. This is not cryptographic authentication.
A new session always erases the whole bank; never resume a partial transaction.
"""
import hashlib
import struct
from pathlib import Path
from modem import k6mod

BASE, END, SECTOR, SLOT = 0x19C000, 0x200000, 4096, 0x4000
BEGIN, ERASE, STAGE, DATA, COMMIT = range(0x80, 0x85)


def prepare(firmware, image, arena):
    from k6_build_identity import image_build_id
    build = bytes.fromhex(image_build_id(firmware))
    image = bytes(image)
    if not 46 <= len(image) <= min(arena, 8192):
        raise ValueError('system page exceeds arena or slot')
    if image[:3] != b'K6S' or image[13:16] != b'\x03\0\0':
        raise ValueError('invalid system page envelope')
    if int.from_bytes(image[4:6], 'little') + 24 != len(image):
        raise ValueError('system page length mismatch')
    if image[-16:-8] != build or image[-8:] != k6mod.image_tag(None,image[:-8]):
        raise ValueError('system page CRC or build ID mismatch')
    if image[16:20] not in (b'KP\x11\x01', b'KP\x12\x02'):
        raise ValueError('unregistered system page')
    return build, image


def install(cable, firmware, image, *, log=print):
    from k6_install import InstallError
    caps = cable.capabilities()
    build, image = prepare(firmware, image, cable.info()['arena'])
    if not caps.get('cable_only') or caps.get('build_id') != build.hex():
        raise InstallError('system bank requires matching cable-only firmware')
    def command(cmd, addr=0, data=b'', expected=0):
        reply = cable.call(cmd, addr, data)
        if len(reply)!=4 or struct.unpack('<i',reply)[0]!=expected:
            raise InstallError(f'system bank command {cmd:#x} at {addr} failed: {reply.hex()}')
    command(BEGIN,data=build)
    for sector in range((END-BASE)//SECTOR):
        command(ERASE,sector)
    log('  system bank erased and verified')
    command(STAGE,data=struct.pack('<H',len(image)))
    for off in range(0,len(image),64):
        chunk=image[off:off+64]
        command(DATA,off,chunk,off+len(chunk))
    command(COMMIT,expected=len(image))
    log(f'  system page verified ({len(image)} B, SHA256 {hashlib.sha256(image).hexdigest()})')
    cable.end()  # exclusive mode remains active until reset
