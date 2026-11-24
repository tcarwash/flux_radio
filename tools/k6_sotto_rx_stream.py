"""Bounded incremental Sotto-1200 native speech decode and 20-ms playout.

Offline receiver-side adapter: feed only independently CRC/FDV1-validated
six-byte Sotto codec frames. This uses the actual pinned fixed-point C decoder,
not synthesized reference audio. Each input consumes one 40-ms codec frame.
The first two decoder windows are priming; a clean session finish flushes the
two delayed frames. No entire-speech buffer is retained.

This is a host-side prototype of the proposed on-K6 service, not a firmware
driver, RF receiver, speaker HAL, or proof of real-time scheduling.
"""
from __future__ import annotations

import ctypes as C
from collections import deque

import numpy as np

SAMPLES_PER_FRAME = 320
SAMPLES_PER_BLOCK = 160
CODEC_BYTES = 6
DECODER_DELAY = 2


class SottoRxStream:
    """One Sotto session, caller-driven 20-ms playout, fail-closed overflow."""

    def __init__(self, decoder, *, capacity_blocks: int = 16,
                 max_frames: int = 1152):
        if (not isinstance(capacity_blocks, int) or isinstance(capacity_blocks, bool)
                or not 4 <= capacity_blocks <= 128
                or not isinstance(max_frames, int) or isinstance(max_frames, bool)
                or not 2 <= max_frames <= 1152):
            raise ValueError("invalid bounded playout configuration")
        self.decoder = decoder
        self.ctx = decoder.sotto_lr_dec_create()
        if not self.ctx:
            raise RuntimeError("native Sotto decoder state allocation failed")
        self.buf = (C.c_int16 * SAMPLES_PER_FRAME)()
        self.queue = deque()
        self.capacity_blocks = capacity_blocks
        self.max_frames = max_frames
        self.frames = 0
        self.output_blocks = 0
        self.played_blocks = 0
        self.peak_blocks = 0
        self.state = "rx"

    def _destroy(self):
        if self.ctx:
            self.decoder.sotto_lr_dec_destroy(self.ctx)
            self.ctx = None

    def abort(self):
        """PTT or corruption: free codec state and drop all queued speech."""
        self._destroy()
        self.queue.clear()
        self.state = "aborted"

    def close(self):
        self.abort()

    def _offer(self):
        audio = np.asarray(self.buf[:], dtype="<i2")
        self.queue.append(audio[:SAMPLES_PER_BLOCK].copy())
        self.queue.append(audio[SAMPLES_PER_BLOCK:].copy())
        self.output_blocks += 2
        self.peak_blocks = max(self.peak_blocks, len(self.queue))

    def feed(self, six: bytes):
        if self.state != "rx":
            raise RuntimeError("Sotto decoder not accepting RX frames")
        if not isinstance(six, bytes) or len(six) != CODEC_BYTES:
            self.abort()
            raise ValueError("Sotto frame must be exactly six validated bytes")
        if self.frames >= self.max_frames or (
                self.frames >= DECODER_DELAY
                and len(self.queue) + 2 > self.capacity_blocks):
            self.abort()
            raise BufferError("Sotto RX playout queue overrun or session limit")
        word = (C.c_uint8 * CODEC_BYTES).from_buffer_copy(six)
        self.decoder.sotto_lr_decode(self.ctx, word, 1, self.buf)
        if self.frames >= DECODER_DELAY:
            self._offer()
        self.frames += 1

    def finish(self):
        """Call only on valid END, never on CRC/epoch failure or PTT."""
        if self.state != "rx":
            raise RuntimeError("Sotto RX session not active")
        if self.frames < DECODER_DELAY or len(self.queue) + 4 > self.capacity_blocks:
            self.abort()
            raise BufferError("Sotto incomplete session or playout ring too full")
        try:
            for _ in range(DECODER_DELAY):
                if self.decoder.sotto_lr_dec_flush(self.ctx, self.buf) != 1:
                    raise RuntimeError("Sotto decoder flush missing latency tail")
                self._offer()
        except Exception:
            self.abort()
            raise
        self._destroy()
        self.state = "finished"
        if self.output_blocks != self.frames * 2:
            self.abort()
            raise RuntimeError("Sotto receiver lost PCM duration")

    def speaker_next(self):
        """One 20-ms PCM block, or None while waiting for more validated RX."""
        if self.state == "aborted":
            return None
        if not self.queue:
            return None
        self.played_blocks += 1
        return self.queue.popleft()

    @property
    def queued_blocks(self):
        return len(self.queue)
