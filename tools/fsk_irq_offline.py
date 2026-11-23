"""Offline-only hypotheses for the Flux FD6818B native-FSK FIFO.

This is NOT a silicon-level emulator. It models one possible documented IRQ
representation and logical FIFO scheduling. It cannot establish whether
FD6818B implements the older FD6818 threshold, IRQ, and refill semantics.
No hardware, serial, SDR or network interfaces are used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json

IRQ_TX_FIFO_ALMOST_EMPTY_EVENT = 14
IRQ_TX_FIFO_ALMOST_EMPTY_ENABLE = 0x4000
WORD_BITS = 16
BAUD = 4800
CAPACITY_WORDS = 64
THRESHOLD_WORDS = 24


def fsk09_original_irq_predicate(reg02: int) -> bool:
    """Predicate from c/k6/mod/fsk_refill.c; tested as-is."""
    return bool(reg02 & IRQ_TX_FIFO_ALMOST_EMPTY_ENABLE)


def fd6818_event_irq_predicate(reg02: int) -> bool:
    """Candidate fix IF the B-chip uses older FD6818's encoded event format."""
    return (reg02 & 0x000F) == IRQ_TX_FIFO_ALMOST_EMPTY_EVENT


@dataclass
class SimulationResult:
    outcome: str
    time_ms: int
    words_consumed: int
    words_loaded: int
    maximum_occupancy: int
    appended_batches: list[int]
    append_times_ms: list[int]
    irq_observations: list[str]
    words_match: bool
    notes: str


def simulate(
    *,
    decoder: str = "event",
    irq_source: str = "event",
    service_delay_ms: int = 0,
    baud: int = BAUD,
    capacity_words: int = CAPACITY_WORDS,
    threshold_words: int = THRESHOLD_WORDS,
    frame_words: int = 128,
    initial_words: int = 48,
    batches: tuple[int, ...] = (32, 32, 16),
    lower_bounds_ms: tuple[int, ...] = (65, 170, 280),
    deadlines_ms: tuple[int, ...] = (230, 350, 490),
    end_ms: int = 1000,
) -> SimulationResult:
    """Simulate a *hypothetical* latched threshold interrupt and word FIFO.

    Times are integer milliseconds. A word takes 16/baud seconds; this
    deliberately ignores physical preamble/sync and SPI write timing.
    The written sequence is deterministic; checking words_match checks
    FIFO ordering in the model, not decoded RF.
    """
    if decoder not in ("event", "original"):
        raise ValueError("decoder")
    if irq_source not in ("event", "bit", "none", "observed_0800"):
        raise ValueError("irq_source")
    if frame_words < 1 or initial_words < 1 or baud <= 0:
        raise ValueError("invalid length/rate")
    if capacity_words < initial_words or threshold_words >= capacity_words:
        raise ValueError("invalid capacity/threshold")
    if len(batches) != len(lower_bounds_ms) or len(batches) != len(deadlines_ms):
        raise ValueError("batch schedule")
    queued = list(range(initial_words))
    consumed = []
    max_occupancy = len(queued)
    pending = False
    event_armed = len(queued) > threshold_words
    current_batch = 0
    candidate_since = None
    append_times = []
    append_sizes = []
    irq_samples = []
    observed_set = set()

    def done(outcome: str, t: int, note: str) -> SimulationResult:
        return SimulationResult(
            outcome=outcome, time_ms=t, words_consumed=len(consumed),
            words_loaded=len(queued), maximum_occupancy=max_occupancy,
            appended_batches=append_sizes, append_times_ms=append_times,
            irq_observations=irq_samples, words_match=(
                consumed == list(range(len(consumed)))
            ), notes=note,
        )

    for t in range(end_ms + 1):
        ideal_consumed = min(frame_words, (t * baud) // (WORD_BITS * 1000))
        if ideal_consumed > len(queued):
            return done("UNDERFLOW", t,
                        "Model FIFO ran out before all declared frame words.")
        while len(consumed) < ideal_consumed:
            consumed.append(queued[len(consumed)])
        occupancy = len(queued) - len(consumed)
        if len(consumed) == frame_words:
            return done("COMPLETE", t,
                        "Reached finite programmed length, NOT proven unbounded streaming.")
        if occupancy == 0:
            return done("UNDERFLOW", t,
                        "FIFO empty before declared frame end; refill at zero is not safe.")
        if occupancy > threshold_words:
            event_armed = True
        elif event_armed and irq_source in ("event", "bit"):
            pending = True
            event_armed = False

        if irq_source == "observed_0800":
            raw = 0x0800
        elif pending:
            raw = 0x000E if irq_source == "event" else 0x4000
        else:
            raw = 0
        if raw and raw not in observed_set:
            observed_set.add(raw)
            irq_samples.append(f"0x{raw:04x}")
        predicate = (fd6818_event_irq_predicate if decoder == "event"
                     else fsk09_original_irq_predicate)
        if current_batch < len(batches):
            if t > deadlines_ms[current_batch]:
                return done("TIMEOUT", t, "No acceptable refill by deadline.")
            if predicate(raw) and t >= lower_bounds_ms[current_batch]:
                if candidate_since is None:
                    candidate_since = t
                if t >= candidate_since + service_delay_ms:
                    count = batches[current_batch]
                    if len(queued) + count > frame_words:
                        return done("FRAME_LENGTH_EXCEEDED", t,
                                    "Refill exceeds declared finite frame length.")
                    if occupancy + count > capacity_words:
                        return done("FIFO_OVERFLOW", t,
                                    "Refill exceeds model FIFO capacity.")
                    queued.extend(range(len(queued), len(queued) + count))
                    max_occupancy = max(max_occupancy, occupancy + count)
                    append_times.append(t)
                    append_sizes.append(count)
                    pending = False
                    current_batch += 1
                    candidate_since = None
        # The idle time after final append is legitimate until frame completion.
    return done("INCOMPLETE", end_ms, "End of simulation without frame completion.")


def main() -> None:
    p = argparse.ArgumentParser(description="Offline hypothetical IRQ/FIFO model")
    p.add_argument("--decoder", choices=("event", "original"), default="event")
    p.add_argument("--irq-source", choices=("event", "bit", "none", "observed_0800"),
                   default="event")
    p.add_argument("--service-delay-ms", type=int, default=0)
    opts = p.parse_args()
    print(json.dumps(asdict(simulate(decoder=opts.decoder,
                                     irq_source=opts.irq_source,
                                     service_delay_ms=opts.service_delay_ms)),
                     indent=2))


if __name__ == "__main__":
    main()
