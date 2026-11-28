"""Passive timestamped K6 emulator FD6818B write trace; no bench or RF TX."""
import json
import struct
import pytest
from sim.k6emu.__main__ import K6Board, APP_BASE, RAM_BASE
from sim.k6emu.rf.tx_trace import observe_writes
from tools.k6_tx_path_lab import main


def tiny_image(tmp_path):
    path=tmp_path/'safe_loop.bin'
    path.write_bytes(struct.pack('<IIH', RAM_BASE+24*1024,APP_BASE+9,0xe7fe))
    return path


def write_simulated_bus(board,reg,val,cycles):
    board.cycles=cycles
    board._fd_pin_change(8,0)
    packet=((reg&0x7f)<<16)|(val&0xffff)
    for bitno in range(23,-1,-1):
        bit=((packet>>bitno)&1)<<6
        board._fd_pin_change(bit,bit|32)
    board._fd_pin_change(0,8)


def test_captured_full_3wire_bus_events_timestamped_and_unmodified(tmp_path):
    board=K6Board(tiny_image(tmp_path),capture_tx_events=True)
    try:
        write_simulated_bus(board,0x36,0x18a2,96000)
        write_simulated_bus(board,0x36,0x1882,192000)
        write_simulated_bus(board,0x70,0x8c44,288000)
        write_simulated_bus(board,0x58,0x0001,384000)
        write_simulated_bus(board,0x72,0xc194,480000)
        assert board.fd_writes==[(0x36,0x18a2),(0x36,0x1882),
                                 (0x70,0x8c44),(0x58,1),(0x72,0xc194)]
        assert board.fd_write_events==[(96000,0x36,0x18a2),
                                       (192000,0x36,0x1882),
                                       (288000,0x70,0x8c44),
                                       (384000,0x58,1),
                                       (480000,0x72,0xc194)]
        record=observe_writes(board.fd_write_events)
        assert record['event_count']==5
        assert record['hardware_qualified'] is False
        assert record['events'][0]['time_us']==1000
        assert record['intent_counts']['driver6_write']==2
        assert any('REG70' in s for s in record['mux_conflict_notices'])
        assert any('REG72' in s for s in record['mux_conflict_notices'])
    finally:board.close_rf()


def test_capture_opt_in_default_off(tmp_path):
    board=K6Board(tiny_image(tmp_path))
    try:
        write_simulated_bus(board,0x19,0x1041,1000)
        assert board.fd_writes==[(0x19,0x1041)]
        assert board.fd_write_events==[]
    finally:board.close_rf()


@pytest.mark.parametrize('bad',[[(12,0x80,1)],[(12,0x30,65536)],
                              [(12,0x30,-1)],[(12,0x30,1),(11,0x30,1)],
                              [(-1,0x30,0)],[(float('nan'),0x30,0)]])
def test_inert_trace_parser_fails_closed(bad):
    with pytest.raises(ValueError):observe_writes(bad)


def test_register_trace_cli_adds_observed_events_separately_from_models(tmp_path):
    trace=tmp_path/'trace.json';out=tmp_path/'report.json'
    trace.write_text(json.dumps(dict(clock_hz=96_000_000,writes=[
        [96000,0x36,0x18a2],[192000,0x51,0x9000],
        [288000,0x51,0xd000]])))
    assert main(['--modes','cw','--max-actors','1','--skip-soapy',
                 '--regtrace',str(trace),'--json',str(out)])==0
    doc=json.loads(out.read_text())
    assert doc['register_trace']['event_count']==3
    assert len(doc['sampled_replays'])>0
    assert doc['register_trace']['hardware_qualified'] is False
    assert any('REG51' in v for v in doc['register_trace']['mux_conflict_notices'])
    assert doc['physical_rf'] is False
