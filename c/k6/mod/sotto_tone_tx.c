/* Cable-launched, deterministic 1 kHz Sotto PCM-to-FSK test.
 * Uses the real encoder and FDV1 scheduler with no microphone ISR/ring.
 * Never transmits on installation; only explicit foreground RUN.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 29
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("SOTTO TONE");

#ifndef SOTTO_TONE_TX_ARMED
#define SOTTO_TONE_TX_ARMED 0
#endif

#if SOTTO_TONE_TX_ARMED
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#include "sotto_live_session.h"
#include "sotto_tone_pcm.h"

/* 80-sample on-chip synthesis eliminates the 4x80 microphone PCM ring
 * and its ISR state, preserving scratch for the Sotto encoder. */
typedef struct {
    sotto_live_t session;
    int16_t pcm[ST_TONE_SAMPLES];
    uint8_t six[6];
    uint8_t inflight, reserved;
    uint32_t last_poll_ms;
} st_state_t;

static int st_cancelled(const k6_api_t *a){
    return a->key_down() || (a->cancel_poll && a->cancel_poll()==1);
}

/* Foreground starts and acknowledges only actual API29 frames; no fake ACK. */
static int st_service(const k6_api_t *a,st_state_t *s){
    const uint16_t *w;
    uint32_t now=a->ms();
    int r;
    if(s->inflight && now-s->last_poll_ms>=5u){
        s->last_poll_ms=now;
        r=a->fsk_async(1,0,0);
        if(r<0 || r>1)return -1;
        if(r){
            if(sl_ack(&s->session,1))return -1;
            s->inflight=0;
        }
    }
    if(!s->inflight && (w=sl_peek(&s->session))){
        if(a->fsk_async(0,w,33))return -1;
        s->inflight=1;
        s->last_poll_ms=a->ms();
    }
    return 0;
}

static int st_room(const k6_api_t *a,st_state_t *s,uint32_t started){
    while(s->session.count==SL_QUEUE){
        if(st_cancelled(a) || a->ms()-started>=8500u ||
           st_service(a,s))return -1;
        a->delay_us(300u);
    }
    return 0;
}

int k6_main(const k6_api_t *a){
    if(!a || a->abi!=1 || a->level<K6_MODULE_LEVEL)return -3;
    unsigned room=0,block,i,flushes;
    uint8_t *scratch;
    st_state_t *s;
    slim_enc_t *enc;
    sotto_io_t io;
    uint32_t started;
    int r;
    if(a->rx_hz()!=433500000u || st_cancelled(a))return -4;
    scratch=a->scratch(&room);
    if(!scratch || ((uintptr_t)scratch&3u) ||
       room<sizeof(*s)+slim_enc_bytes()){
        a->say("SOTTO TONE no RAM");
        return -8;
    }
    s=(st_state_t*)scratch;
    enc=(slim_enc_t*)(scratch+sizeof(*s));
    if(a->fd_read(0)!=0x4829 || sio_init(&io,a))return -10;
    slim_enc_init(enc);enc->io=&io;
    if(sl_begin(&s->session,0x5354544fu,0x4e453132u))return -11;
    s->inflight=0;s->last_poll_ms=0;
    if(a->tx_on(9000u))return -5;
    if(a->tx_pa(0)!=0){a->tx_off();return -6;}
    started=a->ms();
    for(block=0;block<ST_TONE_BLOCKS;block++){
        for(i=0;i<ST_TONE_SAMPLES;i++)
            s->pcm[i]=st_tone_sample(block*ST_TONE_SAMPLES+i);
        r=slim_encode10(enc,s->pcm,s->six);
        if(r<0 || r>1 || io.fault)goto fail;
        if(r){
            if(st_room(a,s,started))goto fail;
            if(sl_push(&s->session,s->six))goto fail;
        }
        if(st_cancelled(a) || a->ms()-started>=8500u ||
           st_service(a,s))goto fail;
    }
    for(flushes=0;flushes<4u;flushes++){
        r=slim_flush(enc,s->six);
        if(r<0 || r>1 || io.fault)goto fail;
        if(!r)break;
        if(st_room(a,s,started) || sl_push(&s->session,s->six))goto fail;
    }
    if(flushes==4u)goto fail;
    while(sl_finish(&s->session)==SL_FULL){
        if(st_cancelled(a) || a->ms()-started>=8500u ||
           st_service(a,s))goto fail;
        a->delay_us(500u);
    }
    while(s->inflight || sl_peek(&s->session)){
        if(st_cancelled(a) || a->ms()-started>=8500u ||
           st_service(a,s))goto fail;
        a->delay_us(500u);
    }
    r=(int)s->session.encoded;
    a->tx_off();
    a->say("SOTTO TONE sent");
    return r;
fail:
    a->tx_off();
    a->say("SOTTO TONE abort");
    return -12;
}
#else
int k6_main(const k6_api_t *a){
    if(!a||a->abi!=1||a->level<K6_MODULE_LEVEL)return -3;
    a->say("SOTTO TONE inert");
    return -9;
}
#endif
