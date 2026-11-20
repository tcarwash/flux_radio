"""Native Flux channel-bank codec and verified, backup-first cable programming."""
import hashlib,json,struct,datetime,tempfile
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from k6_memories import normalize,hz
from k6_user_store_restore import persist
from k6_port_lock import serial_lock
COUNT=1000; SIZE=32; BANK=32768; SECTOR=4096; EMPTY=b'\xff'*SIZE
CAP=b'K6MB\x01\x20\xe8\x03'
BACKUPS=Path.home()/'.local/share/flux/memory-backups'
DCS='023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 114 115 116 122 125 131 132 134 143 145 152 155 156 162 165 172 174 205 212 223 225 226 243 244 245 246 251 252 255 261 263 265 266 271 274 306 311 315 325 331 332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446 452 454 455 462 464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 645 654 662 664 703 712 723 731 732 734 743 754'.split()
CTCSS={670,693,719,744,770,797,825,854,885,915,948,974,1000,1035,1072,1109,1148,1188,1230,1273,1318,1365,1413,1462,1514,1567,1598,1622,1655,1679,1713,1738,1773,1799,1835,1862,1899,1928,1966,1995,2035,2065,2107,2181,2257,2291,2336,2418,2503,2541}
MODES={'FM':(0,0),'NFM':(0,64),'AM':(1,0),'NAM':(1,64),'USB':(2,0),'CW':(3,0)}
def bcd(f):
    if f%10 or not 0<=f<=999999990:raise ValueError('frequency cannot be represented in the native 10 Hz record')
    return bytes.fromhex(f'{f//10:08d}')[::-1]
def unbcd(b):
    if any(x>>4>9 or x&15>9 for x in b):raise ValueError('invalid native frequency')
    return int(b[::-1].hex())*10
def supported(f):return 16000000<=f<=560000000 or 740000000<=f<=999999990
def freq(f):return f'{f/1e6:.6f}'
def tone(r,kind,tx):
    if not kind:return 0
    if kind=='Tone':
        n=Decimal(r['rToneFreq' if tx else 'cToneFreq'])*10
        if n not in CTCSS:raise ValueError('unsupported CTCSS tone')
        return int(n)
    if kind=='DTCS':
        code=r['DtcsCode' if tx else 'RxDtcsCode'].zfill(3)
        if code not in DCS:raise ValueError('unsupported DCS code')
        pol=r['DtcsPolarity'];
        if pol not in ('NN','NR','RN','RR'):raise ValueError('invalid DCS polarity')
        return DCS.index(code)+1+(105 if pol[0 if tx else 1]=='R' else 0)
    raise ValueError('unsupported cross tone')
def encode(row):
    r=normalize(row);rx=hz(r['Frequency']);o=hz(r['Offset']);d=r['Duplex'];tx={'':rx,'+':rx+o,'-':rx-o,'split':o,'off':0}[d]
    if not supported(rx) or (tx and not supported(tx)):raise ValueError('frequency outside supported native range')
    if r['Mode'] not in MODES:raise ValueError('unsupported native modulation: '+r['Mode'])
    if tx and r['Mode'] in ('AM','NAM','USB'):raise ValueError('this modulation is receive-only on Flux; select Duplex off')
    name=r['Name'].encode('ascii');
    if len(name)>12 or any(x<32 or x>126 for x in name):raise ValueError('name must be at most 12 printable ASCII characters')
    if r['Skip'] not in ('','S'):raise ValueError('unsupported scan skip mode')
    mode,width=MODES[r['Mode']];t=r['Tone'];pair={'':('',''),'Tone':('Tone',''),'TSQL':('Tone','Tone'),'DTCS':('DTCS','DTCS')}.get(t)
    if t=='Cross':pair=tuple(r['CrossMode'].split('->'))
    if pair is None or len(pair)!=2:raise ValueError('invalid cross tone')
    if t=='TSQL':r['rToneFreq']=r['cToneFreq']
    if t=='DTCS':r['RxDtcsCode']=r['DtcsCode']
    tt,rt=tone(r,pair[0],True),tone(r,pair[1],False)
    power={'':2,'Low':2,'Mid':1,'High':0}.get(r['Power'])
    if power is None:raise ValueError('unsupported power setting (use Low, Mid or High)')
    return bcd(rx)+bcd(tx)+struct.pack('<HHBBBBI',rt,tt,31,0,power,width|(0 if r['Skip'] else 4),0xbf6d0000|mode)+name.ljust(12,b'\0')
