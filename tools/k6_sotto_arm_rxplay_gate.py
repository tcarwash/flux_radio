#!/usr/bin/env python3
"""Offline two-ARM Sotto-1200 virtual RF -> compiled Cortex-M0 decoder -> PCM/PWM gate.

Reuses the existing two-ARM virtual RF path (live ARM TX -> VirtualAir -> API31
ARM RX) and compiles the pinned fixed-point Sotto-1200 decoder (c/fixed/sotto_fx_dec.c,
sotto_fx_lr.c, fx_math.c, fx_tables.c) into a Cortex-M0 RAM module. Exposes every
ARM-decoded 20-ms PCM and PWM block and compares sample-for-sample against the
independent Linux native decoder and c/k6/sotto_pager.c PWM reference.

Offline only: no physical radio, PA, serial cable, SDR, or speaker DMA is used.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP,
)
from modem import k6mod
from sim.k6emu.__main__ import RAM_BASE, SYSCFG_CFGR1
from sim.k6emu.rf.backend import RFBackend
from sim.k6emu.rf.virtual_link import AirSource, Burst, Channel, VirtualAir, VirtualTxBackend
from tools.k6_build import source_tree as materialize_c_tree
from tools.k6_fsk_emulator_replay import receive
from tools.k6_fsk_virtual_link import HZ
from tools.k6_sotto_e2e_mock import decode_pcm, native_decoder
from tools.k6_sotto_module import PIN, build as build_live
from tools.k6_sotto_opt_bench import mixed_pcm
from tools.k6_sotto_rx4800_dispatch_gate import CABLE, Dispatcher, ENTRY, STOP
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_virtual_link import decode_session, prepare
from tools.k6_user_store_migrate import BASE, END, compact

RX_PLAY_SRC = ROOT / "c/k6/mod/sotto_rx31_play_emu.c"
RX_HDR_MAGIC = 0x5258504C
RX_HDR_FMT = "<14I"
RX_HDR_BYTES = 764
RX_PCM_OFF = 124
RX_PWM_OFF = 444
ARENA_CAP = 14976


def _sotto_dirs(tree: Path, staging: Path):
    """Resolve c/ and c/fixed/ directories, staging submodule symlinks if needed."""
    rev = subprocess.check_output(
        ["git", "-C", str(tree / "sotto"), "rev-parse", "HEAD"], text=True
    ).strip()
    if rev != PIN:
        raise ValueError("Unexpected Sotto pin; requalify codec and SPI tables")
    c_dir = materialize_c_tree(tree, staging)
    return c_dir, c_dir / "fixed"


def build_rx_play(tree: Path, staging: Path) -> tuple[bytes, int]:
    """Compile the actual Cortex-M0 Sotto-1200 RX + decode + PCM/PWM module."""
    c_dir, fixed_dir = _sotto_dirs(tree, staging)
    cflags = [
        *k6mod.CFLAGS,
        "-I" + str(c_dir),
        "-I" + str(fixed_dir),
        "-isystem", str(fixed_dir / "cm4/inc"),
        "-DSOTTO_V36",
        "-DSOTTO_PITCH_THIRD",
        "-DFX_NARROW_MUL",
        "-DFX_CS512_CONST",
        "-DFX_MP_ROWGEN",
        "-DFX_SYNTH_STATE",
        "-DFX_CB_SPI",
        "-Dcalloc=sp_calloc",
        "-Dfree=sp_free",
        "-ffixed-r9",
        "-Wno-unused-function",
        "-Wno-unused-const-variable",
        "-Wno-unused-variable",
    ]
    code, entry = k6mod.build(str(RX_PLAY_SRC), cflags=cflags)
    if entry != k6mod.bin_entry(code):
        raise ValueError("Unexpected RX play module entry")
    if len(code) + 24 > ARENA_CAP:
        raise RuntimeError(f"RX play module ({len(code) + 24} B) exceeds {ARENA_CAP} B arena")
    return code, entry


def pager_ref_lib(c_dir: Path, fixed_dir: Path, destination: Path):
    """Build host reference library reusing c/k6/sotto_pager.c for PWM validation."""
    if not shutil.which("cc"):
        raise RuntimeError("native C compiler unavailable")
    inputs = [
        c_dir / "k6/sotto_pager.c",
        c_dir / "k6/sp_cb.c",
        fixed_dir / "sotto_fx_dec.c",
        fixed_dir / "sotto_fx_lr.c",
        fixed_dir / "fx_math.c",
        fixed_dir / "fx_tables.c",
    ]
    if not all(p.is_file() for p in inputs):
        raise RuntimeError("missing sotto_pager.c or pinned fixed-point decoder source")
    subprocess.run(
        [
            "cc", "-O2", "-shared", "-fPIC",
            "-DSP_LR", "-DSOTTO_V36", "-DSOTTO_PITCH_THIRD", "-DFX_NARROW_MUL",
            "-Dcalloc=sp_calloc", "-Dfree=sp_free",
            "-I" + str(c_dir), "-I" + str(fixed_dir),
            *map(str, inputs), "-o", str(destination), "-lm",
        ],
        check=True, capture_output=True, text=True, timeout=35,
    )
    lib = C.CDLL(str(destination))
    lib.sp_play_start.argtypes = []
    lib.sp_play_start.restype = None
    lib.sp_pcm_to_pwm.argtypes = [
        C.POINTER(C.c_int16), C.POINTER(C.c_uint16), C.c_int, C.c_uint, C.c_int, C.c_int,
    ]
    lib.sp_pcm_to_pwm.restype = None
    return lib


def reference_pwm(pager_lib, pcm: np.ndarray, *, top: int = 2000, gain_q8: int = 256,
                  emphasis: int = 1) -> np.ndarray:
    """Convert reference PCM to PWM compare values via c/k6/sotto_pager.c."""
    if len(pcm) % 160 != 0:
        raise ValueError("PCM length must be a multiple of 160 samples")
    pager_lib.sp_play_start()
    out = np.empty(len(pcm), dtype="<u2")
    in_buf = (C.c_int16 * 160)()
    out_buf = (C.c_uint16 * 160)()
    for off in range(0, len(pcm), 160):
        C.memmove(in_buf, pcm[off:off + 160].tobytes(), 320)
        pager_lib.sp_pcm_to_pwm(in_buf, out_buf, 160, top, gain_q8, emphasis)
        out[off:off + 160] = np.asarray(out_buf[:], dtype="<u2")
    return out


def fnv1a_16(arr: np.ndarray) -> int:
    """FNV-1a 32-bit over little-endian 16-bit samples, matching c/k6/sotto_pager.c."""
    h = 2166136261
    for b in np.asarray(arr, dtype="<u2").tobytes():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def _stage_rx_module(dispatch: Dispatcher, code: bytes, entry: int,
                     arena_cap: int = ARENA_CAP) -> tuple[int, int]:
    """Stage rx module through compiled km_cable_packet, supporting expanded arena."""
    if len(code) + 24 <= 11776:
        return dispatch.verified_cable_stage(code, entry, max_call_quanta=8000)
    envelope = k6mod.image(code, entry, serial=42, key=None, level=31)
    packets = k6mod.packets(envelope, xfer=91, length=64)
    if not packets or len(packets) > 255:
        raise RuntimeError("Invalid cable upload chunk count")
    arena = dispatch.syms["arena"]
    pkt_addr = 0x20005000
    bad = bytes(packets[0][:11]) + bytes([packets[0][11] ^ 0x40]) + packets[0][12:]
    dispatch.board.uc.mem_write(pkt_addr, bad)
    assert dispatch.call(dispatch.linked[CABLE], pkt_addr, 64, arena, arena_cap, limit=8000) == -1
    for idx, pkt in enumerate(packets):
        dispatch.board.uc.mem_write(pkt_addr, pkt)
        actual = dispatch.call(dispatch.linked[CABLE], pkt_addr, 64, arena, arena_cap, limit=8000)
        expected = 3 if idx == len(packets) - 1 else 1
        if actual != expected:
            raise RuntimeError(
                f"Compiled cable loader refused API31 RX module: chunk={idx + 1} actual={actual}"
            )
    if bytes(dispatch.board.uc.mem_read(arena, len(envelope))) != envelope:
        raise RuntimeError("Loader mutated or incompletely staged RX code")
    dispatch.expected_module_entry = arena + 16 + entry

    def on_consumer(*args):
        dispatch.trace["consumer"] += 1

    dispatch.board.uc.hook_add(
        UC_HOOK_CODE, on_consumer,
        begin=dispatch.expected_module_entry, end=dispatch.expected_module_entry,
    )
    return len(packets), len(envelope)


def _clone_air(air: VirtualAir, *, drop_idx: int | None = None,
               corrupt_idx: int | None = None) -> VirtualAir:
    cloned = VirtualAir(air.channel)
    for i, b in enumerate(air.bursts):
        if drop_idx is not None and i == drop_idx:
            continue
        bits = b.bits.copy()
        if corrupt_idx is not None and i == corrupt_idx and len(bits) > 120:
            bits[100:108] *= -1.0
        cloned.bursts.append(Burst(b.start, b.end, b.hz, bits, b.baud))
    return cloned


def _run_tx_arm(elf: Path, image: Path, tree: Path, artifacts: dict,
                blocks: int, max_quanta: int):
    """Run Radio 1 (compiled ARM TX + TIM17 ISR -> VirtualAir) and independent IQ oracle."""
    code, entry = build_live(True, True)
    fw = Dispatcher(elf, image, artifacts["names"])
    try:
        b = fw.board
        cb = artifacts["cb"]
        deck = k6mod.image(artifacts["deck"], 0, serial=77, key=None, level=31, lib=14, cards=True)
        packed, manifest = compact({14: deck})
        assert len(packed) == END - BASE and manifest[0][0] == 14
        b.flash_data[0x100000:0x100000 + len(cb)] = cb
        b.flash_data[BASE:END] = packed
        fw.verified_cable_stage(code, entry, max_call_quanta=4000)
        b.set_gpio("A", 10, 0)
        air = VirtualAir(Channel())
        rf = VirtualTxBackend(air, 240_000, HZ)
        b.rf_backend = rf
        baseline_pa = b.fd_regs.get(0x36)

        def mirror(uc, access, addr, size, value, user):
            if (b.regs.get(SYSCFG_CFGR1, 2) & 3) == 3 and b.memory_alias_mapped:
                off = addr - RAM_BASE
                if 0 <= off and off + size <= 0x1000:
                    uc.mem_write(off, value.to_bytes(size, "little"))

        b.uc.hook_add(UC_HOOK_MEM_WRITE, mirror, begin=RAM_BASE, end=RAM_BASE + 0xFFF)
        syms = fw.syms
        isr = syms.get("pcm_isr")
        ring_symbol = syms.get("pcm_ring")
        if isr is None or ring_symbol is None:
            for line in subprocess.check_output(["llvm-nm", "--demangle", str(elf)], text=True).splitlines():
                fields = line.split(maxsplit=2)
                if len(fields) != 3:
                    continue
                if fields[2] == "aura::hal::pcm_capture::isr":
                    isr = int(fields[0], 16)
                if fields[2] == "aura::hal::pcm_capture::RING":
                    ring_symbol = int(fields[0], 16)
        if isr is None or ring_symbol is None:
            raise RuntimeError("Missing linked ARM TIM17 ISR or ring pointer")
        trampoline = 0x20010000
        b.uc.mem_map(trampoline, 4096)
        b.uc.mem_write(trampoline, b"\x70\x47")
        completed = [False]

        def on_isr_return(uc, addr, size, user):
            completed[0] = True
            uc.emu_stop()

        b.uc.hook_add(UC_HOOK_CODE, on_isr_return, begin=trampoline, end=trampoline)
        stats = dict(samples=0, peak_isr_cycles=0, max_depth=0, lost=0, released_at=None)
        next_isr = None

        def fire_isr():
            saved = b.uc.context_save()
            start = b.cycles
            try:
                b.gpio_external[("A", 11)] = (stats["samples"] // 32) & 1
                b.uc.reg_write(UC_ARM_REG_SP, 0x20005F00)
                b.uc.reg_write(UC_ARM_REG_LR, trampoline | 1)
                b.uc.reg_write(UC_ARM_REG_PC, isr | 1)
                completed[0] = False
                for _ in range(130):
                    b.run(512, 512)
                    if completed[0] or b.fault:
                        break
                if not completed[0] or b.fault:
                    raise RuntimeError(f"Compiled ARM ISR failed to return: {b.fault}")
            finally:
                b.uc.context_restore(saved)
            stats["samples"] += 1
            stats["peak_isr_cycles"] = max(stats["peak_isr_cycles"], b.cycles - start)
            if stats["peak_isr_cycles"] > 12000:
                raise RuntimeError("ISR exceeds modeled 125us period")

        fw.returned = False
        for reg, value in (
            (UC_ARM_REG_R0, syms["app"]),
            (UC_ARM_REG_R1, 0x20005800),
            (UC_ARM_REG_SP, 0x20005FE0),
            (UC_ARM_REG_LR, STOP | 1),
            (UC_ARM_REG_PC, fw.linked[ENTRY] | 1),
        ):
            b.uc.reg_write(reg, value)

        for _ in range(max_quanta):
            b.run(2048, 1024)
            if b.fault or fw.returned:
                break
            rp = int.from_bytes(b.uc.mem_read(ring_symbol, 4), "little")
            if rp:
                if next_isr is None:
                    next_isr = b.cycles + 12000
                while next_isr is not None and b.cycles >= next_isr:
                    fire_isr()
                    next_isr += 12000
                produced, consumed, _, lost = b.uc.mem_read(rp + 640, 4)
                stats["max_depth"] = max(stats["max_depth"], (produced - consumed) & 255)
                stats["lost"] = max(stats["lost"], lost)
                if stats["released_at"] is None and stats["samples"] >= blocks * 80:
                    b.set_gpio("A", 10, 1)
                    stats["released_at"] = b.cycles
                if lost:
                    break
            elif next_isr is not None:
                next_isr = None

        if b.fault or not fw.returned or not fw.module_ret or fw.module_ret[0] <= 0:
            raise RuntimeError(f"ARM live TX failed: fault={b.fault} ret={fw.module_ret}")
        if stats["lost"] or stats["samples"] < blocks * 80 or rf.tx_status()["active"]:
            raise RuntimeError(f"Incomplete live TX: stats={stats} rf={rf.tx_status()}")
        if b.fd_regs.get(0x36) != baseline_pa:
            raise RuntimeError("TX PA not restored")
        if not air.bursts:
            raise RuntimeError("TX emitted no virtual RF")

        virtual_rx = RFBackend(AirSource(air, 240_000, air.bursts[0].hz))
        try:
            frames, decoder = receive(
                virtual_rx, tune=air.bursts[0].hz,
                duration=air.bursts[-1].end + 0.2,
                frame_bytes=66, threshold=4, poll_us=500,
            )
        finally:
            virtual_rx.close()
        codec, session = decode_session(frames)
        if not session["complete"] or not codec:
            raise RuntimeError(f"Independent IQ oracle failed: frames={len(frames)} session={session}")
        return dict(
            tx_code_bytes=len(code),
            tx_frames_encoded=fw.module_ret[0],
            rf_frames=frames,
            codec=codec,
            session=session,
            air=air,
            iq_fsk=decoder["fsk"],
        )
    finally:
        fw.close()


def _run_rx_arm(elf: Path, image: Path, artifacts: dict, rx_code: bytes, rx_entry: int,
                air: VirtualAir, *, expected_return: int, expect_rf_frames: int | None = None,
                corrupt_cb: bool = False, max_quanta: int = 120000):
    """Boot Radio 2, stage sotto_rx31_play_emu.c, run via km_run_on_stack, capture PCM/PWM."""
    radio_rx = Dispatcher(elf, image, artifacts["names"])
    try:
        b = radio_rx.board
        cb = bytearray(artifacts["cb"])
        if corrupt_cb:
            cb[32] ^= 0xFF
        b.flash_data[0x100000:0x100000 + len(cb)] = bytes(cb)
        cb_hash = hashlib.sha256(b.flash_data[0x100000:0x100000 + len(cb)]).hexdigest()

        chunks, envelope = _stage_rx_module(radio_rx, rx_code, rx_entry)
        rx_air = _clone_air(air)
        radio_rx.virtual_air(rx_air)
        for burst in rx_air.bursts:
            burst.start += 0.8
            burst.end += 0.8

        arena = radio_rx.syms["arena"]
        magic_bytes = RX_HDR_MAGIC.to_bytes(4, "little")
        hdr_addr = None
        emit_hooked = False
        pcm_blocks: list[np.ndarray] = []
        pwm_blocks: list[np.ndarray] = []

        def on_emit(uc, addr, size, user):
            nonlocal hdr_addr
            if hdr_addr is None:
                return
            pcm_raw = bytes(uc.mem_read(hdr_addr + RX_PCM_OFF, 320))
            pwm_raw = bytes(uc.mem_read(hdr_addr + RX_PWM_OFF, 320))
            pcm_blocks.append(np.frombuffer(pcm_raw, dtype="<i2").copy())
            pwm_blocks.append(np.frombuffer(pwm_raw, dtype="<u2").copy())

        radio_rx.returned = False
        for reg, value in (
            (UC_ARM_REG_R0, radio_rx.syms["app"]),
            (UC_ARM_REG_R1, 0x20005800),
            (UC_ARM_REG_SP, 0x20005FE0),
            (UC_ARM_REG_LR, STOP | 1),
            (UC_ARM_REG_PC, radio_rx.linked[ENTRY] | 1),
        ):
            b.uc.reg_write(reg, value)

        for _ in range(max_quanta):
            b.run(2048, 1024)
            if not emit_hooked and not b.fault:
                mem = bytes(b.uc.mem_read(arena, ARENA_CAP))
                for off in range(0, ARENA_CAP - RX_HDR_BYTES + 1, 4):
                    if mem[off:off + 4] == magic_bytes:
                        hdr_addr = arena + off
                        emit_pc = int.from_bytes(mem[off + 4:off + 8], "little")
                        if arena + 16 <= emit_pc < arena + 16 + len(rx_code):
                            b.uc.hook_add(UC_HOOK_CODE, on_emit, begin=emit_pc, end=emit_pc)
                            emit_hooked = True
                        break
            if radio_rx.returned or b.fault:
                break

        if b.fault:
            raise RuntimeError(f"ARM RX play dispatcher crashed: {b.fault}")
        if not radio_rx.returned:
            raise RuntimeError("ARM RX play dispatcher hung")
        if radio_rx.trace["radio_enter_tx"] != 0:
            raise RuntimeError(f"RX path entered TX: {radio_rx.trace}")
        if radio_rx.module_ret != [expected_return]:
            raise RuntimeError(f"ARM RX module returned {radio_rx.module_ret}, expected {[expected_return]}")
        if int.from_bytes(b.uc.mem_read(radio_rx.syms["ctx_app"], 4), "little") != 0:
            raise RuntimeError("Host leaked module context on return")
        if hashlib.sha256(b.flash_data[0x100000:0x100000 + len(cb)]).hexdigest() != cb_hash:
            raise RuntimeError("Virtual K6CB flash modified")
        if hdr_addr is None:
            raise RuntimeError("RX module did not initialize rx_hdr_t in scratch")

        vals = struct.unpack(RX_HDR_FMT, bytes(b.uc.mem_read(hdr_addr, RX_HDR_BYTES)))
        hdr = dict(
            magic=vals[0], emit_pc=vals[1], seq=vals[2],
            pcm_hash=vals[3], pcm_samples=vals[4], pwm_hash=vals[5],
            frames_decoded=vals[6], scratch_room=vals[7], scratch_used=vals[8],
            pool_peak=vals[9], pool_blocks_end=vals[10],
            stack_reserve=vals[11], stack_used=vals[12], cb_reads=vals[13],
        )
        pcm_tail = bytes(b.uc.mem_read(hdr_addr + RX_PCM_OFF, 320))
        pwm_tail = bytes(b.uc.mem_read(hdr_addr + RX_PWM_OFF, 320))
        fsk = radio_rx.rf.status()["fsk_rx"]
        if expect_rf_frames is not None:
            if (fsk["frames"] != expect_rf_frames or fsk["words"] != 33 * expect_rf_frames or
                    fsk["overruns"] or fsk["underflows"]):
                raise RuntimeError(f"Unexpected RX FSK FIFO stats: {fsk}")

        arm_pcm = np.concatenate(pcm_blocks) if pcm_blocks else np.empty(0, dtype="<i2")
        arm_pwm = np.concatenate(pwm_blocks) if pwm_blocks else np.empty(0, dtype="<u2")
        return dict(
            module_bytes=len(rx_code),
            envelope_bytes=envelope,
            cable_chunks=chunks,
            return_value=radio_rx.module_ret[0],
            trace=dict(radio_rx.trace),
            fsk=fsk,
            hdr=hdr,
            pcm=arm_pcm,
            pwm=arm_pwm,
            pcm_zeroed_on_exit=(pcm_tail == b"\x00" * 320),
            pwm_zeroed_on_exit=(pwm_tail == b"\x00" * 320),
        )
    finally:
        radio_rx.close()


def run(elf: Path, image: Path, tree: Path, blocks: int = 8, max_quanta: int = 90000,
        include_corruption: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="flux-sotto-arm-rxplay-") as td:
        temp = Path(td)
        sha, fw_bytes = matching_image(elf, image, temp)
        c_dir, fixed_dir = _sotto_dirs(tree, temp / "c-staging")
        rx_code, rx_entry = build_rx_play(tree, temp / "mod-staging")
        artifacts = prepare(tree, temp, pcm=mixed_pcm())
        tx = _run_tx_arm(elf, image, tree, artifacts, blocks=blocks, max_quanta=max_quanta)

        rx = _run_rx_arm(
            elf, image, artifacts, rx_code, rx_entry, tx["air"],
            expected_return=len(tx["rf_frames"]),
            expect_rf_frames=len(tx["rf_frames"]),
        )
        if rx["hdr"]["pool_blocks_end"] != 0:
            raise RuntimeError(f"Decoder pool leaked blocks: {rx['hdr']}")
        if not rx["pcm_zeroed_on_exit"] or not rx["pwm_zeroed_on_exit"]:
            raise RuntimeError("ARM RX module did not zero PCM/PWM buffers on exit")

        decoder_lib = native_decoder(tree, temp / "sotto-lr-decoder.so")
        ref_pcm = decode_pcm(decoder_lib, tx["codec"])
        pager_lib = pager_ref_lib(c_dir, fixed_dir, temp / "sotto-pager-ref.so")
        ref_pwm = reference_pwm(pager_lib, ref_pcm, top=2000, gain_q8=256, emphasis=1)

        if not np.array_equal(rx["pcm"], ref_pcm):
            raise RuntimeError(
                f"ARM PCM != host reference PCM: arm_len={len(rx['pcm'])} ref_len={len(ref_pcm)}"
            )
        if not np.array_equal(rx["pwm"], ref_pwm):
            raise RuntimeError(
                f"ARM PWM != c/k6/sotto_pager.c reference PWM: arm_len={len(rx['pwm'])} ref_len={len(ref_pwm)}"
            )
        if rx["hdr"]["pcm_hash"] != fnv1a_16(ref_pcm):
            raise RuntimeError("ARM pcm_hash mismatch against reference PCM")
        if rx["hdr"]["pwm_hash"] != fnv1a_16(ref_pwm):
            raise RuntimeError("ARM pwm_hash mismatch against reference PWM")

        corruption = {}
        if include_corruption:
            bad_crc = _run_rx_arm(
                elf, image, artifacts, rx_code, rx_entry,
                _clone_air(tx["air"], corrupt_idx=1),
                expected_return=-11,
            )
            assert bad_crc["hdr"]["pool_blocks_end"] == 0
            assert bad_crc["pcm_zeroed_on_exit"] and bad_crc["pwm_zeroed_on_exit"]

            missing_pkt = _run_rx_arm(
                elf, image, artifacts, rx_code, rx_entry,
                _clone_air(tx["air"], drop_idx=1),
                expected_return=-10,
            )
            assert missing_pkt["hdr"]["pool_blocks_end"] == 0
            assert missing_pkt["pcm_zeroed_on_exit"] and missing_pkt["pwm_zeroed_on_exit"]

            bad_cb = _run_rx_arm(
                elf, image, artifacts, rx_code, rx_entry,
                tx["air"], expected_return=-13, corrupt_cb=True,
            )
            assert bad_cb["trace"]["api31"] == 0
            assert bad_cb["hdr"]["pool_blocks_end"] == 0

            corruption = dict(
                bad_crc_return=bad_crc["return_value"],
                missing_packet_return=missing_pkt["return_value"],
                bad_codebook_return=bad_cb["return_value"],
                all_cleaned_up=True,
            )

        return dict(
            result="PASS",
            firmware_sha256=sha,
            firmware_bytes=fw_bytes,
            session_ms=blocks * 10,
            blocks=blocks,
            codec_frames=len(tx["codec"]) // 6,
            rf_packets=len(tx["rf_frames"]),
            real_arm_rx_decoder=True,
            host_decoder_used_for_arm_output=False,
            dma_speaker_validated=False,
            module_bytes=rx["module_bytes"],
            envelope_bytes=rx["envelope_bytes"],
            cable_chunks=rx["cable_chunks"],
            arena_cap=ARENA_CAP,
            scratch_room=rx["hdr"]["scratch_room"],
            scratch_used=rx["hdr"]["scratch_used"],
            pool_peak_bytes=rx["hdr"]["pool_peak"],
            pool_blocks_end=rx["hdr"]["pool_blocks_end"],
            stack_reserve_bytes=rx["hdr"]["stack_reserve"],
            stack_used_bytes=rx["hdr"]["stack_used"],
            cb_reads=rx["hdr"]["cb_reads"],
            pcm_samples=len(rx["pcm"]),
            pcm_sha256=hashlib.sha256(rx["pcm"].tobytes()).hexdigest(),
            pwm_sha256=hashlib.sha256(rx["pwm"].tobytes()).hexdigest(),
            pcm_exact_match=True,
            pwm_exact_match=True,
            pcm_hash=rx["hdr"]["pcm_hash"],
            pwm_hash=rx["hdr"]["pwm_hash"],
            corruption_tests=corruption,
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf", required=True, type=Path)
    p.add_argument("--image", required=True, type=Path)
    p.add_argument("--sotto-tree", required=True, type=Path)
    p.add_argument("--blocks", type=int, default=8)
    p.add_argument("--max-quanta", type=int, default=90000)
    p.add_argument("--no-corruption", action="store_true")
    p.add_argument("--json", type=Path)
    a = p.parse_args()
    out = run(
        a.elf.resolve(), a.image.resolve(), a.sotto_tree.resolve(),
        blocks=a.blocks, max_quanta=a.max_quanta,
        include_corruption=not a.no_corruption,
    )
    if a.json:
        a.json.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
