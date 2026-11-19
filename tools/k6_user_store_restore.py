"""Host-driven, backup-required restoration of a compact user bank."""
from pathlib import Path
import os,tempfile,hashlib,json
from k6_user_store_migrate import BASE,END,upgrade,locations

BACKUP_DIR=Path.home()/'.local/share/flux/backups'

def backup(cable, directory, log=print):
    raw=cable.read(BASE,END-BASE)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    fd,path=tempfile.mkstemp(prefix='flux-library-',suffix='.spi',dir=directory)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    log(f'  existing library saved to {path}')
    bank,manifest=upgrade(raw,preserve_opaque=True)
    log(f'  {len(manifest)} objects compacted; {126-sum(x[2] for x in manifest)} free sectors')
    return bank,path

def restore(cable,build,bank,log=print,*,key=None):
    from k6_install import InstallError
    locations(bank)
    caps=cable.capabilities()
    if not caps.get('cable_only'):
        if key is None:raise InstallError('key required for OTA firmware restoration')
        caps=cable.authenticated_capabilities(key)
    if cable.info()['protocol']<5 or caps.get('build_id')!=build.hex():raise InstallError('packed restore requires matching firmware')
    def cmd(op,addr=0,data=b''):
        r=cable.call(op,addr,data)
        if r!=bytes(4):raise InstallError(f'user restore command {op:#x} block {addr} failed: {r.hex()}')
    cmd(0x86,data=build)
    for sector in range(128):cmd(0x87,sector)
    staged=bytearray(bank);staged[:4]=staged[4096:4100]=b'\xff'*4
    for block in (*range(128,8192),*range(128)):
        chunk=staged[block*64:block*64+64]
        if chunk!=b'\xff'*64:cmd(0x88,block,chunk)
    if cable.read(BASE,END-BASE)!=staged:raise InstallError('user restore readback differs; directory not activated')
    cmd(0x89)
    if cable.read(BASE,END-BASE)!=bank:raise InstallError('published user library readback differs')
    log('  compact user library restored and verified')


def persist(path,data):
    """Atomic, durable replacement; no partially written recovery manifest."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.flux-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        if os.name!='nt':
            d=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(d)
            finally:os.close(d)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def prepare(cable,directory,build,port,log=print):
    bank,original=backup(cable,directory,log)
    original=Path(original);packed=original.with_suffix('.kul2');journal=original.with_suffix('.json')
    persist(packed,bank)
    # Fingerprint calibration independently of firmware identity, port and library contents.
    fingerprint=hashlib.sha256(cable.read(0xf000,4096)).hexdigest()
    record={'version':1,'build':build,'port':str(port),'original':original.name,'packed':packed.name,'packed_sha256':hashlib.sha256(bank).hexdigest(),'original_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'calibration_sha256':fingerprint,'status':'pending'}
    persist(journal,json.dumps(record,indent=2).encode());log(f'  recovery journal: {journal}')
    return bank,journal


def load_recovery(journal,cable,build,port):
    journal=Path(journal);r=json.loads(journal.read_text())
    if r['version']!=1 or r['status']!='pending' or r['build']!=build or r['port']!=str(port):raise ValueError('recovery journal does not match pending upgrade')
    if hashlib.sha256(cable.read(0xf000,4096)).hexdigest()!=r['calibration_sha256']:raise ValueError('recovery radio calibration does not match')
    if Path(r['packed']).name!=r['packed'] or Path(r['original']).name!=r['original']:raise ValueError('invalid recovery file names')
    raw=(journal.parent/r['original']).read_bytes();bank=(journal.parent/r['packed']).read_bytes()
    if hashlib.sha256(bank).hexdigest()!=r['packed_sha256'] or hashlib.sha256(raw).hexdigest()!=r['original_sha256']:raise ValueError('recovery backup checksum mismatch')
    locations(bank);return bank


def complete(journal):
    journal=Path(journal);r=json.loads(journal.read_text());r['status']='complete';persist(journal,json.dumps(r,indent=2).encode())


def pending_for_pack(directory,build,port):
    result=[]
    for p in sorted(Path(directory).glob('flux-library-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:r=json.loads(p.read_text())
        except (OSError,ValueError):continue
        if r.get('version')==1 and r.get('status')=='pending' and r.get('build')==build and r.get('port')==str(port):
            result.append(p)
    return result
