"""Native direct-FSK RX model tests: raw RF bits, no packet/CRC oracle."""
import numpy as np
import pytest
from sim.k6emu.rf.backend import create_backend
from sim.k6emu.rf.fsk import SYNC, FULL, DONE
from test_k6_rf_backend import tiny_image, regs_for
from sim.k6emu.__main__ import K6Board

HZ = 440850000
RATE = 240000
MARK = b"\x55"*16 + bytes.fromhex("076b3e36")
DATA = np.random.default_rng(123).integers(0, 256, 64, dtype=np.uint8).tobytes()


def iq_frame(data=DATA, marker=MARK, rate=RATE, polarity=1, lead=.0237, snr=25):
    bits = np.unpackbits(np.frombuffer(marker+data, dtype=np.uint8))
    n = int(rate*(lead+len(bits)/4800+.025))
    t = np.arange(n)/rate-lead
    idx = np.floor(t*4800).astype(int)
    active = (idx>=0)&(idx<len(bits))
    freq = (bits[np.clip(idx,0,len(bits)-1)].astype(float)*2-1)*1800*polarity + 230
    signal = .15*np.exp(2j*np.pi*np.cumsum(freq)/rate)*active
    rng = np.random.default_rng(42)
    noise = .15*10**(-snr/20)/np.sqrt(2)*(rng.normal(size=n)+1j*rng.normal(size=n))
    return (signal+noise).astype(np.complex64)


def setup(b, regs, threshold=4, n=64, baud=0xc194, seconds=0):
    for a,v in [(0x58,0xe1),(0x72,baud),(0x5a,0x076b),(0x5b,0x3e36),(0x5c,0x5625),
                (0x5d,(n-1)<<8),(0x5e,0x200|threshold),(0x59,0x40f8),(0x59,0x10f8),
                (0x02,0),(0x3f,SYNC|FULL|DONE)]:
        b.write(a,v,regs,seconds)
        regs[a]=v


def poll(b, regs, end=.22, interval=.001, clear_first=True, drain=True):
    data = bytearray()
    events = []
    for t in np.arange(interval,end,interval):
        if b.read(0x0c,regs,t)&1:
            if clear_first:
                b.write(0x02,0,regs,t)
            flags = b.read(0x02,regs,t)
            if not clear_first:
                b.write(0x02,0,regs,t)
            events.append((t,flags))
            if drain:
                if flags&FULL:
                    for _ in range(regs[0x5e]&7):
                        data.extend(b.read(0x5f,regs,t).to_bytes(2,"little"))
                if flags&DONE:
                    while len(data)<((regs[0x5d]>>8)+1):
                        data.extend(b.read(0x5f,regs,t).to_bytes(2,"little"))
    return bytes(data),events


@pytest.mark.parametrize("rate,polarity,interval,clear_first",[
    (240000,1,.001,True),(240000,-1,.0017,False),(1024000,1,.0023,True),
    (128000,-1,.0013,True)])
def test_raw_frame_fifo(tmp_path,rate,polarity,interval,clear_first):
    p=tmp_path/"rf.npy"
    np.save(p,iq_frame(rate=rate,polarity=polarity))
    b=create_backend("npy:"+str(p),rate=rate,center_hz=HZ)
    try:
        regs=regs_for(HZ)
        setup(b,regs)
        data,events=poll(b,regs,interval=interval,clear_first=clear_first)
        assert data==DATA
        assert events[0][1]&SYNC and not events[0][1]&FULL
        assert events[-1][1]&DONE
        assert .05<events[0][0]<.065
        assert .155<events[-1][0]<.17
        assert b.chip.fsk.overruns==b.chip.fsk.underflows==0
    finally:
        b.close()


def test_overrun_and_empty(tmp_path):
    p=tmp_path/"rf.npy";np.save(p,iq_frame())
    b=create_backend("npy:"+str(p),rate=RATE,center_hz=HZ)
    try:
        regs=regs_for(HZ);setup(b,regs)
        _,events=poll(b,regs,drain=False)
        assert events[-1][1]&DONE
        assert b.chip.fsk.overruns==24
        assert b.chip.fsk.status()["fifo_words"]==8
        assert b"".join(b.read(0x5f,regs,.22).to_bytes(2,"little") for _ in range(8))==DATA[:16]
        assert b.read(0x5f,regs,.22)==0 and b.chip.fsk.underflows==1
        b.write(0x59,0x40f8,regs,.22)
        assert not b.chip.fsk.pending and not b.chip.fsk.armed
    finally:
        b.close()


@pytest.mark.parametrize("marker,baud,tune",[
    (b"\x55"*16+b"\x00\x00\x00\x00",0xc194,HZ),
    (MARK,0x60ca,HZ),(MARK,0xc194,HZ+1000000)])
