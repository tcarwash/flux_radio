"""Opt-in cable regression against the compiled firmware in Unicorn."""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import k6_pack_install

import k6_install
from k6_build_identity import image_build_id
from k6_install import CABLE_HANDSHAKE, CMD_CAPS, CMD_END, CPS_HEADER, Cable, InstallError, k6mod

TEST_KEY = bytes(range(16))


class SimulatedSerial:
    """Advance the simulated CPU while the real cable client waits for bytes."""

    def __init__(self, board):
        self.board = board
        self.offset = 0
        self.closed = False
        self.requests = []

    def reset_input_buffer(self):
        self.offset = len(self.board.uart_tx)

    def write(self, data):
        self.requests.append(bytes(data))
        self.board.inject_uart_rx(data)
        return len(data)

    def read(self, size):
        if self.offset == len(self.board.uart_tx):
            self.advance(65536)
        data = bytes(self.board.uart_tx[self.offset:self.offset + size])
        self.offset += len(data)
        return data

    def close(self):
        self.closed = True

    def advance(self, instructions):
        self.board.run(instructions)
        assert not self.board.fault, self.board.fault

    def monotonic(self):
        return self.board.cycles / 96_000_000

    def sleep(self, seconds):
        self.advance(round(seconds * 96_000_000 / self.board.cpi))


@pytest.fixture
def simulated_radio(tmp_path, monkeypatch):
    def start(image, *, cable_only=False):
        pytest.importorskip("unicorn")
        from sim.k6emu.__main__ import K6Board

        keyed = tmp_path / "keyed-firmware.bin"
        keyed.write_bytes(image if cable_only else k6mod.key_image(image, TEST_KEY))
        board = K6Board(keyed, collect_mmio=False)
        # An erased chip with Aura's first-boot marker is sufficient for boot.
        board.flash_data[0xB000:0xB004] = b"AURA"
        port = SimulatedSerial(board)
        port.advance(70_000_000)

        # Both sides use virtual time even when Unicorn runs slower than the radio.
        monkeypatch.setattr(k6_install, "time", SimpleNamespace(
            monotonic=port.monotonic, sleep=port.sleep, time=time.time,
        ))
        return port
    return start


def test_compiled_cable_dispatch(simulated_radio):
    """Distinct opcodes must reach CAPS, INFO, SERIAL, unknown-command and END handlers."""
    image_path = os.environ.get('K6_SIM_DISPATCH_IMAGE')
    if not image_path:
        pytest.skip('set K6_SIM_DISPATCH_IMAGE to a cable-only starter firmware.bin')
    port = simulated_radio(Path(image_path).read_bytes(), cable_only=True)
    before = bytes(port.board.flash_data)
    cable = Cable(port, log=lambda _: None)
    assert cable.handshake(CABLE_HANDSHAKE, wait=3.0)
    assert cable.capabilities()['cable_only']
    info = cable.info()
    assert info['protocol'] in (2, 4) and info['slots'] == 32
    assert info['arena'] > 0
    assert cable.serial_watermark() == 0
    with pytest.raises(InstallError, match='refused command 0x7e'):
        cable.call(0x7e)
    assert bytes(port.board.flash_data) == before
    assert cable.call(CMD_END) == b''


def test_compiled_cable_only_install(simulated_radio, monkeypatch):
    """Exercise a starter's real module upload and readback without a signing key."""
    pack_path = os.environ.get('K6_SIM_STARTER_PACK')
    if not pack_path:
        pytest.skip('set K6_SIM_STARTER_PACK to a cable-only starter build directory')
    pack = Path(pack_path)
    port = simulated_radio((pack / 'firmware.bin').read_bytes(), cable_only=True)
    monkeypatch.setattr(k6_install, 'open_port', lambda _: port)
    spec = json.loads(k6_pack_install.DEFAULT.read_text())
    messages = []
    k6_pack_install.provision_pack(pack, spec, 'simulated', action='install', log=messages.append)
    assert '  bundled modules installed and read back successfully' in messages
    assert port.closed


def test_compiled_base_cable(simulated_radio):
    image_path = os.environ.get("K6_SIM_BASE_IMAGE")
    if not image_path:
        pytest.skip("set K6_SIM_BASE_IMAGE to a Base firmware.bin from tools/k6.py matrix --build")
    image = Path(image_path).read_bytes()
    port = simulated_radio(image)
    before = bytes(port.board.flash_data)
    cable = Cable(port, log=lambda _: None)
    assert cable.handshake(CABLE_HANDSHAKE, wait=3.0)

    reported = cable.capabilities()
    assert reported["format"] == 2
    assert reported["build_id"] == image_build_id(image)
    assert reported["module_loader"]
    assert not reported["scanner"]
    assert not reported["fsk_rx"]
    assert cable.authenticated_capabilities(TEST_KEY) == reported
    with pytest.raises(InstallError, match="expected module key"):
        cable.authenticated_capabilities(bytes(reversed(TEST_KEY)))

    assert sum(req.startswith(bytes([CPS_HEADER, CMD_CAPS])) for req in port.requests) == 3
    assert bytes(port.board.flash_data) == before


