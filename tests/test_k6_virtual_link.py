"""Virtual-only TX transactions, IQ generation, compiled TX/RX and fault injection."""
import shutil
import numpy as np
import pytest
from sim.k6emu.rf.virtual_link import Channel, VirtualAir, AirSource, VirtualTxBackend, TX_DONE
from tools.k6_fsk_virtual_link import trial
from test_k6_rf_backend import regs_for

HZ=433500000


def configure(b, n=64):
    regs=regs_for(HZ)
    def write(a,v,t=0):
        b.write(a,v,regs,t)
        regs[a]=v
    for a,v in [(0x3f,0x8000),(0x58,0xe1),(0x72,0xc194),(0x5a,0x076b),(0x5b,0x3e36),
                (0x5c,0x5625),(0x5d,(n-1)<<8),(0x59,0x80f8),(0x59,0xf8)]:
        write(a,v)
    return regs,write


def test_preload_done_and_irq():
    air=VirtualAir()
    b=VirtualTxBackend(air,240000,HZ)
    try:
        regs,w=configure(b)
        for i in range(32): w(0x5f,i)
        w(0x59,0x8f8,.01)
        assert len(air.bursts)==1
        assert not b.read(0xc,regs,.149)&1
        assert b.read(0xc,regs,.151)&1
        assert b.read(2,regs,.151)==TX_DONE
        w(2,0,.151)
        assert not b.read(0xc,regs,.151)&1
        assert b.read(2,regs,.151)==TX_DONE
        assert b.completed==1 and not b.active
        assert air.bursts[0].end==pytest.approx(.15)
        raw=np.packbits((air.bursts[0].bits>0).astype(np.uint8)).tobytes()
        assert raw[:20]==b"\x55"*16+bytes.fromhex("076b3e36")
        assert raw[20:]==b"".join(i.to_bytes(2,"little") for i in range(32))
    finally: b.close()


def test_short_fifo_and_abort():
    air=VirtualAir()
    b=VirtualTxBackend(air,240000,HZ)
    try:
        regs,w=configure(b)
        w(0x5f,1);w(0x59,0x8f8,.01)
        assert b.rejected==1 and not air.bursts
        w(0x59,0x80f8,.02);w(0x59,0xf8,.02)
        for i in range(32): w(0x5f,i,.02)
        w(0x59,0x8f8,.03)
        w(0x59,0xf8,.04)
        assert b.aborted==1 and b.completed==0
        assert air.bursts[0].end==pytest.approx(.04)
        assert not b.read(0xc,regs,.2)&1
    finally: b.close()


def test_air_chunk_invariance():
    air=VirtualAir(Channel(offset_hz=200,clock_ppm=1000,cn_db=12))
    air.emit(.003,HZ,b"\x55"*16+bytes.fromhex("076b3e36"),bytes(range(64)))
    a=AirSource(air,240000,HZ);b=AirSource(air,240000,HZ)
    x=a.read(40000);y=np.concatenate([b.read(1),b.read(799),b.read(10000),b.read(29200)])
    np.testing.assert_array_equal(x,y)


requires_compiler=pytest.mark.skipif(not all(shutil.which(x) for x in ("clang","ld.lld","llvm-objcopy","llvm-nm")),
                                    reason="Cortex-M0 build tools unavailable")


@requires_compiler
@pytest.mark.parametrize("prefix,guard,exact,raw,shifted",[(0,False,4,4,0),(2,False,2,2,0),(2,True,4,2,2)])
def test_compiled_virtual_link(prefix,guard,exact,raw,shifted):
    r=trial(Channel(prefix_every=prefix),guard=guard)
    assert r["tx_completed"]==r["received"]==4
    assert r["exact_packets"]==r["crc_valid"]==exact
    assert r["raw_exact_frames"]==raw and r["recovered_offset_2"]==shifted
    assert r["rx_overruns"]==r["rx_underflows"]==0


@requires_compiler
def test_slow_poll_causes_loss():
    r=trial(Channel(),threshold=6,poll_us=25000)
    assert r["rx_overruns"]>0 and r["exact_packets"]<4


@requires_compiler
def test_large_clock_error_causes_loss():
    r=trial(Channel(clock_ppm=15000))
    assert r["exact_packets"]<4


def test_fifo_overflow_rejects_tx():
    air=VirtualAir()
    b=VirtualTxBackend(air,240000,HZ)
    try:
        regs,w=configure(b,n=128)
        for i in range(65): w(0x5f,i)
        w(0x59,0x8f8,.01)
        assert b.overflows==b.rejected==1 and not air.bursts
    finally: b.close()


def test_summary_worst_phase():
    from tools.k6_fsk_virtual_link import summarize
    row=dict(deviation_hz=1800,offset_hz=0,clock_ppm=0,cn_db=25,signal_dbm=-70,
             prefix_every=0,threshold=4,poll_us=1000,gap_us=12000,guard=False,rate=240000,
             packets=4,tx_hz=HZ,rx_hz=HZ,seed=1,lead_us=30000,packet_delivery=1.,
             rx_overruns=0,rx_underflows=0)
    rows=[row,dict(row,seed=2,lead_us=35000,packet_delivery=.75,rx_overruns=1),
          dict(row,threshold=6,seed=2)]
    result=summarize(rows)
    assert result[0]["threshold"]==6
    assert result[1]["trials"]==2 and result[1]["worst_delivery"]==.75
    assert result[1]["mean_delivery"]==.875 and result[1]["max_rx_overruns"]==1