def test_no_false_frame(tmp_path,marker,baud,tune):
    p=tmp_path/"rf.npy";np.save(p,iq_frame(marker=marker))
    b=create_backend("npy:"+str(p),rate=RATE,center_hz=HZ)
    try:
        regs=regs_for(tune);setup(b,regs,baud=baud)
        data,events=poll(b,regs)
        assert not data and not events and b.chip.fsk.frames==0
        assert bool(b.chip.fsk.unsupported)==(baud!=0xc194)
    finally:
        b.close()


def test_u8_archive(tmp_path):
    p=tmp_path/"rf.npy"
    np.save(p,np.array([0,255,128,127],dtype=np.uint8))
    b=create_backend("npy-u8:"+str(p),rate=RATE,center_hz=HZ)
    try:
        np.testing.assert_allclose(b.source.read(3),[(-127.5+127.5j)/128,(.5-.5j)/128,0])
    finally:
        b.close()


def test_real_bus_pop_once(tmp_path):
    p=tmp_path/"rf.npy";np.save(p,iq_frame())
    b=create_backend("npy:"+str(p),rate=RATE,center_hz=HZ)
    board=K6Board(tiny_image(tmp_path),rf_backend=b)
    def bus_write(a,v):
        board._fd_pin_change(8,0)
        for i in range(23,-1,-1):
            bit=(((a<<16|v)>>i)&1)<<6
            board._fd_pin_change(bit,bit|32)
        board._fd_pin_change(0,8)
    def bus_read(a):
        board._fd_pin_change(8,0)
        for i in range(7,-1,-1):
            bit=(((a|0x80)>>i)&1)<<6
            board._fd_pin_change(bit,bit|32)
        v=0
        for _ in range(16):
            board._fd_pin_change(0,32)
            v=v<<1|board.fd_read_bit
            board._fd_read_sampled()
        board._fd_pin_change(0,8)
        return v
    try:
        for a,v in [*regs_for(HZ).items(),(0x58,0xe1),(0x72,0xc194),(0x5a,0x076b),
                    (0x5b,0x3e36),(0x5c,0x5625),(0x5d,0x3f00),(0x5e,0x204),
                    (0x59,0x40f8),(0x59,0x10f8),(0x3f,SYNC|FULL|DONE)]:
            bus_write(a,v)
        board.cycles=int(.072*96e6)
        assert bus_read(0x0c)&1
        bus_write(0x02,0)
        assert bus_read(0x02)&FULL
        assert bus_read(0x5f)==int.from_bytes(DATA[:2],"little")
        assert bus_read(0x5f)==int.from_bytes(DATA[2:4],"little")
        assert b.chip.fsk.status()["fifo_words"]==2
    finally:
        board.close_rf()


def test_compiled_module_reads_rf_fifo(tmp_path):
    import shutil
    if not all(shutil.which(x) for x in ("clang","ld.lld","llvm-objcopy","llvm-nm")):
        pytest.skip("Cortex-M0 build tools unavailable")
    from tools.k6_fsk_emulator_replay import replay
    p=tmp_path/"rf.npy";np.save(p,iq_frame())
    frames,stats=replay("npy:"+str(p),rate=RATE,center=HZ,tune=HZ,duration=.25)
    assert frames==[DATA]
    assert stats["module_return"]==1
    assert stats["fsk"]["overruns"]==stats["fsk"]["underflows"]==0


def test_irq_mask_and_rearm(tmp_path):
    p=tmp_path/"rf.npy";np.save(p,iq_frame())
    b=create_backend("npy:"+str(p),rate=RATE,center_hz=HZ)
    try:
        regs=regs_for(HZ);setup(b,regs)
        regs[0x3f]=0
        assert not b.read(0x0c,regs,.06)&1
        regs[0x3f]=SYNC
        assert b.read(0x0c,regs,.06)&1
        b.write(0x02,0,regs,.06)
        assert b.read(0x02,regs,.06)&SYNC
        assert not b.read(0x0c,regs,.06)&1
        b.write(0x59,0x40f8,regs,.06)
        assert not b.chip.fsk.armed and not b.chip.fsk.fifo
        assert b.read(0x5f,regs,.06) is None
    finally:
        b.close()


def test_retune_drops_partial_frame(tmp_path):
    p=tmp_path/"rf.npy";np.save(p,iq_frame())
    b=create_backend("npy:"+str(p),rate=RATE,center_hz=HZ)
    try:
        regs=regs_for(HZ);setup(b,regs)
        assert b.read(0x0c,regs,.072)&1
        assert b.chip.fsk.status()["fifo_words"]==4
        for a,v in regs_for(HZ+1000000).items():
            b.write(a,v,regs,.072);regs[a]=v
        assert not b.read(0x0c,regs,.08)&1
        assert not b.chip.fsk.fifo and not b.chip.fsk.locked
        assert b.chip.fsk.gaps>=1
    finally:
        b.close()
