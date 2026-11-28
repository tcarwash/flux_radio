/* Staged Sotto session consumer using the common Data-pack FSK API.
 * No private RFIC writes, tuning, library writes, or implicit retransmission.
 * The caller must have finished recording, restored the microphone route,
 * and obtained explicit local/operator TX authorization before entering.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_DATA_TX_H
#define FLUX_SOTTO_DATA_TX_H
#include "sotto_tx_stage.h"
#define SOTTO_DATA_CONFIG (4800u | (21u << 16))
#define SOTTO_DATA_SESSION_MS 3900u

static inline int sotto_data_cancel(const k6_api_t *a){
    return a->key_down() || km_power_switch_off() ||
        (a->level>=25u && a->cancel_poll && a->cancel_poll()==1);
}

/* No automatic fallback to the old raw RFIC path on refusal. A host that
 * advertises level 24/25 but has no Data 4800 extension will fail closed.
 * Completion alone advances the sealed packet queue. */
static inline int sotto_data_send(const k6_api_t *a,sotto_stage_t *s){
    unsigned sent=0;
    uint32_t begin;
    int rc=-2;
    if(!s || s->phase!=SOTTO_STAGE_SEALED)return -2;
    if(!a || a->abi!=1 || a->level<24u || !a->fsk_configured ||
        a->fd_read(0)!=0x4829u || (a->fd_read(0x30)&2u) ||
        (a->fd_read(0x3f)&0xa000u) || sotto_data_cancel(a))goto fail;
    if(KM_RSSI_DBM(a->fd_read(0x67))>-90){rc=-7;goto fail;}
    a->delay_us(50000u);
    if(sotto_data_cancel(a)||KM_RSSI_DBM(a->fd_read(0x67))>-90){rc=-7;goto fail;}
    if(a->tx_on(SOTTO_DATA_SESSION_MS)!=0){rc=-5;goto fail;}
    begin=a->ms();
    if(a->tx_pa(0)!=0){rc=-5;goto cleanup;}
    while(s->phase!=SOTTO_STAGE_DONE){
        const uint16_t *w=sotto_stage_peek(s);
        if(!w){rc=-12;goto cleanup;}
        if(sotto_data_cancel(a)||a->ms()-begin>=SOTTO_DATA_SESSION_MS){rc=-4;goto cleanup;}
        rc=a->fsk_configured(2,(uint16_t*)w,33,SOTTO_DATA_CONFIG);
        if(rc!=0){(void)sotto_stage_confirm(s,0);goto cleanup;}
        if(sotto_stage_confirm(s,1)){rc=-12;goto cleanup;}
        ++sent;
    }
    rc=(int)sent;
cleanup:
    /* Successful Data frames return PA off. On failure the core may
       already have returned to RX; tx_off is idempotent. */
    (void)a->tx_pa(0);
    a->tx_off();
    if(rc>=0)return rc;
fail:
    sotto_stage_abort(s);
    return rc;
}
#endif
