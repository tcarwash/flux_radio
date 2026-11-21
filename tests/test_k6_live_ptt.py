"""Remote PTT live protocol and Hamlib lease regression (no physical RF)."""
import struct
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from k6_live import LiveLink, LiveUnavailable
from k6_live_v2 import PTT_LEASE, PTT_RELEASE, PTT_LEASE_MS, decode_request
from k6_install import InstallError, cps_frame
from k6_rigctld import Rig


class Port:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.buf = b''
        self.writes = []
        self.tx = False
        self.owner = None

    def write(self, request):
        self.writes.append(request)
        if request.startswith(b'FLXLV1'):
            cmd, seq = request[6:8]
            if cmd != 0x68:
                raise AssertionError(f'unexpected v1 opcode {cmd}')
            flags = (1 << 18) if self.enabled else 0
            response = b'\0FLCP\1' + struct.pack('<I', flags)
        else:
            cmd, seq, arg, payload = decode_request(request)
            if cmd == PTT_LEASE:
                assert arg == PTT_LEASE_MS == 800
                if self.owner is None:
                    self.owner = payload
                    self.tx = True
                    response = b'\0'
                elif self.owner == payload:
                    response = b'\0'
                else:
                    response = b'\1'
            elif cmd == PTT_RELEASE:
                assert arg == 0
                if self.owner == payload:
                    self.tx = False
                    self.owner = None
                response = b'\0'
            else:
                raise AssertionError(f'unexpected v2 opcode {cmd}')
        self.buf += cps_frame(cmd, seq, response)

    def read(self, n):
        data, self.buf = self.buf[:n], self.buf[n:]
        return data


def test_live_ptt_lease_token_ownership_and_release():
    port = Port()
    link = LiveLink(port, initial_seq=0)
    token = b'12345678'
    assert link.ptt_hold(token) is None
    assert port.tx is True
    assert link.ptt_hold(token) is None
    with pytest.raises(InstallError, match='busy'):
        link.ptt_hold(b'87654321')
    assert port.owner == token and port.tx
    link.ptt_release(token)
    assert port.owner is None and not port.tx
    link.ptt_release(token)  # idempotent
    commands = [decode_request(w)[0] for w in port.writes if w.startswith(b'FLXLV2')]
    assert commands == [PTT_LEASE, PTT_LEASE, PTT_LEASE, PTT_RELEASE, PTT_RELEASE]


def test_no_ptt_support_never_keys():
    port = Port(enabled=False)
    with pytest.raises(InstallError, match='does not advertise'):
        LiveLink(port).ptt_hold(b'12345678')
    assert not port.tx
    assert all(w.startswith(b'FLXLV1') for w in port.writes)


@pytest.mark.parametrize('token', [b'', b'\0'*8, b'x', b'a'*9, 'text', None])
def test_ptt_rejects_invalid_tokens(token):
    p = Port()
    l = LiveLink(p)
    with pytest.raises(ValueError, match='token'):
        l.ptt_hold(token)
    assert not p.writes


class FakeLink:
    def __init__(self):
        self.token = None
        self.events = []
    def ptt_hold(self, token):
        if self.token is not None and self.token != token:
            raise InstallError('busy')
        self.token = token
        self.events.append('hold')
    def ptt_release(self, token):
        if self.token == token:
            self.token = None
            self.events.append('release')
    def status(self):
        return dict(transmitting=self.token is not None)


def test_hamlib_auto_ptt_renew_disconnection_and_capabilities():
    link = FakeLink()
    rig = Rig(link, ['status', 'ptt'], enable_ptt=True)
    try:
        assert 'ptt_type=0x1' in rig.execute(r'\dump_state')
        assert rig.execute('T 1', session='one') == 'RPRT 0\n'
        assert rig.execute('T 1', session='two') == 'RPRT -9\n'
        assert link.token is not None
        time.sleep(0.24)
        assert link.events.count('hold') >= 2
        rig.disconnect('one')
        assert link.token is None
        assert 'release' in link.events
        assert rig.execute('T 1', session='three') == 'RPRT 0\n'
        assert rig.execute('T 0', session='three') == 'RPRT 0\n'
        assert link.token is None
    finally:
        rig.close()


def test_legacy_and_disabled_hamlib_do_not_advertise_ptt():
    for features, enabled in [(['status'], True), (['status', 'ptt'], False)]:
        rig = Rig(FakeLink(), features, enable_ptt=enabled)
        try:
            assert 'ptt_type=0x0' in rig.execute(r'\dump_state')
            assert rig.execute('T 1') == 'RPRT -11\n'
        finally:
            rig.close()