def decode(record,slot):
    if record==EMPTY:return None
    rx,tx=unbcd(record[:4]),unbcd(record[4:8]);rt,tt,_,_,power,flags,mod=struct.unpack_from('<HHBBBBI',record,8)
    if not rx:raise ValueError('zero receive frequency')
    mode=next((m for m,v in MODES.items() if v==((mod&255) if mod&0xffffff00==0xbf6d0000 else 0,flags&64)),None)
    if mode is None:raise ValueError('unsupported native mode')
    r=normalize({'Location':str(slot),'Name':record[20:].split(b'\0')[0].split(b'\xff')[0].decode('ascii'),'Frequency':freq(rx),'Duplex':'off' if not tx else '+' if tx>rx else '-' if tx<rx else '', 'Offset':freq(abs(tx-rx)) if tx else '0','Mode':mode,'Skip':'' if flags&4 else 'S','Power':{0:'High',1:'Mid',2:'Low'}.get(power,'')})
    kinds=[];pol=[]
    for value,tk,dk in ((tt,'rToneFreq','DtcsCode'),(rt,'cToneFreq','RxDtcsCode')):
        if value==0:kinds.append('');pol.append('N')
        elif value in CTCSS:kinds.append('Tone');pol.append('N');r[tk]=str(Decimal(value)/10)
        elif 1<=value<=210:kinds.append('DTCS');pol.append('R' if value>105 else 'N');r[dk]=DCS[(value-1)%105]
        else:raise ValueError('unsupported native tone code')
    r['DtcsPolarity']=''.join(pol);a,b=kinds
    r['Tone']='' if not a and not b else 'Tone' if (a,b)==('Tone','') else 'TSQL' if a==b=='Tone' and tt==rt else 'DTCS' if a==b=='DTCS' and r['DtcsCode']==r['RxDtcsCode'] else 'Cross'
    r['CrossMode']=a+'->'+b
    return r
def inventory(bank):
    rows=[];opaque=[]
    for i in range(COUNT):
        try:r=decode(bank[i*SIZE:(i+1)*SIZE],i)
        except (ValueError,UnicodeError):opaque.append(i);continue
        if r is not None:rows.append(r)
    return rows,opaque
def plan(bank,rows,mode):
    if len(bank)!=BANK or mode not in ('merge','replace'):raise ValueError('invalid bank or mode')
    if not isinstance(rows,list) or len(rows)>COUNT or not all(isinstance(r,dict) for r in rows):raise ValueError('maximum 1000 memories')
    if mode=='replace' and not rows:raise ValueError('empty replacement not permitted')
    new=bytearray(bank if mode=='merge' else EMPTY*COUNT+bank[COUNT*SIZE:]);occupied=set();keys=set();added=0;skipped=0
    def key(b):return b[:12]+bytes([b[15]&64])+b[16:20]
    for i in range(COUNT):
        b=bytes(new[i*SIZE:(i+1)*SIZE])
        if b!=EMPTY:occupied.add(i);keys.add(key(b))
    for i,r in enumerate(rows):
        try:b=encode(r)
        except (ValueError,ArithmeticError,UnicodeError,TypeError,KeyError) as e:raise ValueError(f'Channel {i+1}: {e}') from e
        if key(b) in keys:skipped+=1;continue
        loc=r.get('Location','')
        target=int(loc) if mode=='replace' and str(loc).isdigit() and 0<=int(loc)<COUNT and int(loc) not in occupied else next((j for j in range(COUNT) if j not in occupied),None)
        if target is None:raise ValueError('memory bank full')
        new[target*SIZE:(target+1)*SIZE]=b;occupied.add(target);keys.add(key(b));added+=1
    return bytes(new),{'added':added,'duplicates':skipped,'occupied':len(occupied)}
