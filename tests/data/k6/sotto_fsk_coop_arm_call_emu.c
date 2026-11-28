/* Offline-only linker/Cortex-M0 module fixture. No hardware operations.
 * Stages a sealed 33-word FSK packet via the real API30 host callback.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t *a) {
    uint32_t room=0;
    uint16_t *buf;
    int rc,i,out=-8;
    if(!a || a->abi!=1 || a->level!=30 || !a->fsk_async)return -2;
    buf=(uint16_t*)a->scratch(&room);
    if(!buf || room<96u || ((uintptr_t)buf&3u))return -3;
    for(i=0;i<33;i++)buf[i]=(uint16_t)(0x2134u+137u*i);
    buf[32]=0xa55au;
#ifdef BAD_GUARD
    buf[32]=0x1234u;
#endif
    if(a->tx_on(3000u)!=0)return -10;
    rc=a->fsk_async(0,buf,33);
#ifdef BAD_GUARD
    if(rc<0)return -20; // host must already have dekeyed on guard refusal
    a->tx_off();return -21;
#endif
    if(rc!=0){a->tx_off();return -11;}
#ifdef ABORT_EARLY
    if(a->fsk_async(2,0,0)==0)return 8;
    a->tx_off();return -22;
#endif
#ifdef DELAY_PAST_DEADLINE
    a->delay_us(900000);
#endif
    for(i=0;i<200;i++){
        rc=a->fsk_async(1,0,0);
        if(rc==1){out=9;break;}
        if(rc<0){out=-12;break;}
        a->delay_us(5000u);
    }
    a->tx_off();
    return out;
}
