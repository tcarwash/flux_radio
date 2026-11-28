/* Cortex-M0 API29 FSK-only radio TX module; NO microphone or live voice yet.
 * Constant test payload is arbitrary; only last guard word is significant.
 * The RF test is only via Unicorn/SoapyFang VirtualTxBackend, NEVER real RF.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t *a) {
    uint32_t room=0;
    uint16_t *buf;
    int rc, i, out=-8;
    if (!a || a->level!=31 || !a->fsk_async || a->abi!=1) return -2;
    buf=(uint16_t*)a->scratch(&room);
    if (!buf || room<96 || ((uintptr_t)buf&3u)) return -3;
    for(i=0;i<33;i++)buf[i]=(uint16_t)(0x2134u+i*137u);
    buf[32]=0xa55a;
#ifdef BAD_GUARD
    buf[32]=0x1234;
#endif
#ifdef HALF_DUPLEX
    if(a->fsk4800_rx(0,0,4)!=0) return -30; /* normal listening RX */
#endif
    if(a->tx_on(3000)!=0) return -10;
    rc=a->fsk_async(0,buf,33);
#ifdef BAD_GUARD
    if(rc<0) return -20;
    a->tx_off();return -21;
#endif
    if(rc!=0){a->tx_off();return -11;}
#ifdef ABORT_EARLY
    if(a->fsk_async(2,0,0)==0)return 8;
    return -22;
#endif
#ifdef DELAY_PAST_DEADLINE
    a->delay_us(900000);
#endif
    for(i=0;i<350;i++){
        rc=a->fsk_async(1,0,0);
        if(rc==1){out=9;break;}
        if(rc<0){out=-12;break;}
        a->delay_us(1000);
    }
    a->tx_off();
#ifdef HALF_DUPLEX
    if(out==9 && a->fsk4800_rx(0,0,4)!=0) return -31; /* back to RX */
    if(out==9 && a->fsk4800_rx(2,0,0)!=0) return -32; /* clean disarm */
#endif
    return out;
}
