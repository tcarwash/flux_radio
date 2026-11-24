"""Opt-in Sotto digital voice lab for the SoapyFang/K6 workbench.

Uses the real compiled ARM K6 Sotto TX module + modelled 4800-baud 2FSK
RFBackend/AirSource path + independent wire decoder + pinned native
fixed-point Sotto voice decoder. This is NOT a full firmware-host integration
and never opens an SDR TX sink, cable, USB device, hardware bench or radio.
"""
from __future__ import annotations

import io
import hashlib
import math
from pathlib import Path
import threading
import tempfile
import time
import wave

import numpy as np

from sim.k6emu.rf.virtual_link import Channel


class SottoVoiceLab:
    """One synchronous, mutually exclusive offline experiment at a time."""

    def __init__(self, source_tree: Path):
        self.source_tree = Path(source_tree).expanduser().resolve()
        if not (self.source_tree / "c/k6/mod/sotto_live_tx.c").is_file():
            raise ValueError("Sotto lab requires a checkout with experimental live TX")
        self._lock = threading.Lock()
        self._last = None
        self._last_wav = None
        self._temp = None
        self._source_hash = None
        self._cached_artifacts = {}
        self._native_decoder = None
        self._closed = False

    # This deliberately uses file *contents*, not a Git branch name or
    # mtime. A developer may tune the encoder or service in a dirty worktree
    # without changing HEAD, then immediately rerun the same virtual test.
    _SOURCE_INPUTS = (
        "c/k6/k6_mod.h",
        "c/k6/mod/sotto_live_tx.c",
        "c/k6/mod/sotto_live_engine.h",
        "c/k6/mod/sotto_live_session.h",
        "c/k6/mod/sotto_m0rt.h",
        "c/k6/mod/dv_guard33_session.h",
        "c/k6/mod/sotto_tx_codec_io.h",
        "c/k6/mod/dv_live_slim_dct.h",
        "c/fixed/sotto_fx_slim.c",
        "c/fixed/sotto_fx_dec.c",
        "c/fixed/sotto_fx_lr.c",
        "c/fixed/fx_math.c",
        "c/fixed/fx_tables.c",
        "c/fixed/sotto_slim.h",
        "c/fixed/fx.h",
        "c/fixed/fx_tables.h",
        "c/fixed/fx_internal.h",
        "c/fixed/slim_cells.h",
        "c/sotto.h",
        "c/sotto_lr_dec.inc",
        "c/sotto_lr_tables.h",
        "tools/k6_sotto_module.py",
        "tools/k6_sotto_ivf_deck.py",
        "tests/data/k6/sotto_wire_rx_emu.c",
        "modem/k6mod.py",
    )

    def _fingerprint(self):
        h = hashlib.sha256()
        for name in self._SOURCE_INPUTS:
            p = self.source_tree / name
            if not p.is_file():
                raise ValueError(f"Missing Sotto compilation input: {name}")
            h.update(name.encode("utf-8") + b"\0")
            h.update(p.read_bytes())
        return h.hexdigest()

    def _prepared(self, fixture):
        from tools.k6_sotto_virtual_link import prepare
        from tools.k6_sotto_opt_bench import mixed_pcm
        from tools.k6_sotto_e2e_mock import native_decoder

        digest = self._fingerprint()
        if digest != self._source_hash:
            # Discard cached objects when any compile-relevant file changes.
            if self._temp is not None:
                self._temp.cleanup()
            self._temp = tempfile.TemporaryDirectory(prefix="soapyfang-sotto-")
            self._source_hash = digest
            self._cached_artifacts = {}
            self._native_decoder = None
        hit = fixture in self._cached_artifacts
        root = Path(self._temp.name)
        if self._native_decoder is None:
            self._native_decoder = native_decoder(
                self.source_tree, root / "sotto-decoder.so")
        if not hit:
            dest = root / fixture
            dest.mkdir()
            pcm = mixed_pcm() if fixture == "mixed" else None
            art = prepare(self.source_tree, dest, pcm=pcm)
            if self._fingerprint() != digest:
                self._cached_artifacts.clear()
                self._source_hash = None
                raise RuntimeError("Sotto source changed during compilation; rerun")
            self._cached_artifacts[fixture] = art
        return self._cached_artifacts[fixture], self._native_decoder, hit

    def close(self):
        with self._lock:
            if self._temp is not None:
                self._temp.cleanup()
                self._temp = None
            self._source_hash = None
            self._cached_artifacts = {}
            self._native_decoder = None
            self._last_wav = None
            self._closed = True

    def state(self):
        """No emulated radio state or hardware PTT is changed by status."""
        return {
            "enabled": True,
            "kind": "offline-soapyfang-sotto-1200",
            "busy": self._lock.locked(),
            "last": self._last,
            "audio_available": self._last_wav is not None,
            "cached_fixtures": sorted(self._cached_artifacts),
            "source_fingerprint": self._source_hash[:12] if self._source_hash else None,
            "limits": "separate ARM module bench and SDR-backed virtual RF, NOT booted Aura firmware",
        }

    def wav(self) -> bytes:
        if self._lock.locked():
            raise RuntimeError("Sotto lab is running")
        if self._last_wav is None:
            raise ValueError("No verified received audio yet")
        return self._last_wav

    def run(self, *, fixture: str = "mixed", cn_db: float = 25.0,
            offset_hz: float = 0.0, prefix_every: int = 0,
            adc_cycles: int = 4800, spi_us: float = 10.0,
            spi_byte_us: float = 0.0) -> dict:
        """Build, execute, receive, decode and render one half-duplex session."""
        if fixture not in ("mixed", "triangle"):
            raise ValueError("Fixture must be mixed or triangle")
        cn_db, offset_hz, spi_us, spi_byte_us = map(
            float, (cn_db, offset_hz, spi_us, spi_byte_us))
        if not all(math.isfinite(n) for n in (cn_db, offset_hz, spi_us, spi_byte_us)):
            raise ValueError("Model parameters must be finite")
        if not (5 <= cn_db <= 45 and -4000 <= offset_hz <= 4000
                and 1 <= spi_us <= 100 and 0 <= spi_byte_us <= 5):
            raise ValueError("RF / SPI model parameter outside safe offline range")
        if isinstance(prefix_every, bool) or isinstance(adc_cycles, bool):
            raise ValueError("Invalid non-integer simulation setting")
        if (int(prefix_every) != prefix_every or int(adc_cycles) != adc_cycles
                or not 0 <= prefix_every <= 6 or not 0 <= adc_cycles <= 7500):
            raise ValueError("Invalid FSK prefix or ADC workload")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Sotto lab is busy")
        try:
            if self._closed:
                raise RuntimeError("Sotto lab was closed")
            run_started = time.perf_counter()
            # Invalidate stale playback before every attempt; failed sessions
            # must never leak the previously recovered speech.
            self._last = None
            self._last_wav = None
            from tools.k6_sotto_e2e_mock import MockSottoRadio
            from tools.k6_sotto_opt_bench import mixed_pcm
            compiled_at = time.perf_counter()
            artifact, decoder, reused = self._prepared(fixture)
            setup_s = time.perf_counter() - compiled_at
            pcm = mixed_pcm() if fixture == "mixed" else None
            radio = MockSottoRadio(artifact, decoder, pcm=pcm)
            try:
                radio.install()
                if radio.select() != "SOTTO":
                    raise RuntimeError("Mock Sotto VFO selection failed")
                passed = radio.transmit(
                    adc_cycles=int(adc_cycles), spi_us=spi_us,
                    spi_byte_us=spi_byte_us,
                    channel=Channel(cn_db=cn_db, offset_hz=offset_hz,
                                    prefix_every=int(prefix_every)),
                )
                raw = radio.last
                if raw is None:
                    raise RuntimeError("ARM modem produced no TX/RX report")
                if passed:
                    pcm_blocks = []
                    while (block := radio.speaker_next()) is not None:
                        pcm_blocks.append(block)
                    if not pcm_blocks:
                        raise RuntimeError("Sotto receive produced no speaker samples")
                    speaker = np.concatenate(pcm_blocks)
                    if not np.array_equal(speaker, radio.rx_audio):
                        raise RuntimeError("Sotto virtual speaker PCM mismatch")
                    output = io.BytesIO()
                    with wave.open(output, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(8000)
                        wav.writeframes(np.asarray(speaker, dtype="<i2").tobytes())
                    self._last_wav = output.getvalue()
                summary = {
                    "passed": bool(passed), "fixture": fixture,
                    "radio_path": "actual ARM TX -> SoapyFang RFBackend/VirtualAir IQ -> independent RX -> pinned C Sotto decoder",
                    "experimental_api": 30,
                    "compiled_artifacts_reused": reused,
                    "compile_setup_seconds": round(setup_s, 3),
                    "half_duplex": True,
                    "source_revision": artifact["revision"][:12],
                    "source_dirty": artifact["dirty"],
                    "module_bytes": raw["module_bytes"],
                    "samples_in": raw["samples_expected"],
                    "samples_captured": raw["samples"],
                    "pcm_loss": bool(raw["pcm_lost"]),
                    "codec_frames": raw["codec_frames"],
                    "codec_exact": bool(raw["codec_exact"]),
                    "rf_packets_received": raw["received"],
                    "session_complete": bool(raw["session"]["complete"]),
                    "wire_decoder_agrees": bool(raw["wire_decoder_agrees"]),
                    "audio_samples": int(len(radio.rx_audio)) if passed else 0,
                    "speaker_blocks_20ms": int(len(pcm_blocks)) if passed else 0,
                    "tx_idle": not raw["tx"]["active"] and not radio.ptt,
                    "radio_returned_to_rx": radio.radio_state == "RX",
                    "spi_reads": raw["spi_reads"],
                    "keyed_spi_reads": raw["keyed_reads"],
                    "spi_read_bytes": raw["spi_read_bytes"],
                    "keyed_spi_read_bytes": raw["keyed_read_bytes"],
                    "stack_bytes": raw["stack_bytes"],
                    "adc_cycles": adc_cycles,
                    "spi_us": spi_us,
                    "spi_byte_us": spi_byte_us,
                    "cn_db": cn_db,
                    "offset_hz": offset_hz,
                    "prefix_every": prefix_every,
                    "limitations": "offline modeled RF; no hardware, no Aura on-device RX/PCM integration",
                }
                if not summary["tx_idle"] or not summary["radio_returned_to_rx"]:
                    raise RuntimeError("Failed to restore half-duplex idle/RX state")
                summary["total_wall_seconds"] = round(time.perf_counter() - run_started, 3)
                self._last = summary
                return summary
            finally:
                # A mock install is local, never a real firmware library write.
                radio.remove()
        finally:
            self._lock.release()
