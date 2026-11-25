"""Evidence-backed, deliberately non-silicon-accurate K6 hardware behavior.

Unlike propagation/channel models, these observations belong on the radio
hardware side. No RF, microphone, or MCU hardware accessed here. Limited
bench cases are *descriptive*; untested configs are not extrapolated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import random

FIXTURE = Path(__file__).resolve().parents[3]/"tests/fixtures/k6_bchip_20261125_calibration.json"


def load_calibration(path: Path = FIXTURE) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != 1 or not data.get("measured"):
        raise ValueError("expected provenance-backed K6 physical fixture schema=1")
    tx = data.get("transmit")
    mic = data.get("microphone")
    if not isinstance(tx,list) or not isinstance(mic,list):
        raise ValueError("missing physical case lists")
    ids = [str(x.get("id")) for x in tx+mic]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate calibration case")
    for r in tx:
        n,g,b,m = (r[k] for k in ("expected_packets","crc_valid_delta","crc_rejected_delta",
                                   "not_crc_valid_or_rejected"))
        if not all(isinstance(v,int) for v in (n,g,b,m)) or not n>0 or min(g,b,m)<0 or n!=g+b+m:
            raise ValueError("invalid physical packet accounting")
    for r in mic:
        if not 70000<r["density_fs_hz"]<100000 or len(r["spectral_scores"])!=4:
            raise ValueError("invalid measured GP0 record")
    return data


@dataclass(frozen=True)
class TxCondition:
    """Physical PA/FIFO sequence, not a propagation channel condition."""
    pa_enabled_at_preload: bool
    settle_ms: float | None
    interframe_gap_ms: float | None
    rekey: bool
    receiver_mode: str

    def __post_init__(self):
        for v in (self.settle_ms,self.interframe_gap_ms):
            if v is not None and (not math.isfinite(v) or v < 0):
                raise ValueError("invalid PA settling or packet gap")

    @classmethod
    def from_case(cls, r: dict):
        return cls(r["pa_enabled_at_preload"],r["settle_ms"],
                   r["interframe_gap_ms"],r["rekey"],r["receiver_mode"])


class PhysicalTxCalibration:
    """Exact-condition empirical lookup with no magical TX/CRC predictions."""
    def __init__(self, rows=None):
        self.rows = tuple(rows if rows is not None else load_calibration()["transmit"])

    def assess(self, condition: TxCondition) -> dict:
        if not isinstance(condition,TxCondition):
            raise TypeError("TxCondition required")
        matches = [r for r in self.rows if TxCondition.from_case(r)==condition]
        good = sum(r["crc_valid_delta"] for r in matches)
        total = sum(r["expected_packets"] for r in matches)
        bad = sum(r["crc_rejected_delta"] for r in matches)
        complete = sum(r["complete_session"] for r in matches)
        count = len(matches)
        if not matches:
            verdict = "unobserved"
        elif condition.receiver_mode in {"fsk13_old_reader","fsk13_new_reader"}:
            verdict = "measurement_chain_only"
        elif count>=4 and complete==count and bad==0:
            verdict = "repeatably_observed"
        elif complete==0:
            verdict = "no_complete_sessions_observed"
        else:
            verdict = "mixed_or_sparse_evidence"
        return {
            "status": verdict, "cases": count,
            "complete_sessions": complete, "crc_valid_packets": good,
            "crc_rejected_packets": bad, "expected_packets": total,
            "unobserved_packets": total-good-bad,
            "observed_complete_fraction": (complete/count if count else None),
            "sources": [r["id"] for r in matches],
            "note": "Empirical condition match, NOT predicted silicon error rate or proof of untested timing.",
        }

    def leave_one_out(self, families=("direct_pa20_gap60","onradio_pa20_gap0")) -> list[dict]:
        """A visible coverage gate, not an inflated train/test accuracy claim."""
        output=[]
        for family in families:
            grp=[r for r in self.rows if r["family"]==family]
            if len(grp)<2:
                raise ValueError(f"not enough independent runs for {family}")
            train=PhysicalTxCalibration([r for r in self.rows if r["id"]!=grp[-1]["id"]])
            result=train.assess(TxCondition.from_case(grp[-1]))
            output.append({"held_out":grp[-1]["id"],"training_cases":result["cases"],
                           "train_status":result["status"],
                           "observed_complete":grp[-1]["complete_session"],
                           "matched_conditions_only":True})
        return output


@dataclass
class PaFifoTimeline:
    """Observe explicit PA, FIFO-preload and packet-start events in simulation.

    The virtual RFIC does not implement the actual PA control registers yet;
    the caller MUST explicitly feed PA transitions, never infer PA state
    from a merely enabled FSK register. No RF is altered by this observer.
    """
    receiver_mode: str = "direct_fsk"
    pa_on: bool = False
    pa_enabled_at: float | None = None
    preloaded_at: float | None = None
    pa_at_preload: bool | None = None
    last_packet_start: float | None = None
    rekeyed: bool = False
    packet_starts: list = field(default_factory=list)

    def _clock(self,t):
        if not math.isfinite(t) or t<0:
            raise ValueError("invalid simulation time")
        if self.packet_starts and t < self.packet_starts[-1]["time_s"]:
            raise ValueError("nonmonotonic packet event")

    def note_pa(self, enabled: bool, t: float):
        self._clock(t)
        if enabled and not self.pa_on:
            self.pa_on=True
            self.pa_enabled_at=t
            if self.packet_starts:self.rekeyed=True
        elif not enabled and self.pa_on:
            self.pa_on=False
            self.pa_enabled_at=None
            if self.packet_starts:self.rekeyed=True

    def note_fifo_preload(self,t:float):
        self._clock(t)
        if self.preloaded_at is None:
            self.pa_at_preload=self.pa_on
            self.preloaded_at=t

    def note_tx_start(self,t:float, model:PhysicalTxCalibration, interframe_gap_ms=None,
                      configured_settle_ms=None) -> dict:
        self._clock(t)
        settled = (None if not self.pa_on or self.pa_enabled_at is None
                   else round((t-self.pa_enabled_at)*1000,6))
        if settled is not None and settled < 0:
            raise ValueError("packet begins before PA enable")
        gap = interframe_gap_ms
        if gap is None and self.last_packet_start is not None:
            # Packet-start spacing is NOT inter-packet silence. Requiring a
            # separate explicit gap avoids inventing an RF timing observation.
            gap = None
        # The experiment's configured guard is not necessarily the actual
        # time the PA has been enabled; keep these quantities separate.
        configured = settled if configured_settle_ms is None else configured_settle_ms
        cond=TxCondition(bool(self.pa_at_preload),configured,gap,self.rekeyed,self.receiver_mode)
        report=model.assess(cond)
        # A claimed 20ms bench guard cannot make a 1ms physical PA warm-up
        # "qualified" merely by supplying favorable metadata. Validate that
        # the explicit virtual PA event timeline agrees with that claim.
        if (configured_settle_ms is not None and settled is not None
                and abs(settled-configured_settle_ms)>0.5):
            report["status"]="timing_claim_mismatch"
            report["note"]=("Reported configured guard does not match actual simulated PA-on "
                            "interval; refusing measured-profile qualification")
        report.update({"time_s":t,"condition":{
            "pa_enabled_at_preload":cond.pa_enabled_at_preload,"settle_ms":cond.settle_ms,
            "interframe_gap_ms":cond.interframe_gap_ms,"rekey":cond.rekey,
            "receiver_mode":cond.receiver_mode},
            "pa_enabled_at_tx_start":self.pa_on,"actual_pa_on_time_ms":settled})
        self.packet_starts.append(report)
        self.last_packet_start=t
        self.preloaded_at=None
        self.pa_at_preload=None
        return report


class Gp0MicCalibration:
    """Observed-only tone/DMA statistics, not a synthetic microphone waveform."""
    def __init__(self,rows=None):
        self.rows=tuple(rows if rows is not None else load_calibration()["microphone"])
        keys=[(x["measurement"],x["stimulus"]) for x in self.rows]
        if len(set(keys))!=len(keys):
            raise ValueError("duplicate measured microphone condition")

    def evidence(self, measurement:str,stimulus:str) -> dict:
        matches=[r for r in self.rows if r["measurement"]==measurement and r["stimulus"]==stimulus]
        if len(matches)!=1:
            raise ValueError("unmeasured microphone condition; no interpolation")
        r=matches[0]
        windows=r["spectral_scores"]
        return {"source":r["source"],"sha256":r["sha256"],"measurement":measurement,
                "stimulus":stimulus,"window_count":len(windows),
                "vox_max":r["vox_max"],"density_fs_hz":r["density_fs_hz"],
                "raw_dma_hz":r["inferred_raw_dma_hz"],
                "average_1khz_score":sum(x["tone_1khz"] for x in windows)/len(windows),
                "average_2khz_score":sum(x["tone_2khz"] for x in windows)/len(windows),
                "average_rms":sum(x["rms"] for x in windows)/len(windows),
                "status":"measured_narrowband_summary_only",
                "note":r["frequency_response_status"]}

    def sample_observed_window(self, measurement, stimulus,seed=0) -> dict:
        """Empirical bootstrap of recorded summaries; NEVER a reconstructed WAV."""
        self.evidence(measurement,stimulus)  # validates coverage
        r=next(x for x in self.rows if x["measurement"]==measurement and x["stimulus"]==stimulus)
        return {"kind":"observed_window_resampling", "source":r["source"],
                "sample":dict(random.Random(seed).choice(r["spectral_scores"]))}

    def rate_range(self,measurement="pcm"):
        rates=[x["inferred_raw_dma_hz"] for x in self.rows if x["measurement"]==measurement]
        if not rates:raise ValueError("no observed raw DMA rates")
        return (min(rates),max(rates))

    def dma_wrap_us(self,bytes_in_ring=1024,measurement="pcm") -> dict:
        if not isinstance(bytes_in_ring,int) or bytes_in_ring <=0:
            raise ValueError("nonempty DMA ring length required")
        lo,hi=self.rate_range(measurement)
        return {"ring_bytes":bytes_in_ring,"measurement":measurement,
                "fastest_wrap_us":bytes_in_ring/hi*1e6,
                "slowest_wrap_us":bytes_in_ring/lo*1e6,
                "sample_count":len([x for x in self.rows if x["measurement"]==measurement]),
                "warning":"Observed conditions only; no CPU contention or decoder timing modeled."}
