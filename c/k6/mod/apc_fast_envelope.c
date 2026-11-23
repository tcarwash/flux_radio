/* Flux APC high-side envelope response lab: 80%-100% ONLY, API27.
 * Eight 20-ms pairs, eight 10-ms pairs, twelve 5-ms pairs.
 * One bounded, guarded TX; no flash or raw REG_36 writes.
 * Inert unless operator has explicitly enabled the fixture and TIM14 watchdog.
 */
#define K6_MODULE_LEVEL 27
#include "../k6_mod.h"
#ifndef APC_FAST_ENABLE
#define APC_FAST_ENABLE 0
#endif
#ifndef APC_FAST_FIXTURE_ACK
#define APC_FAST_FIXTURE_ACK 0
#endif
#ifndef APC_FAST_WATCHDOG_ACK
#define APC_FAST_WATCHDOG_ACK 0
#endif
#if APC_FAST_ENABLE && (!APC_FAST_FIXTURE_ACK || !APC_FAST_WATCHDOG_ACK)
#error "APC fast envelope needs fixture and TX watchdog acknowledgment"
#endif
#define APC_FAST_HZ 433500000u
#define APC_FAST_TX_LIMIT_MS 1800u
#define R_ID 0x00u
#define R_STATE 0x30u
#define R_PA 0x36u
#define R_RSSI 0x67u

#if APC_FAST_ENABLE
static int safe(const k6_api_t *a){
    return !a->key_down() && !km_power_switch_off();
}
static int sethold(const k6_api_t *a, uint16_t original,
                   uint8_t percent, uint16_t duration_ms) {
    uint32_t begin;
    unsigned n,q;
    uint16_t expected;
    if((percent!=80u && percent!=100u) || !safe(a)) return -4;
    if(a->apc_set(percent)!=0) return -14;
    n=(unsigned)(original>>8)*percent+50u;
    q=(n*5243u)>>19; /* exact integer /100 for maximum n=25550 */
    expected=(uint16_t)((q<<8)|0x00a2u);
    if(a->fd_read(R_PA)!=expected) return -15;
    begin=a->ms();
    while((uint32_t)(a->ms()-begin)<duration_ms) {
        if(!safe(a)) return -4;
        a->delay_us(500u);
    }
    return 0;
}
#endif

int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<27) return -3;
    if(a->fd_read(R_ID)!=0x4829u) return -6;
#if !APC_FAST_ENABLE
    a->say("APC fast lab inert");
    return -9;
#else
    uint16_t original;
    unsigned i;
    int ret=0,keyed=0;
    if(!a->apc_set) return -3;
    if(a->rx_hz()!=APC_FAST_HZ || !safe(a) ||
       (a->fd_read(R_STATE)&2u)) return -2;
    if(KM_RSSI_DBM(a->fd_read(R_RSSI))>-90) return -7;
    a->delay_us(50000u);
    if(!safe(a)||KM_RSSI_DBM(a->fd_read(R_RSSI))>-90)return -7;
    if(a->tx_on(APC_FAST_TX_LIMIT_MS)!=0)return -5;
    keyed=1;
    original=a->fd_read(R_PA);
    if((original&0x00ffu)!=0x00a2u || (original>>8)<32u){
        ret=-12;goto done;
    }
    a->say("APC fast 20/10/5ms");
    if((ret=sethold(a,original,100,80))!=0)goto done;
    for(i=0;i<8;i++){
        if((ret=sethold(a,original,80,20))!=0)goto done;
        if((ret=sethold(a,original,100,20))!=0)goto done;
    }
    for(i=0;i<8;i++){
        if((ret=sethold(a,original,80,10))!=0)goto done;
        if((ret=sethold(a,original,100,10))!=0)goto done;
    }
    for(i=0;i<12;i++){
        if((ret=sethold(a,original,80,5))!=0)goto done;
        if((ret=sethold(a,original,100,5))!=0)goto done;
    }
    if((ret=sethold(a,original,100,80))!=0)goto done;
    ret=24;
done:
    if(keyed){
        (void)a->apc_set(100u);
        a->tx_off();
    }
    if(ret==24) a->say("APC fast done; RX restored");
    else a->say("APC fast aborted; TX released");
    return ret;
#endif
}