def test_compiled_scan_data_pack_preflight(simulated_radio, tmp_path, monkeypatch):
    pack_path = os.environ.get("K6_SIM_SCAN_DATA_PACK")
    if not pack_path:
        pytest.skip("set K6_SIM_SCAN_DATA_PACK to the output of tools/k6.py build scan data")
    pack = Path(pack_path)
    port = simulated_radio((pack / "firmware.bin").read_bytes())
    before = bytes(port.board.flash_data)
    key_file = tmp_path / "synthetic.key"
    key_file.write_text(TEST_KEY.hex())
    monkeypatch.setattr(k6_install, "open_port", lambda _: port)
    spec = json.loads(k6_pack_install.DEFAULT.read_text())

    report = k6_pack_install.radio_preflight(pack, spec, "simulated", key_file)

    assert set(report["packs"]) == {"base", "scan", "data"}
    assert report["build_id_match"]
    assert report["capabilities_match"]
    assert report["capability_report_authenticated"]
    assert report["key_possession_verified"]
    assert port.closed
    assert bytes(port.board.flash_data) == before


def test_compiled_pack_install_and_retry(simulated_radio, tmp_path, monkeypatch):
    pack_path = os.environ.get("K6_SIM_SCAN_DATA_PACK")
    if not pack_path:
        pytest.skip("set K6_SIM_SCAN_DATA_PACK to a built Scan + Data pack")
    pack = Path(pack_path)
    port = simulated_radio((pack / "firmware.bin").read_bytes())
    key_file = tmp_path / "synthetic.key"
    key_file.write_text(TEST_KEY.hex())
    monkeypatch.setattr(k6_install, "open_port", lambda _: port)
    spec = json.loads(k6_pack_install.DEFAULT.read_text())
    # Firmware reset is outside the emulator's cable tests. Keep this cable
    # session open for the second plan, using the same real UART transport.
    monkeypatch.setattr(Cable, "end", lambda _: None)
    opened = []
    real_open = k6_install.open_cable
    def open_once(*args, **kwargs):
        if not opened:
            opened.append(real_open(*args, **kwargs))
        return opened[0]
    monkeypatch.setattr(k6_install, "open_cable", open_once)
    messages = []

    # Interrupt the second upload before any part of it is committed. The
    # next plan must recognize the first verified module and finish the pack.
    send_image = Cable.send_image
    uploads = 0

    def interrupted(cable, image, transfer):
        nonlocal uploads
        uploads += 1
        if uploads == 2:
            raise OSError("simulated cable disconnect")
        return send_image(cable, image, transfer)

    with monkeypatch.context() as interruption:
        interruption.setattr(Cable, "send_image", interrupted)
        with pytest.raises(OSError, match="simulated cable disconnect"):
            k6_pack_install.provision_pack(pack, spec, "simulated", key_file,
                                           action="install", log=messages.append)
    assert uploads == 2
    messages.clear()
    k6_pack_install.provision_pack(pack, spec, "simulated", key_file,
                                   action="install", log=messages.append)
    after = bytes(port.board.flash_data)
    assert sum("already verified" in line for line in messages) == 1
    assert sum("module" in line and " B" in line for line in messages) == 3
    messages.clear()
    k6_pack_install.provision_pack(pack, spec, "simulated", key_file,
                                   action="install", log=messages.append)
    assert sum("already verified" in line for line in messages) == 4
    assert bytes(port.board.flash_data) == after


def test_compiled_designer_install(simulated_radio):
    image_path=os.environ.get("K6_SIM_DESIGNER_IMAGE")
    if not image_path:pytest.skip("set K6_SIM_DESIGNER_IMAGE")
    from k6_theme_package import pack
    port=simulated_radio(Path(image_path).read_bytes())
    cable=Cable(port,log=lambda _:None)
    assert cable.handshake(CABLE_HANDSHAKE,wait=3.0)
    assert cable.info()["protocol"]==3
    blob=pack("flux")
    base=0x110000+31*0x4000
    before=bytes(port.board.flash_data)
    r,_,_,_=cable.send_image(k6mod.image(blob,0,1,bytes(reversed(TEST_KEY)),lib=31,theme=True),1)
    assert r==-4
    assert bytes(port.board.flash_data)==before
    r,_,_,put=cable.send_image(k6mod.image(blob,0,2,TEST_KEY,lib=31,theme=True),2)
    assert (r,put)==(3,len(blob))
    assert cable.read(base,len(blob))==blob
    after=bytes(port.board.flash_data)
    assert after[:base]==before[:base] and after[base+len(blob):]==before[base+len(blob):]
    r,_,_,put=cable.send_image(k6mod.image(blob,0,3,TEST_KEY,lib=31,theme=True),3)
    assert (r,put)==(3,-7)
    assert bytes(port.board.flash_data)==after
    r,_,_,put=cable.send_image(k6mod.image(b"test",0,4,TEST_KEY,lib=31),4)
    assert (r,put)==(3,-7)
    assert bytes(port.board.flash_data)==after
