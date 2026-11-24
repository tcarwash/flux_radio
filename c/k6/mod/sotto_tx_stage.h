/* Staged Sotto TX modem core for loadable Flux modules.
 *
 * Caller-owned, bounded scratch memory, no persistent writable globals.
 * Record compressed 6-byte superframes with the PA OFF, seal the recording
 * only after the capture and codec complete without loss, then issue a single
 * guarded 66-byte packet at a time. The driver is responsible for PA/PTT,
 * independent watchdog, hardware restore and TX completion observation.
 *
 * FDV1 control packets are supported for the new receiver. For the first
 * staged RF test the receiver must use that same explicit format; this is
 * not the legacy multi-packet pager message format.
 *
 * No hardware access, flash writes or automatic RF action here.
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_SOTTO_TX_STAGE_H
#define FLUX_SOTTO_TX_STAGE_H
#include <stdint.h>
#include "dv_guard33_session.h"

/* 80 x 40 ms = 3.2 seconds. Allow compile-time reduction for smaller arenas.
 * 10 voice packets + BEGIN + END; one 33-word FIFO buffer.
 */
#ifndef SOTTO_STAGE_MAX_FRAMES
#define SOTTO_STAGE_MAX_FRAMES 80u
#endif
#if SOTTO_STAGE_MAX_FRAMES < 1 || SOTTO_STAGE_MAX_FRAMES > 80
#error "Invalid bounded recording size"
#endif

enum {
    SOTTO_STAGE_IDLE=0, SOTTO_STAGE_RECORD=1, SOTTO_STAGE_SEALED=2,
    SOTTO_STAGE_SEND=3, SOTTO_STAGE_DONE=4, SOTTO_STAGE_FAILED=5
};
typedef struct {
    uint32_t tag_hi,tag_lo;
    uint16_t words[33];
    uint8_t codec[SOTTO_STAGE_MAX_FRAMES*6u];
    uint8_t frames,first_id,phase,next_packet,prepared;
} sotto_stage_t;

static inline void sotto_stage_abort(sotto_stage_t *s) {
    if (s) { s->phase=SOTTO_STAGE_FAILED; s->prepared=0u; }
}
static inline int sotto_stage_begin(sotto_stage_t *s,unsigned first_id,
                                    uint32_t hi,uint32_t lo) {
    unsigned i;
    if (!s) return -1;
    for (i=0u;i<sizeof(*s);++i) ((uint8_t*)s)[i]=0u;
    if (!first_id || first_id>255u || !(hi|lo) ||
        first_id+(SOTTO_STAGE_MAX_FRAMES+7u)/8u-1u>255u) {
        sotto_stage_abort(s);return -1;
    }
    s->tag_hi=hi;s->tag_lo=lo;s->first_id=(uint8_t)first_id;
    s->phase=SOTTO_STAGE_RECORD;
    return 0;
}
static inline int sotto_stage_record(sotto_stage_t *s,const uint8_t six[6]) {
    unsigned i;
    if (!s || !six || s->phase!=SOTTO_STAGE_RECORD) return -1;
    if (s->frames>=SOTTO_STAGE_MAX_FRAMES) {
        sotto_stage_abort(s);return -2;
    }
    for (i=0u;i<6u;++i) s->codec[6u*s->frames+i]=six[i];
    ++s->frames;
    return 0;
}
/* A positive lost count or a failed codec / microphone / SPI operation
 * MUST abort rather than sending a silently truncated recording. */
static inline int sotto_stage_seal(sotto_stage_t *s,unsigned lost,int codec_ok) {
    if (!s || s->phase!=SOTTO_STAGE_RECORD) return -1;
    if (lost || !codec_ok || !s->frames) {
        sotto_stage_abort(s);return -2;
    }
    s->phase=SOTTO_STAGE_SEALED;return 0;
}
static inline unsigned sotto_stage_segments(const sotto_stage_t *s) {
    return ((unsigned)s->frames+7u)/8u;
}
/* No packet is available while recording. The repeated peek is idempotent
 * until one physical-frame completion ACK. Never consume a frame on failure. */
static inline const uint16_t *sotto_stage_peek(sotto_stage_t *s) {
    unsigned seg,last,tail,n,p;
    if (!s || (s->phase!=SOTTO_STAGE_SEALED && s->phase!=SOTTO_STAGE_SEND))
        return (const uint16_t*)0;
    if (s->prepared) return s->words;
    seg=sotto_stage_segments(s);last=s->first_id+seg-1u;
    tail=(s->frames-1u)%8u+1u;p=s->next_packet;
    if (p==0u) {
        n=dv_session_build_words(s->words,DV_SESSION_BEGIN,
               s->tag_hi,s->tag_lo,s->first_id,0u,0u,0u);
    } else if (p<=seg) {
        unsigned count=p==seg?tail:8u;
        n=dv_guard33_build_words(s->words,s->codec+(p-1u)*48u,
                                 count,(uint8_t)(s->first_id+p-1u));
    } else if (p==seg+1u) {
        n=dv_session_build_words(s->words,DV_SESSION_END,
               s->tag_hi,s->tag_lo,s->first_id,last,tail,seg);
    } else {sotto_stage_abort(s);return (const uint16_t*)0;}
    if (n!=33u) {sotto_stage_abort(s);return (const uint16_t*)0;}
    s->phase=SOTTO_STAGE_SEND;s->prepared=1u;return s->words;
}
static inline int sotto_stage_confirm(sotto_stage_t *s,int observed_tx_done) {
    if (!s || s->phase!=SOTTO_STAGE_SEND || !s->prepared) return -1;
    if (!observed_tx_done) {sotto_stage_abort(s);return -2;}
    s->prepared=0u;++s->next_packet;
    if (s->next_packet==sotto_stage_segments(s)+2u) s->phase=SOTTO_STAGE_DONE;
    return 0;
}
#endif
