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


class SottoRxPaced:
    """Decode an RX packet at speaker demand, not at RF packet arrival.

    Holds at most ``capacity_frames`` six-byte encoded frames plus a four
    20-ms PCM block ring.  Use enqueue_packet() only after the independent
    physical packet's CRC and FDV1 session validation.  end() is ONLY for a
    verified END; abort() drops both queues on key/cancel/corruption.  This
    host-side memory model intentionally does not promise a K6 CPU deadline.
    """

    def __init__(self, decoder, *, capacity_frames=16, max_frames=1152):
        if (not isinstance(capacity_frames, int) or isinstance(capacity_frames, bool)
                or not 8 <= capacity_frames <= 128):
            raise ValueError("compressed RX ring must hold at least one full packet")
        self.decoder = SottoRxStream(decoder, capacity_blocks=4,
                                     max_frames=max_frames)
        self.frames = deque()
        self.capacity_frames = capacity_frames
        self.peak_encoded_frames = 0
        self.accepted_frames = 0
        self.state = "rx"

    def abort(self):
        self.frames.clear()
        self.decoder.abort()
        self.state = "aborted"

    def close(self):
        self.abort()

    @property
    def queued_encoded_frames(self):
        return len(self.frames)

    @property
    def peak_pcm_blocks(self):
        return self.decoder.peak_blocks

    @property
    def queued_pcm_blocks(self):
        return self.decoder.queued_blocks

    def enqueue_packet(self, packet: bytes):
        if self.state != "rx":
            raise RuntimeError("Sotto compressed RX closed")
        if not isinstance(packet, bytes) or not packet or len(packet) % CODEC_BYTES or len(packet) > 48:
            self.abort()
            raise ValueError("Sotto RX packet must carry 1–8 validated six-byte frames")
        count = len(packet) // CODEC_BYTES
        if (len(self.frames) + count > self.capacity_frames
                or self.accepted_frames + count > self.decoder.max_frames):
            self.abort()
            raise BufferError("Sotto compressed RX ring overrun or session limit")
        self.frames.extend(packet[i:i+CODEC_BYTES]
                           for i in range(0, len(packet), CODEC_BYTES))
        self.accepted_frames += count
        self.peak_encoded_frames = max(self.peak_encoded_frames, len(self.frames))

    def end(self):
        if self.state != "rx":
            raise RuntimeError("Sotto RX session already ended")
        if self.accepted_frames < DECODER_DELAY:
            self.abort()
            raise ValueError("Sotto received END before decoder priming")
        self.state = "end_received"

    def speaker_next(self):
        """Advance a virtual speaker 20 ms; None means no PCM is available.

        A device scheduler must call this at its real audio cadence, check
        underruns and budget decoder CPU time. Never synthesize a silent
        sample from missing data and call it successful reception.
        """
        if self.state == "aborted":
            return None
        block = self.decoder.speaker_next()
        if block is not None:
            return block
        while self.frames:
            try:
                self.decoder.feed(self.frames.popleft())
            except Exception:
                self.abort()
                raise
            block = self.decoder.speaker_next()
            if block is not None:
                return block
        if self.state == "end_received":
            try:
                self.decoder.finish()
            except Exception:
                self.abort()
                raise
            self.state = "finished"
            return self.decoder.speaker_next()
        return None
