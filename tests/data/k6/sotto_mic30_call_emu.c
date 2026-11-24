/* Offline linked K6 MCU API30 mic ownership and vector smoke.
 * This is NOT a real microphone quality/capture timing test.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t *a){
    uint32_t room=0;
    uint8_t *p;
    unsigned i;
    int ptt;
    if(!a||a->abi!=1||a->level!=31||!a->mic_stream)return -2;
    ptt=a->mic_stream(3,0,0);
    if(ptt<0||ptt>1)return -3;
    p=(uint8_t *)a->scratch(&room);
    if(!p||room<768||((uintptr_t)p&3u))return -4;
    if(a->mic_stream(0,p,644)!=-1)return -5;/* no TX lease */
    if(a->mic_stream(0,(uint8_t*)0x40000000,644)!=-1)return -6;
    for(i=0;i<644;i++)p[i]=0;
    if(a->tx_on(3000)!=0)return -7;
    {int start=a->mic_stream(0,p,644);if(start!=0){a->tx_off();return -80+start;}}
    if(a->mic_stream(0,p,644)!=-1){a->tx_off();return -9;} /* no reentrant sampler */
    if(a->mic_stream(2,0,0)<0){a->tx_off();return -10;}
#ifdef LEAVE_ACTIVE
    // Deliberately abandon microphone + TX ownership. The host's
    // automatic module-return cleanup must stop the ISR before remap.
    return 11;
#endif
    if(a->mic_stream(1,0,0)!=0){a->tx_off();return -11;}
    a->tx_off();
    return 10;
}
