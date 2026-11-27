"""Host-only regression for passive GP0 serial tracing; no radio or RF."""
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import k6_sotto_gp0_follow as follow


def test_run_return_pair_must_match_exact_module_serial():
    active = False
    active, done = follow.consume_line('K6 MOD ret=-12 (0xfffffff4)', 1795730062, active)
    assert not active and not done
    active, done = follow.consume_line('K6 MOD run serial=987 code=9999 B status=9999 B', 1795730062, active)
    assert not active and not done
    active, done = follow.consume_line('K6 MOD ret=-12 (0xfffffff4)', 1795730062, active)
    assert not active and not done
    active, done = follow.consume_line('K6 MOD run serial=1795730062 code=9784 B status=9784 B', 1795730062, active)
    assert active and not done
    active, done = follow.consume_line('K6 MOD ret=-12 (0xfffffff4) in 4432 ms', 1795730062, active)
    assert not active and done


class Port:
    def __init__(self, events):
        self.events = list(events)
        self.writes = []
    def read(self, n):
        return self.events.pop(0) if self.events else b''
    def write(self, data):
        self.writes.append(data)


def test_passive_monitor_reads_only_then_triggers_on_right_module():
    port = Port([b'K6 MOD run serial=123 code=42 B\r\nK6 MOD ret=-1\n',
                 b'K6 MOD run serial=1795730062 code=9784 B\r\n',
                 b'K6 MOD ret=-12 (0xfffffff4) in 4432 ms stack_free=1428 B\r\n'])
    now = [0]
    def clock():
        now[0] += .01
        return now[0]
    lines, ret = follow.observe(port, module_serial=1795730062, timeout=1,
                                on_line=lambda _:None, clock=clock, pause=lambda _:None)
    assert ret.startswith('K6 MOD ret=-12')
    assert len(lines) == 4
    assert not port.writes


def test_passive_monitor_timeout_has_no_uart_writes():
    port = Port([b'K6 MOD ret=-12\n', b'K6 MOD run serial=42 code=42\n'])
    now = [0]
    def clock():
        now[0] += .05
        return now[0]
    try:
        follow.observe(port, module_serial=1795730062, timeout=.2,
                       on_line=lambda _:None, clock=clock, pause=lambda _:None)
    except TimeoutError:
        pass
    else:
        assert False, 'must fail closed if this specific module did not run'
    assert not port.writes


def test_capture_saves_raw_before_cable_end(tmp_path, monkeypatch):
    source = tmp_path / 'evidence.json'
    from k6_sotto_gp0_timing import decode_timing
    from k6_sotto_gp0_fault import decode
    import struct
    f = b'GPF1' + struct.pack('<IiI2Ii4I', 1795730062,-12,0x27,0,0,0,3,2182,3147,0)
    t = b'GPT1' + struct.pack('<9I',1795730062,1,811,24779,4849338,22,1,2,3)
    assert decode(f)['valid'] and decode_timing(t,decode(f))['valid']
    state={}
    class Cable:
        def __init__(self, dev, logger): pass
        def handshake(self,*args,**kwargs): return True
        def info(self): return {'protocol':5,'arena':14976}
        def call(self,cmd): return {0x75:f,0x76:t}[cmd]
        def end(self):
            state['end_after_save'] = source.exists()
    monkeypatch.setitem(sys.modules, 'k6_install',
                        types.SimpleNamespace(Cable=Cable,CABLE_HANDSHAKE=b'PROGRAM'))
    record=follow.capture_after_return(Port([]),serial=1795730062,
                                       lines=['K6 MOD run serial=1795730062','K6 MOD ret=-12'],
                                       return_line='K6 MOD ret=-12',save=source)
    assert record['timing']['gp0_code3_reason']=='pcm_queue_full'
    assert state['end_after_save'] is True
    assert source.read_text().count('fault_raw_hex')==1


def test_capture_bad_decode_preserves_raw_before_reset(tmp_path, monkeypatch):
    source=tmp_path/'bad.json'; state={}
    class Cable:
        def __init__(self, dev, logger): pass
        def handshake(self,*a,**kw):return True
        def info(self):return {'protocol':5,'arena':14976}
        def call(self,cmd):return b'bad'
        def end(self):state['had_raw']=source.exists() and '626164' in source.read_text()
    monkeypatch.setitem(sys.modules, 'k6_install',
                        types.SimpleNamespace(Cable=Cable,CABLE_HANDSHAKE=b'PROGRAM'))
    try:
        follow.capture_after_return(Port([]),serial=1795730062,
                                    lines=['K6 MOD run serial=1795730062','K6 MOD ret=-12'],
                                    return_line='K6 MOD ret=-12',save=source)
    except ValueError:pass
    else:assert False, 'corrupt report must fail closed'
    assert state['had_raw'] is True
