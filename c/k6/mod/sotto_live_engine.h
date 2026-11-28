/* Cooperative live TX engine. The timer ISR is the only PCM producer;
 * foreground is the only consumer and owns the encoder and packet queue.
 * Hardware callbacks are supplied by the host; no RF or IRQ manipulation
 * is performed here. SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_LIVE_ENGINE_H
#define FLUX_SOTTO_LIVE_ENGINE_H
#include "sotto_live_session.h"
#define SL_PCM_BLOCKS 4u
#define SL_PCM_SAMPLES 80u
typedef struct {
    int16_t pcm[SL_PCM_BLOCKS][SL_PCM_SAMPLES];
    volatile uint8_t produced, consumed, offset, lost;
} sl_pcm_t;
/* Single-core Cortex-M0, naturally aligned byte counters. Publish samples
 * before making a block visible; do not reuse it until encode returns. */
static inline void sl_barrier(void){__asm volatile("" ::: "memory");}
static inline int sl_pcm_sample(sl_pcm_t *p,int16_t sample){
    uint8_t h=p->produced,n=p->offset;
    if(p->lost)return -1;
    if((uint8_t)(h-p->consumed)>=SL_PCM_BLOCKS){p->lost=1;return -1;}
    p->pcm[h&(SL_PCM_BLOCKS-1u)][n++]=sample;
    if(n==SL_PCM_SAMPLES){p->offset=0;sl_barrier();p->produced=h+1u;}
    else p->offset=n;
    return 0;
}
/* Caller must stop and join capture ISR before padding/closing. */
static inline int sl_pcm_close(sl_pcm_t *p){
    while(p->offset)if(sl_pcm_sample(p,0))return -1;
    return p->lost?-1:0;
}
enum {SL_IDLE=0,SL_CAPTURE=1,SL_DRAIN=2,SL_DONE=3,SL_FAILED=4};
typedef struct {
    sotto_live_t tx;
    sl_pcm_t mic;
    uint8_t frame[6], pending, inflight, flushes, phase, codec_done;
} sl_engine_t;
/* start: 0 accepted (FIFO owns a copy or uses stable peek until poll);
 * poll: 0 busy, 1 observed completion, <0 failed. Never fake an ACK.
 * encode/flush: 0 no frame, 1 frame, <0 fatal. stop_capture and abort
 * are synchronous host cleanup callbacks; abort must dekey and restore RX. */
typedef struct {
    int (*encode)(void*,const int16_t*,uint8_t*);
    int (*flush)(void*,uint8_t*);
    int (*start)(void*,const uint16_t*);
    int (*poll)(void*);
    void (*stop_capture)(void*);
    void (*abort)(void*);
    void (*end)(void*); /* normal dekey and restore RX */
} sl_ops_t;
static inline int sl_engine_begin(sl_engine_t *s,uint32_t hi,uint32_t lo){
    unsigned i;
    if(!s)return -1;
    if(!(hi|lo))return -1;
    for(i=0;i<sizeof(*s);i++)((uint8_t*)s)[i]=0;
    s->tx.tag_hi=hi;s->tx.tag_lo=lo;
    s->phase=SL_CAPTURE;return 0;
}
static inline int sl_engine_fail(sl_engine_t *s,const sl_ops_t *o,void *ctx){
    if(s->phase!=SL_FAILED){
        s->phase=SL_FAILED;
        o->stop_capture(ctx);o->abort(ctx);
        sl_abort(&s->tx);
    }
    return -1;
}
static inline int sl_engine_release(sl_engine_t *s,const sl_ops_t *o,void *ctx){
    if(s->phase!=SL_CAPTURE)return -1;
    o->stop_capture(ctx);
    if(sl_pcm_close(&s->mic))return sl_engine_fail(s,o,ctx);
    s->phase=SL_DRAIN;return 0;
}
/* Bounded foreground step, at most one 10ms codec block or one flush call.
 * Service native FSK before encoding; the host watchdog independently
 * bounds TX lease and packet duration. No wait loops or duration buffers. */
static inline int sl_engine_tick(sl_engine_t *s,const sl_ops_t *o,void *ctx){
    int r;
    const uint16_t *w;
    if(s->phase!=SL_CAPTURE&&s->phase!=SL_DRAIN)return s->phase==SL_DONE?1:-1;
    if(s->mic.lost)goto fail;
    if(s->inflight){
        r=o->poll(ctx);if(r<0||r>1)goto fail;
        if(r){if(sl_ack(&s->tx,1))goto fail;s->inflight=0;}
    }
    if(!s->inflight&&(w=sl_peek(&s->tx))){
        if(o->start(ctx,w))goto fail;
        s->inflight=1;
    }
    if(s->pending){
        r=sl_push(&s->tx,s->frame);
        if(r==SL_FULL)return 0;
        if(r)goto fail;
        s->pending=0;
    }
    if(s->mic.consumed!=s->mic.produced){
        uint8_t t=s->mic.consumed;
        sl_barrier();
        r=o->encode(ctx,s->mic.pcm[t&(SL_PCM_BLOCKS-1u)],s->frame);
        sl_barrier();s->mic.consumed=t+1u;
        if(r<0||r>1||s->mic.lost)goto fail;
        s->pending=(uint8_t)r;
        return 0;
    }
    if(s->phase==SL_DRAIN){
        if(!s->codec_done){
            if(s->flushes++>=4u)goto fail;
            r=o->flush(ctx,s->frame);
            if(r<0||r>1)goto fail;
            s->pending=(uint8_t)r;s->codec_done=!r;
            return 0;
        }
        r=sl_finish(&s->tx);
        if(r==SL_FULL)return 0;
        if(r)goto fail;
        if(!s->inflight&&!sl_peek(&s->tx)){o->end(ctx);s->phase=SL_DONE;return 1;}
    }
    return 0;
fail:
    return sl_engine_fail(s,o,ctx);
}
#endif
