#!/usr/bin/env python3
"""Offline packet/goodput/jitter model for repeated finite native-FSK frames.

This is a capacity model, not an IQ receiver or proof of real-time voice. It
keeps the measured 46.719 ms frame-restart interval separate from payload,
adds explicit packet framing and optional rate-1/2 repetition protection, and
simulates delivery/loss into a fixed-delay speech playout buffer.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass

BAUD = 4800
FIFO_BITS = 64 * 16
RESTART_S = 0.046719
SOTTO = {"1200": (6, 0.040), "600": (6, 0.080), "400": (6, 0.120)}
HEADER_BYTES = 4  # version/codec, sequence, payload length
CRC_BYTES = 2


@dataclass(frozen=True)
class PacketPlan:
    codec: str
    frames_per_packet: int
    fec_rate_num: int = 1
    fec_rate_den: int = 1

    @property
    def frame_bytes(self):
        return SOTTO[self.codec][0]

    @property
    def payload_bytes(self):
        return self.frames_per_packet * self.frame_bytes

    @property
    def protected_bytes(self):
        # Symbol repetition is deliberately simple and conservative. It is a
        # capacity bound, not a claimed RF decoder/FEC implementation.
        return math.ceil((HEADER_BYTES + self.payload_bytes + CRC_BYTES)
                         * self.fec_rate_den / self.fec_rate_num)

    @property
    def fits(self):
        return self.protected_bytes * 8 <= FIFO_BITS

    @property
    def speech_s(self):
        return self.frames_per_packet * SOTTO[self.codec][1]

    @property
    def packet_air_s(self):
        return self.protected_bytes * 8 / BAUD + RESTART_S

    @property
    def goodput_bps(self):
        return self.payload_bytes * 8 / self.packet_air_s


def simulate(plan: PacketPlan, *, packets=500, loss=0.0, jitter_s=0.0,
             playout_delay_s=0.600, seed=20261123) -> dict:
    if not plan.fits:
        raise ValueError("protected packet does not fit the 64-word FIFO frame")
    if packets < 1 or not 0 <= loss < 1 or jitter_s < 0 or playout_delay_s < 0:
        raise ValueError("invalid packet count, loss, jitter or playout delay")
    rng = random.Random(seed)
    arrival = 0.0
    delivered = []
    arrivals = []
    lost = 0
    # Source emits fixed-size groups of codec frames continuously. Each new
    # packet arrives only after payload transmission and measured restart gap.
    for seq in range(packets):
        arrival += plan.packet_air_s
        arrivals.append(arrival)
        if rng.random() < loss:
            lost += 1
            continue
        delivered.append((seq, max(0.0, arrival + rng.uniform(-jitter_s, jitter_s))))

    # Packets contain consecutive audio frames. A packet is useful only if it
    # reaches the receiver before its first frame's fixed playout deadline;
    # late/lost packets become an interruption (PLC is not modeled as speech).
    received = {seq: t for seq, t in delivered}
    frame_s = SOTTO[plan.codec][1]
    deadline_misses = 0
    late_packets = 0
    missing_audio_s = 0.0
    for seq in range(packets):
        due = playout_delay_s + seq * plan.speech_s
        t = received.get(seq)
        if t is None or t > due:
            deadline_misses += 1
            missing_audio_s += plan.speech_s
            if t is not None:
                late_packets += 1

    gaps = [arrivals[i] - arrivals[i - 1] for i in range(1, len(arrivals))]
    return {
        "offline_model_only": True,
        "physical_rf_evidence": False,
        "codec": plan.codec,
        "codec_frame_ms": round(frame_s * 1000, 3),
        "frames_per_packet": plan.frames_per_packet,
        "payload_bytes": plan.payload_bytes,
        "protected_bytes_on_air": plan.protected_bytes,
        "protection_rate": f"{plan.fec_rate_num}/{plan.fec_rate_den}",
        "fifo_payload_bits": FIFO_BITS,
        "fifo_fit": plan.fits,
        "native_baud_bits_per_s": BAUD,
        "measured_restart_gap_ms": RESTART_S * 1000,
        "modeled_packet_air_ms": round(plan.packet_air_s * 1000, 3),
        "modeled_application_goodput_bps": round(plan.goodput_bps, 2),
        "source_codec_rate_bps": int(plan.frame_bytes * 8 / frame_s),
        "modeled_sustains_codec_rate": plan.goodput_bps >= plan.frame_bytes * 8 / frame_s,
        "packets": packets,
        "packet_loss_probability": loss,
        "jitter_bound_ms": jitter_s * 1000,
        "playout_delay_ms": playout_delay_s * 1000,
        "observed_simulated_packet_losses": lost,
        "late_packet_deadline_misses": late_packets,
        "total_packet_deadline_misses": deadline_misses,
        "modeled_missing_audio_ms": round(missing_audio_s * 1000, 3),
        "maximum_packet_interval_ms": round(max(gaps, default=0) * 1000, 3),
        "limitations": [
            "Restart interval is the observed two-frame measurement, not a sustained distribution.",
            "No new-payload native-FSK RF packet has yet been decoded by an independent receiver.",
            "FEC is an idealized repetition capacity bound; decoder performance is not modeled.",
            "Fixed-delay deadline model omits codec packet-loss concealment and audio hardware.",
        ],
    }


def sweep() -> list[dict]:
    rows = []
    for codec in ("1200", "600", "400"):
        for frames in (1, 2, 4, 8, 10, 16):
            for numerator, denominator in ((1, 1), (1, 2)):
                plan = PacketPlan(codec, frames, numerator, denominator)
                if plan.fits:
                    rows.append(simulate(plan, loss=.05, jitter_s=.150,
                                         playout_delay_s=.600))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="write a new JSON report; refuses overwrite")
    ap.add_argument("--packets", type=int, default=500)
    ap.add_argument("--loss", type=float, default=.05)
    ap.add_argument("--jitter-ms", type=float, default=150)
    ap.add_argument("--playout-ms", type=float, default=600)
    ap.add_argument("--seed", type=int, default=20261123)
    args = ap.parse_args()
    report = {
        "project": "Flux",
        "experiment": "FSK-10A finite-frame digital-voice capacity and jitter model",
        "assumptions": {"frame_bits": FIFO_BITS, "native_baud": BAUD,
                        "restart_gap_ms": RESTART_S * 1000,
                        "model_note": "capacity/jitter model, not physical RF"},
        "sweep_at_5pct_loss_150ms_jitter": sweep(),
        "selected_run": simulate(PacketPlan("1200", 8, 1, 2),
                                  packets=args.packets, loss=args.loss,
                                  jitter_s=args.jitter_ms / 1000,
                                  playout_delay_s=args.playout_ms / 1000,
                                  seed=args.seed),
    }
    output = json.dumps(report, indent=2) + "\n"
    if args.out:
        from pathlib import Path
        path = Path(args.out)
        if path.exists():
            ap.error(f"refusing to overwrite existing report: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output)
    print(json.dumps({"sweep_rows": len(report["sweep_at_5pct_loss_150ms_jitter"]),
                      "selected_run": report["selected_run"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
