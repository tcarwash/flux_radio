/* Offline real ARM API30 GP0 DMA start/stop route test. No RF emission.
 * Uses real module scratch from the cable loader and no virtual PCM injection.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t*a){
    uint32_t n=0;
    uint8_t *p=(uint8_t*)a->scratch(&n);
    if(!a||a->abi!=1||a->level!=30||!p||n<644u||(uintptr_t)p&3u)return -3;
    if(a->mic_stream(8,p,87260u)!=-1)return -4; /* TX ownership mandatory */
    if(a->tx_on(2500u)!=0)return -5;
    if(a->mic_stream(7,0,0)!=0){a->tx_off();return -6;}
    {int code=a->mic_stream(8,p,87260u);
    if(code!=0){a->mic_stream(1,0,0);a->tx_off();return -80+code;}}
    if(a->mic_stream(8,p,87260u)!=-1){a->mic_stream(1,0,0);a->tx_off();return -9;}
    if(a->mic_stream(1,0,0)!=0){a->tx_off();return -10;}
    a->tx_off();
    return 23;
}
