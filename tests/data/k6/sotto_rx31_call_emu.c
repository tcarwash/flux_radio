/* Cortex-M0 executable API31 receive consumer: clean FDV1 session across
 * native 4800 33-word frames. OFFLINE test fixture. Does not key or decode
 * speaker PCM; receiver hardware service is supplied by host/emulator.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#include "../../../c/k6/k6_mod.h"
#include "../../../c/k6/sotto_dv_wire.c"

int k6_main(const k6_api_t *a) {
    uint32_t room=0, timeout=0, count=0, segments=0, frames=0, h=0, l=0;
    uint8_t next=0, first=0, state=0, payload[48], id=0;
    unsigned n=0, off=0, co=0;
    uint16_t *fifo;
    dv_session_ctrl_t c;
    int r, result=-8;

    if (!a || a->level < 31 || a->abi!=1 || !a->fsk4800_rx) return -3;
    fifo=(uint16_t*)a->scratch(&room);
    if (!fifo || ((uintptr_t)fifo&3u) || room<128) return -4;
    if (a->fsk4800_rx(0,0,4)!=0) return -5;
    timeout=a->ms()+4500;
    while ((int32_t)(timeout-a->ms()) > 0 && count<144u+2u) {
        if (a->key_down()) {result=-6;break;}
        r=a->fsk4800_rx(1,fifo,0);
        if (r<0) {result=-7;break;}
        if (r==0) {a->delay_us(500);continue;}
        if (r!=33) {result=-9;break;}
        count++;
        if (flux_dv_wire_session_rx66((const uint8_t*)fifo,&c,&co)) {
            if (c.kind==DV_SESSION_BEGIN && !state && (c.tag_hi|c.tag_lo)) {
                h=c.tag_hi;l=c.tag_lo;first=c.first_id;next=first;state=1;
            } else if(c.kind==DV_SESSION_END && state==1 &&
                    c.tag_hi==h && c.tag_lo==l &&
                    segments==c.segments &&
                    c.first_id==first &&
                    c.last_id==(uint8_t)(next-1u) &&
                    c.last_frames==frames &&
                    segments>0) {
                state=2;result=(int)count;break;
            } else {result=-10;break;}
        } else if (flux_dv_wire_rx66((const uint8_t*)fifo,payload,&id,&n,&off) &&
                   state==1 && id==next && n>=1 && n<=8) {
            next++;
            segments++;
            frames=n;
        } else {result=-11;break;}
        if(a->fsk4800_rx(0,0,4)!=0) {result=-12;break;}
    }
    // Disarm on success, corruption, PTT or deadline; host also owns exit cleanup.
    a->fsk4800_rx(2,0,0);
    return result;
}
