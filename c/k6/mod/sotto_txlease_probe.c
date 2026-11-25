/* Explicitly-launched module transmit lease diagnostic (no codec or microphone).
 * This is only a 250ms FM carrier test into an operator-confirmed dummy load.
 * Never transmit on installation; default build is inert. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 29
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("TXLEASE PROB");
#ifndef K6_TXLEASE_ARMED
#define K6_TXLEASE_ARMED 0
#endif
int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<29)return -3;
#if !K6_TXLEASE_ARMED
    a->say("TXLEASE inert");
    return -9;
#else
    int rc;
    if(a->rx_hz()!=433500000u || a->key_down()){
        a->say("TXLEASE freq/key");
        return -4;
    }
    rc=a->tx_on(1200u);
    if(rc!=0){
        a->say(rc==-1?"TXLEASE refused":"TXLEASE already");
        return -50+rc;
    }
    a->tx_pa(1);
    a->delay_us(250000u);
    a->tx_pa(0);
    a->tx_off();
    a->say("TXLEASE OK");
    return 77;
#endif
}
