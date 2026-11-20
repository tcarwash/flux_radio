"""Packed, build-bound system bank provisioning. CRC is integrity, not authentication."""
import hashlib
import struct
from modem import k6mod

BASE, END, SECTOR = 0x19C000, 0x200000, 4096
PAYLOAD=BASE+SECTOR
BEGIN, ERASE, STAGE, DATA, COMMIT, FINISH = range(0x80,0x86)


def pack(firmware, images, arena):
    from k6_build_identity import image_build_id
    build=bytes.fromhex(image_build_id(firmware));pages={}
    if isinstance(images,(bytes,bytearray)):images=[images]
    for image in images:
        image=bytes(image)
        if not 46<=len(image)<=min(arena,8192):raise ValueError('system page exceeds arena')
        if image[:3]!=b'K6S' or image[13:16]!=b'\x03\0\0' or int.from_bytes(image[4:6],'little')+24!=len(image):raise ValueError('invalid system envelope')
        if image[-16:-8]!=build or image[-8:]!=k6mod.image_tag(None,image[:-8]):raise ValueError('system page CRC or build mismatch')
        magic,id,functions=struct.unpack_from('<HBB',image,16)
        if magic!=0x504b or not id or not functions or id in pages or len(image)<42+4*functions:raise ValueError('invalid or duplicate page ID')
        pages[id]=image
    pos=PAYLOAD;bank=bytearray(b'\xff'*(END-BASE));bank[:12]=b'KSD1'+build
    for id,image in sorted(pages.items()):
        if pos+len(image)>END:raise ValueError('system bank full')
        struct.pack_into('<II',bank,16+8*id,pos,len(image));bank[pos-BASE:pos-BASE+len(image)]=image;pos+=(len(image)+3)&~3
    return build,sorted(pages.items()),bytes(bank)


def install(cable,firmware,images,*,log=print):
    from k6_install import InstallError
    caps=cable.capabilities();build,pages,bank=pack(firmware,images,cable.info()['arena'])
    if not caps.get('cable_only') or caps.get('build_id')!=build.hex():raise InstallError('system bank requires matching cable-only firmware')
    def command(cmd,addr=0,data=b'',expected=0):
        reply=cable.call(cmd,addr,data)
        if len(reply)!=4 or struct.unpack('<i',reply)[0]!=expected:raise InstallError(f'system bank command {cmd:#x} at {addr} failed: {reply.hex()}')
    command(BEGIN,data=build)
    for sector in range((END-BASE)//SECTOR):command(ERASE,sector)
    log('  system bank erased and verified')
    for id,image in pages:
        command(STAGE,id,struct.pack('<H',len(image)))
        for off in range(0,len(image),64):
            chunk=image[off:off+64];command(DATA,off,chunk,off+len(chunk))
        command(COMMIT,id,expected=len(image))
        log(f'  system page {id} verified ({len(image)} B, SHA256 {hashlib.sha256(image).hexdigest()})')
    command(FINISH);log('  packed directory activated');cable.end()