def read_bank(c):
    if c.call(0x90)!=CAP:raise ValueError('firmware does not support Flux memory-bank protocol v1')
    # Capability v1 defines the bank at SPI offset zero. Bulk reads are read-only;
    # retain the explicit opcode for test/fixed clients without a bulk reader.
    if callable(getattr(c,'read',None)):
        bank=c.read(0,BANK)
        if len(bank)!=BANK:raise ValueError('short memory-bank read')
        return bank
    parts=[c.call(0x91,i) for i in range(BANK//64)]
    if any(len(p)!=64 for p in parts):raise ValueError('malformed memory-bank read frame')
    bank=b''.join(parts)
    if len(bank)!=BANK:raise ValueError('short memory-bank read')
    return bank
def write_bank(c,old,new,directory=BACKUPS,*,identity=None,recovery=None):
    if len(old)!=BANK or len(new)!=BANK or old[COUNT*SIZE:]!=new[COUNT*SIZE:]:raise ValueError('invalid bank or changed reserved tail')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if recovery is None:
        path=Path(tempfile.mkdtemp(prefix='memory-',dir=directory));persist(path/'original.bin',old);persist(path/'target.bin',new)
        record={'status':'pending','identity':identity,'original_sha256':hashlib.sha256(old).hexdigest(),'target_sha256':hashlib.sha256(new).hexdigest()};persist(path/'journal.json',json.dumps(record).encode())
    else:
        path=Path(recovery);record=json.loads((path/'journal.json').read_text())
        if record.get('status')!='pending' or record.get('identity')!=identity or hashlib.sha256((path/'original.bin').read_bytes()).hexdigest()!=record['original_sha256'] or hashlib.sha256(new).hexdigest()!=record['target_sha256']:raise ValueError('recovery identity or checksum mismatch')
    def cmd(op,addr,data=b''):
        if c.call(op,addr,data)!=b'\0'*4:raise ValueError(f'memory transaction refused: {op:#x} {addr}; backup {path}')
    for i in range(8):
        start=i*SECTOR;b=new[start:start+SECTOR]
        if b==old[start:start+SECTOR]:continue
        cmd(0x92,i)
        for j in range(64):cmd(0x93,j,b[j*64:(j+1)*64])
        cmd(0x94,i)
    if read_bank(c)!=new:raise ValueError(f'memory verification failed; backup {path}')
    record['status']='complete';persist(path/'journal.json',json.dumps(record).encode());return str(path)
def operate(port,key_file,action,rows=None):
    import k6_install as radio
    with serial_lock(port):
        connection=radio.open_port(SimpleNamespace(port=port));c=None
        try:
            c=radio.open_cable(connection,lambda *a,**k:None,wait=3.0)
            if c is None:raise ValueError('no Flux cable session')
            caps=c.capabilities()
            if not caps.get('cable_only'):c.authenticated_capabilities(radio.k6mod.read_key(key_file or radio.find_key()))
            old=read_bank(c)
            identity={'port':str(port),'calibration':hashlib.sha256(c.read(0xf000,4096)).hexdigest()}
            pending=[]
            for p in BACKUPS.glob('memory-*/journal.json'):
                record=json.loads(p.read_text())
                if record.get('status')=='pending' and record.get('identity')==identity:pending.append(p.parent)
            if action=='recover':
                if len(pending)!=1:raise ValueError('expected exactly one pending memory backup for this radio')
                path=pending[0];target=(path/'target.bin').read_bytes()
                return {'backup':write_bank(c,old,target,identity=identity,recovery=path),'occupied':len(inventory(target)[0]),'added':0,'duplicates':0}
            if action!='read' and pending:raise ValueError('pending memory write: choose Resume interrupted write before starting a new operation')
            if action=='read':
                data,opaque=inventory(old);return {'channels':data,'opaque_slots':opaque,'pending':bool(pending)}
            new,report=plan(old,rows,action);backup=write_bank(c,old,new,identity=identity);return dict(report,backup=backup)
        finally:
            try:
                if c is not None:c.end()
            finally:connection.close()
