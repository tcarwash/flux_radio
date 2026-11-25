/* Live module's in-place packet sealer must match the public wire codec. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "c/k6/mod/sotto_live_session.h"

static int same(const uint16_t *a, const uint16_t *b, unsigned seed, const char *where) {
    if (!memcmp(a,b,66)) return 0;
    fprintf(stderr,"packet mismatch: seed=%u stage=%s\n",seed,where);
    return 1;
}
int main(void) {
    unsigned seed;
    for(seed=1;seed<700;seed++) {
        sotto_live_t s;
        uint16_t ref[33];
        uint32_t hi=0x13243546u ^ (seed * 12345u);
        uint32_t lo=0x87654321u ^ (seed * 98765u);
        unsigned i, count=(seed % 8u)+1u, start_seg=(seed % 144u);
        if(sl_begin(&s,hi,lo)) return 10;
        if(sl_control(&s,DV_SESSION_BEGIN))return 11;
        if(dv_session_build_words(ref,DV_SESSION_BEGIN,hi,lo,SL_FIRST,0,0,0)!=33)return 12;
        if(same(s.ring[0],ref,seed,"BEGIN"))return 13;
        s.segments=(uint8_t)start_seg;
        s.frames=(uint8_t)count;
        for(i=0;i<count*6;i++)s.pending[i]=(uint8_t)(seed*13u+i*37u);
        if(sl_voice(&s))return 14;
        if(dv_guard33_build_words(ref,s.pending,count,(uint8_t)(SL_FIRST+start_seg))!=33)return 15;
        if(same(s.ring[1],ref,seed,"VOICE"))return 16;
        if(sl_control(&s,DV_SESSION_END))return 17;
        if(dv_session_build_words(ref,DV_SESSION_END,hi,lo,SL_FIRST,
             SL_FIRST+s.segments-1u,count,s.segments)!=33)return 18;
        if(same(s.ring[2],ref,seed,"END"))return 19;
    }
    /* Three slots must survive wrapping the ring and a complete 144-segment
     * epoch under backpressure, including the three-packet boundary. */
    {
        sotto_live_t s;
        unsigned f,tries,i;
        uint8_t six[6];
        int r;
        if(sl_begin(&s,0x12345u,0x789abu))return 20;
        for(f=0;f<8u*DV_SESSION_MAX_SEGMENTS;f++){
            for(i=0;i<6;i++)six[i]=(uint8_t)(f+i);
            for(tries=0;;tries++){
                if(tries>SL_QUEUE+3u)return 21;
                r=sl_push(&s,six);
                if(r==SL_OK)break;
                if(r!=SL_FULL || !sl_peek(&s) || sl_ack(&s,1))return 22;
            }
            if((f&1u)==0u && sl_peek(&s) && sl_ack(&s,1))return 23;
        }
        for(tries=0;;tries++){
            if(tries>SL_QUEUE+3u)return 24;
            r=sl_finish(&s);
            if(r==SL_OK)break;
            if(r!=SL_FULL || !sl_peek(&s) || sl_ack(&s,1))return 25;
        }
        while(sl_peek(&s))if(sl_ack(&s,1))return 26;
        if(s.encoded!=8u*DV_SESSION_MAX_SEGMENTS ||
           s.completed!=DV_SESSION_MAX_SEGMENTS+2u ||
           s.count || s.opened || !s.ending || s.failed ||
           s.tag_hi!=0x12345u || s.tag_lo!=0x789acu) return 27;
    }
    puts("PASS: 2097 packet equality checks plus 144-segment epoch/wrap/backpressure");
    return 0;
}
