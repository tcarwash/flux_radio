/* Flux bounded 1-to-8-frame / 33-word guarded Sotto segment queue.
 *
 * LIVE-COMPATIBLE SCHEDULING PRIMITIVE, NOT A RADIO TRANSMITTER.
 * Caller supplies COMPLETE, PRE-ENCODED 6-byte Sotto-1200 frames (e.g.
 * actual slim_encode10 completions, 8kHz/40ms). The caller's main loop,
 * not an interrupt context, owns this object and calls each function.
 * No allocations, SPI, PA11 GPIO, PTT, registers, ticks, IRQ or sleeps.
 *
 * Each 320ms speech segment is a new msg_id, packet seq 0, depth 1,
 * total 8 (or 1..7 after final flush). The existing Flux L64 CRC,
 * whitening and sacrificial guard are built by dv_guard33_packet.h.
 *
 * Do not wrap msg_id within a call: at start ID 112, at most 144
 * complete/partial segments fit (~46s). Higher layer must signal
 * session start/end, loss, and message-ID rollover with a separately
 * specified control protocol; those wire protocols are not invented here.
 *
 * Two 66-byte FIFO packet buffers plus one 48-byte partial Sotto segment.
 * This QUEUE DOES NOT MAKE AN UNRUNNABLE 200-turn PA11 sampler real-time.
 * If push/finish returns BACKPRESSURE, the caller must drain or declare
 * a missed-frame/overrun; NEVER silently discard the mic frame.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_K6_DV_GUARD33_STREAM_H
#define FLUX_K6_DV_GUARD33_STREAM_H
#include <stdint.h>
#include "dv_guard33_packet.h"

#define DV_STREAM_QUEUE_SLOTS 2u
#define DV_STREAM_OK 0
#define DV_STREAM_COMMITTED 1
#define DV_STREAM_BAD_INPUT -1
#define DV_STREAM_BACKPRESSURE -2
#define DV_STREAM_MSG_WRAP -3
#define DV_STREAM_ENDED -4

typedef struct dv_guard33_stream {
    uint16_t ring[DV_STREAM_QUEUE_SLOTS][DV_GUARD33_WORDS];
    uint8_t partial[DV_GUARD33_MAX_SUPERFRAMES * DV_GUARD33_SUPERFRAME_BYTES];
    uint16_t next_msg_id; /* 1..255; 256 is exhausted, not wrapped */
    uint16_t segments;    /* for telemetry; bounded to <=255 */
    uint8_t filled;       /* 0..2 committed guarded packets */
    uint8_t read_slot;    /* 0..1 */
    uint8_t write_slot;   /* 0..1 */
    uint8_t frames;       /* 0..7 after every successful call */
    uint8_t ended;
} dv_guard33_stream_t;

/* Initialize existing preallocated, caller-owned RAM. */
static inline int dv_stream_begin(dv_guard33_stream_t *s, unsigned start_id) {
    unsigned j;
    if (!s || start_id < 1u || start_id > 255u) return DV_STREAM_BAD_INPUT;
    for (j = 0u; j < sizeof(*s); ++j)
        ((uint8_t*)s)[j] = 0u;
    s->next_msg_id = (uint16_t)start_id;
    return DV_STREAM_OK;
}

/* Called only if queue capacity and non-wrapping msg_id are assured.
 * We never consume the last frame of a 320ms segment if the packet
 * queue is full, so the caller can reliably retry it after drain. */
static inline int dv_stream_push(dv_guard33_stream_t *s, const uint8_t six[6]) {
    unsigned j;
    if (!s || !six) return DV_STREAM_BAD_INPUT;
    if (s->ended) return DV_STREAM_ENDED;
    if (s->next_msg_id > 255u) return DV_STREAM_MSG_WRAP;
    if (s->frames > 7u || s->filled > DV_STREAM_QUEUE_SLOTS)
        return DV_STREAM_BAD_INPUT;
    if (s->frames == 7u && s->filled == DV_STREAM_QUEUE_SLOTS)
        return DV_STREAM_BACKPRESSURE;
    for (j = 0u; j < 6u; ++j)
        s->partial[(unsigned)s->frames * 6u + j] = six[j];
    ++s->frames;
    if (s->frames == 8u) {
        if (dv_guard33_build_words(s->ring[s->write_slot],
                                   s->partial,8u,(uint8_t)s->next_msg_id)
             != DV_GUARD33_WORDS)
            return DV_STREAM_BAD_INPUT;
        s->write_slot = (uint8_t)((s->write_slot+1u)%DV_STREAM_QUEUE_SLOTS);
        ++s->filled;
        ++s->next_msg_id;
        ++s->segments;
        s->frames=0u;
        return DV_STREAM_COMMITTED;
    }
    return DV_STREAM_OK;
}

/* At PTT release, seal partial (1..7) with total field equal to the
 * real number of encoded 40ms frames, not padded eight. For a complete
 * segment boundary, simply finish the session; a higher protocol still
 * needs an explicit end marker, including the exact-8-frame case.
 * If BACKPRESSURE, do not discard pending partial frames; drain and
 * retry finish. */
static inline int dv_stream_finish(dv_guard33_stream_t *s) {
    if (!s) return DV_STREAM_BAD_INPUT;
    if (s->ended) return DV_STREAM_ENDED;
    if (s->frames) {
        if (s->next_msg_id > 255u) return DV_STREAM_MSG_WRAP;
        if (s->filled == DV_STREAM_QUEUE_SLOTS) return DV_STREAM_BACKPRESSURE;
        if (dv_guard33_build_words(s->ring[s->write_slot],
                                   s->partial,s->frames,(uint8_t)s->next_msg_id)
             != DV_GUARD33_WORDS)
            return DV_STREAM_BAD_INPUT;
        s->write_slot = (uint8_t)((s->write_slot+1u)%DV_STREAM_QUEUE_SLOTS);
        ++s->filled;
        ++s->next_msg_id;
        ++s->segments;
        s->frames = 0u;
    }
    s->ended = 1u;
    return DV_STREAM_OK;
}

/* Read only; valid until dv_stream_ack() advances the queue. */
static inline const uint16_t *dv_stream_peek(const dv_guard33_stream_t *s) {
    return (s && s->filled) ? s->ring[s->read_slot] : (const uint16_t *)0;
}

/* Caller may acknowledge ONLY after the radio has completed a
 * bounded guarded RF physical frame. A failed/uncertain frame should
 * abort the session and record an erasure, not silently ACK it. */
static inline int dv_stream_ack(dv_guard33_stream_t *s) {
    if (!s || !s->filled) return DV_STREAM_BAD_INPUT;
    s->read_slot = (uint8_t)((s->read_slot+1u)%DV_STREAM_QUEUE_SLOTS);
    --s->filled;
    return DV_STREAM_OK;
}
#endif /* FLUX_K6_DV_GUARD33_STREAM_H */
