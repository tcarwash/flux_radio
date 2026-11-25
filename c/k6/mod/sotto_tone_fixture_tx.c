/* Bounded pre-encoded 1kHz Sotto digital voice FSK lab transmission.
 * Eight public synthetic codec frames generated offline from 320ms 1kHz PCM.
 * This isolates API29 RF FIFO from microphone, encoder, and SPI codebooks.
 * Explicitly armed build: only a manual module RUN transmits.
 * SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 31
#endif
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("SOTTO 1K FX");
#ifndef SOTTO_1K_FIXTURE_ARMED
#define SOTTO_1K_FIXTURE_ARMED 0
#endif
#if SOTTO_1K_FIXTURE_ARMED
#include "sotto_live_session.h"
#include "sotto_tone_bits.h"
static int cancel(const k6_api_t *a){
    return a->key_down() || (a->cancel_poll && a->cancel_poll()==1);
}
#endif
int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<K6_MODULE_LEVEL)return -3;
#if !SOTTO_1K_FIXTURE_ARMED
    a->say("SOTTO 1K inert");
    return -9;
#else
    unsigned room=0,i;
    uint32_t begun;
    uint8_t *scratch;
    sotto_live_t *s;
    const uint16_t *words;
    int r;
    if(a->rx_hz()!=433500000u || cancel(a))return -4;
    scratch=a->scratch(&room);
    if(!scratch || ((uintptr_t)scratch&3u) || room<sizeof(*s))return -8;
    s=(sotto_live_t*)scratch;
    if(sl_begin(s,0x5354544fu,0x4e453132u))return -11;
    for(i=0;i<8;i++){
        if(sl_push(s,st_tone_bits+6u*i))return -11;
    }
    if(sl_finish(s) || s->count!=3u)return -11;
    r=a->tx_on(1200u);
    if(r){
        a->say("SOTTO 1K tx refuse");
        return -50+r;
    }
    if(a->tx_pa(0)!=0)goto fail;
    while((words=sl_peek(s))!=0){
        if(cancel(a) || a->fsk_async(0,words,33))goto fail;
        begun=a->ms();
        for(;;){
            if(cancel(a) || a->ms()-begun>=700u)goto fail;
            r=a->fsk_async(1,0,0);
            if(r<0)goto fail;
            if(r==1){
                if(sl_ack(s,1))goto fail;
                break;
            }
            a->delay_us(2500u);
        }
    }
    a->tx_off();
    a->say("SOTTO 1K sent");
    return (int)s->encoded;
fail:
    a->fsk_async(2,0,0);
    a->tx_off();
    a->say("SOTTO 1K abort");
    return -12;
#endif
}
