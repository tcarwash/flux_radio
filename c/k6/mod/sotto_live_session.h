/* Bounded live Sotto-1200 FDV1 session scheduler. No hardware access.
 * Foreground owns this queue. A separate ISR owns PCM capture.
 * 4 slots allow a voice frame plus END/BEGIN at an epoch boundary without
 * imposing an entire speech recording or a new wire format.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_LIVE_SESSION_H
#define FLUX_SOTTO_LIVE_SESSION_H
#include "dv_guard33_session.h"
#define SL_QUEUE 4u
#define SL_FIRST 112u
enum {SL_OK=0, SL_FULL=-2, SL_STATE=-3, SL_FAULT=-4};
typedef struct {
    uint16_t ring[SL_QUEUE][33];
    uint8_t pending[48];
    uint32_t tag_hi,tag_lo;
    uint32_t encoded,completed;
    uint8_t head,count,frames,segments,opened,ending,failed,tail;
} sotto_live_t;
static inline void sl_abort(sotto_live_t *s){s->failed=1;s->count=0;}
static inline int sl_begin(sotto_live_t *s,uint32_t hi,uint32_t lo){
    unsigned i;
    if(!s||!(hi|lo))return SL_STATE;
    for(i=0;i<sizeof(*s);i++)((uint8_t*)s)[i]=0;
    s->tag_hi=hi;s->tag_lo=lo;
    return SL_OK;
}
static inline uint16_t *sl_slot(sotto_live_t *s){
    return s->ring[(s->head+s->count)&(SL_QUEUE-1u)];
}
static inline int sl_control(sotto_live_t *s,unsigned type){
    unsigned n=dv_session_build_words(sl_slot(s),type,s->tag_hi,s->tag_lo,
        SL_FIRST,type==DV_SESSION_END?SL_FIRST+s->segments-1u:0,
        type==DV_SESSION_END?s->tail:0,type==DV_SESSION_END?s->segments:0);
    if(n!=33){sl_abort(s);return SL_FAULT;}
    s->count++;return SL_OK;
}
static inline int sl_voice(sotto_live_t *s){
    if(dv_guard33_build_words(sl_slot(s),s->pending,s->frames,
                             (uint8_t)(SL_FIRST+s->segments))!=33){
        sl_abort(s);return SL_FAULT;
    }
    s->count++;s->segments++;s->tail=s->frames;s->frames=0;
    return SL_OK;
}
static inline int sl_close_epoch(sotto_live_t *s){
    if(sl_control(s,DV_SESSION_END))return SL_FAULT;
    s->opened=0;s->segments=0;s->tail=0;
    if(++s->tag_lo==0 && ++s->tag_hi==0){sl_abort(s);return SL_FAULT;}
    return SL_OK;
}
/* Return FULL before accepting any bytes. Caller retains the six-byte
 * frame and may retry after a confirmed completion. No loss hidden. */
static inline int sl_push(sotto_live_t *s,const uint8_t six[6]){
    unsigned i,need;
    if(!s||!six||s->ending||s->failed)return SL_STATE;
    need=!s->opened;
    if(s->frames==7){need++;if(s->segments==DV_SESSION_MAX_SEGMENTS-1u)need++;}
    if(s->count+need>SL_QUEUE)return SL_FULL;
    if(!s->opened){if(sl_control(s,DV_SESSION_BEGIN))return SL_FAULT;s->opened=1;}
    for(i=0;i<6;i++)s->pending[6u*s->frames+i]=six[i];
    s->frames++;s->encoded++;
    if(s->frames==8){
        if(sl_voice(s))return SL_FAULT;
        if(s->segments==DV_SESSION_MAX_SEGMENTS)return sl_close_epoch(s);
    }
    return SL_OK;
}
static inline int sl_finish(sotto_live_t *s){
    if(!s||s->failed)return SL_STATE;
    if(s->ending)return SL_OK;
    if(s->count+(s->opened?1u:0u)+(s->frames?1u:0u)>SL_QUEUE)return SL_FULL;
    if(s->frames&&sl_voice(s))return SL_FAULT;
    if(s->opened&&sl_close_epoch(s))return SL_FAULT;
    s->ending=1;return SL_OK;
}
static inline const uint16_t *sl_peek(const sotto_live_t *s){
    return s&&!s->failed&&s->count?s->ring[s->head]:0;
}
static inline int sl_ack(sotto_live_t *s,int confirmed){
    if(!sl_peek(s))return SL_STATE;
    if(!confirmed){sl_abort(s);return SL_FAULT;}
    s->head=(s->head+1u)&(SL_QUEUE-1u);s->count--;s->completed++;
    return SL_OK;
}
#endif
