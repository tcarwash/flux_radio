/* Bounded live Sotto-1200 FDV1 session scheduler. No hardware access.
 * Foreground owns this queue. A separate ISR owns PCM capture.
 * 3 slots allow a voice frame plus END/BEGIN at an epoch boundary without
 * imposing an entire speech recording or a new wire format.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_LIVE_SESSION_H
#define FLUX_SOTTO_LIVE_SESSION_H
#include "dv_guard33_session.h"
#define SL_QUEUE 3u
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
static inline unsigned sl_index(unsigned i){
    /* head <=2 and count <=3: at most one subtraction is required. */
    return i>=SL_QUEUE?i-SL_QUEUE:i;
}
static inline uint16_t *sl_slot(sotto_live_t *s){
    return s->ring[sl_index(s->head+s->count)];
}
/* Live TX only: both voice and FDV1 control use the same L64 CRC/PN9
 * sealing loop. A single implementation saves RAM-executable code without
 * changing the on-air wire format or the public packet builders. */
static void sl_seal_packet(uint16_t out[33]){
    uint8_t *raw=(uint8_t*)out;
    unsigned i;
    uint16_t crc=0xffffu,pn9=0x01ffu;
    for(i=0;i<64u;i++){
        uint8_t b;
        if(i<62u){b=raw[i];crc=dv_guard33_crc_step(crc,b);}
        else b=(uint8_t)(i==62u?(crc>>8):crc);
        b^=dv_guard33_pn9_step(&pn9);
        raw[i]=b;
    }
    out[32]=DV_GUARD33_GUARD_WORD;
}
static inline int sl_control(sotto_live_t *s,unsigned type){
    uint16_t *out=sl_slot(s);
    uint8_t *raw=(uint8_t*)out;
    unsigned i,segments=type==DV_SESSION_END?s->segments:0u;
    unsigned last=type==DV_SESSION_END?SL_FIRST+segments-1u:0u;
    unsigned tail=type==DV_SESSION_END?s->tail:0u;
    /* The scheduler supplies fixed first ID 112; for END, 1..144
     * segments keeps its computed last ID within the 8-bit range. */
    if(!(s->tag_hi|s->tag_lo) ||
       (type!=DV_SESSION_BEGIN && type!=DV_SESSION_END) ||
       (type==DV_SESSION_END &&
           (!segments||segments>DV_SESSION_MAX_SEGMENTS||!tail||tail>8u))){
        sl_abort(s);return SL_FAULT;
    }
    for(i=0;i<62u;i++)raw[i]=0;
    raw[0]=DV_SESSION_MAGIC;raw[1]=DV_SESSION_VERSION;raw[2]=(uint8_t)type;
    for(i=0;i<4u;i++){
        raw[4u+i]=(uint8_t)(s->tag_hi>>(24u-8u*i));
        raw[8u+i]=(uint8_t)(s->tag_lo>>(24u-8u*i));
    }
    raw[12]=SL_FIRST;raw[13]=(uint8_t)last;
    raw[14]=(uint8_t)tail;raw[15]=(uint8_t)segments;
    raw[16]='F';raw[17]='D';raw[18]='V';raw[19]='1';
    sl_seal_packet(out);
    s->count++;return SL_OK;
}
static inline int sl_voice(sotto_live_t *s){
    uint16_t *out=sl_slot(s);
    uint8_t *raw=(uint8_t*)out;
    unsigned i;
    if(s->frames<1u||s->frames>8u){sl_abort(s);return SL_FAULT;}
    for(i=0;i<62u;i++)raw[i]=0;
    raw[0]=0xa9u;raw[1]=(uint8_t)(SL_FIRST+s->segments);
    raw[2]=0xc0u;raw[5]=s->frames;
    for(i=0;i<6u*s->frames;i++)raw[6u+i]=s->pending[i];
    sl_seal_packet(out);
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
    s->head=(uint8_t)sl_index(s->head+1u);s->count--;s->completed++;
    return SL_OK;
}
#endif
