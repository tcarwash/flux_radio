/* Reusable guarded FD6818B native FSK consumer for a SEALED Sotto session.
 * Derived from physically exercised FSK-13/14 guard-word research.
 * The calling module must require explicit operator approval and appropriate
 * RF conditions. No tune, flash write, mic sampling, or automatic retries.
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_SOTTO_GUARD_TX_H
#define FLUX_SOTTO_GUARD_TX_H
#include "sotto_tx_stage.h"

#define SOTTO_RF_FRAME_MS 300u
#define SOTTO_RF_SESSION_MS 3900u

typedef int (*sotto_rf_cancel_fn)(const k6_api_t *);
static inline int sg_safe(const k6_api_t *a,sotto_rf_cancel_fn cancel) {
    return !cancel(a) && !km_power_switch_off();
}
static inline int sg_frame(const k6_api_t *a,const uint16_t *words,
                           sotto_rf_cancel_fn cancel,uint32_t begin) {
    unsigned i;
    uint32_t t;
#define WR(r,v) do{if(a->fd_write((r),(uint16_t)(v)))return -13;}while(0)
    if(!sg_safe(a,cancel) || a->ms()-begin>=SOTTO_RF_SESSION_MS)return -4;
    WR(0x59,0x00f8); WR(0x02,0); WR(0x5d,0x4100);
    WR(0x59,0x80f8); WR(0x59,0x00f8);
    for(i=0;i<33u;i++){
        if(!sg_safe(a,cancel))return -4;
        WR(0x5f,words[i]);
    }
    /* Never acknowledge a stale completion from a previous packet. */
    if(a->fd_read(0x0c)&1u)return -14;
    if(!sg_safe(a,cancel))return -4;
    WR(0x59,0x08f8);
    t=a->ms();
    for(;;){
        if(!sg_safe(a,cancel)||a->ms()-begin>=SOTTO_RF_SESSION_MS)return -4;
        if(!(a->fd_read(0x30)&2u))return -5;
        if(a->fd_read(0x0c)&1u)return 0;
        if(a->ms()-t>=SOTTO_RF_FRAME_MS)return -14;
        a->delay_us(5000u);
    }
#undef WR
}

/* Return the number of fully completed RF frames; any failure consumes the
 * sealed session so the caller cannot accidentally replay a partial session.
 * The independent firmware watchdog remains authoritative. */
static inline int sotto_guard_tx(const k6_api_t *a,sotto_stage_t *s,
                                  sotto_rf_cancel_fn cancel) {
    static const uint8_t regs[]={0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d};
    static const uint16_t vals[]={0x8000,0x00f8,0x00e1,0x0095,0xc194,
                                 0x076b,0x3e36,0x5625,0x4100};
    uint16_t saved[9];
    uint32_t start;
    unsigned i,n=0;
    int result=-5,restore=0,cleanup_bad=0;
    if(!s||s->phase!=SOTTO_STAGE_SEALED||!cancel)return -2;
    if(a->fd_read(0)!=0x4829u || (a->fd_read(0x30)&2u) ||
       (a->fd_read(0x3f)&0xa000u) || !sg_safe(a,cancel)){
        result=-2;goto failed;
    }
    if(KM_RSSI_DBM(a->fd_read(0x67))>-90){result=-7;goto failed;}
    a->delay_us(50000u);
    if(!sg_safe(a,cancel)||KM_RSSI_DBM(a->fd_read(0x67))>-90){
        result=-7;goto failed;
    }
    if(a->tx_on(SOTTO_RF_SESSION_MS)!=0)goto failed;
    start=a->ms();
    if(a->tx_pa(0)!=0)goto cleanup;
    for(i=0;i<9;i++)saved[i]=a->fd_read(regs[i]);
    if((saved[0]&0xa000u)||(saved[1]&0x0800u))goto cleanup;
    restore=1;
    if(a->fd_write(0x59,0x00f8u)||a->fd_write(0x02,0u)){
        result=-13;goto cleanup;
    }
    for(i=0;i<9;i++)if(a->fd_write(regs[i],vals[i])){
        result=-13;goto cleanup;
    }
    if(!sg_safe(a,cancel)||a->tx_pa(1)!=0)goto cleanup;
    while(s->phase!=SOTTO_STAGE_DONE){
        const uint16_t *w=sotto_stage_peek(s);
        if(!w){result=-12;goto cleanup;}
        result=sg_frame(a,w,cancel,start);
        if(result!=0){(void)sotto_stage_confirm(s,0);goto cleanup;}
        if(sotto_stage_confirm(s,1)){result=-12;goto cleanup;}
        ++n;
    }
    result=(int)n;
cleanup:
    /* Every branch after successful tx_on follows this path. Best effort
     * ALL restore writes even if a preceding write fails. */
    if(a->tx_pa(0)!=0)cleanup_bad=1;
    if(restore){
        if(a->fd_write(0x59,0x00f8u))cleanup_bad=1;
        if(a->fd_write(0x3f,0u))cleanup_bad=1;
        if(a->fd_write(0x02,0u))cleanup_bad=1;
        for(i=9;i>0;i--)if(a->fd_write(regs[i-1],saved[i-1]))cleanup_bad=1;
    }
    a->tx_off();
    if(cleanup_bad)result=-15;
    if(result>=0)return result;
failed:
    sotto_stage_abort(s);
    return result;
}
#endif
