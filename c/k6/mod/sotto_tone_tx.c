/* Cable-launched on-radio 1 kHz Sotto PCM test.
 * Explicitly armed build only; installation never transmits.
 * No microphone, headset PTT, ADC, or serial PCM ingress.
 * Native codec -> FDV1 session -> API29 33-word guarded FSK.
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
#include "sotto_live_engine.h"
#include "sotto_tone_pcm.h"
typedef struct {
    const k6_api_t *api;
    slim_enc_t *encoder;
    sotto_io_t *resources;
    uint32_t last_poll_ms;
} st_ctx_t;
static int st_encode(void *v,const int16_t *pcm,uint8_t *six){
    st_ctx_t *c=(st_ctx_t*)v;
    int r=slim_encode10(c->encoder,pcm,six);
    return c->resources->fault?-1:r;
}
static int st_flush(void *v,uint8_t *six){
    st_ctx_t *c=(st_ctx_t*)v;
    int r=slim_flush(c->encoder,six);
    return c->resources->fault?-1:r;
}
static int st_start(void *v,const uint16_t *frame){
    st_ctx_t *c=(st_ctx_t*)v;
    return c->api->fsk_async(0,frame,33);
}
static int st_poll(void *v){
    st_ctx_t *c=(st_ctx_t*)v;
    uint32_t now=c->api->ms();
    if(now-c->last_poll_ms<5u)return 0;
    c->last_poll_ms=now;
    return c->api->fsk_async(1,0,0);
}
static void st_stop_capture(void *v){(void)v;} /* no microphone ISR */
static void st_end(void *v){
    st_ctx_t *c=(st_ctx_t*)v;
    c->api->tx_off();
}
static int st_cancelled(const k6_api_t *a){
    return a->key_down() ||
        (a->cancel_poll && a->cancel_poll()==1);
}
#endif

int k6_main(const k6_api_t *a){
    if(!a||a->abi!=1||a->level<K6_MODULE_LEVEL)return -3;
#if !SOTTO_TONE_TX_ARMED
    a->say("SOTTO TONE inert");
    return -9;
#else
    unsigned room=0,block,i;
    uint8_t *scratch;
    sl_engine_t *engine;
    slim_enc_t *encoder;
    sotto_io_t io;
    st_ctx_t ctx;
    sl_ops_t ops;
    uint32_t started;
    int rc=0,cancelled=0;
    /* A deliberate lab-frequency gate makes accidental run commands inert. */
    if(a->rx_hz()!=433500000u || st_cancelled(a)){
        a->say("SOTTO TONE freq/key");
        return -4;
    }
    scratch=a->scratch(&room);
    if(!scratch || ((uintptr_t)scratch&3u) ||
       room<sizeof(*engine)+slim_enc_bytes()){
        a->say("SOTTO TONE no RAM");
        return -8;
    }
    engine=(sl_engine_t*)scratch;
    encoder=(slim_enc_t*)(scratch+sizeof(*engine));
    if(a->fd_read(0)!=0x4829 || sio_init(&io,a)){
        a->say("SOTTO TONE resources");
        return -10;
    }
    slim_enc_init(encoder);
    encoder->io=&io;
    ctx.api=a;ctx.encoder=encoder;ctx.resources=&io;ctx.last_poll_ms=0;
    ops.encode=st_encode;ops.flush=st_flush;
    ops.start=st_start;ops.poll=st_poll;
    ops.stop_capture=st_stop_capture;
    ops.abort=st_end;ops.end=st_end;
    if(sl_engine_begin(engine,0x5354544fu,0x4e453132u)){
        return -11;
    }
    /* Only an explicit RUN/RUN_COOP of this opt-in module reaches TX.
     * tx_on allocates a finite lease; mute analog PA until API29 FSK starts.
     */
    if(a->tx_on(9000u))return -5;
    if(a->tx_pa(0)!=0){a->tx_off();return -6;}
    started=a->ms();
    for(block=0;block<ST_TONE_BLOCKS;block++){
        for(i=0;i<ST_TONE_SAMPLES;i++){
            if(sl_pcm_sample(&engine->mic,st_tone_sample(block*ST_TONE_SAMPLES+i))){
                goto failure;
            }
        }
        /* Consume this one block before producing another; bounded queue. */
        while(engine->mic.consumed!=engine->mic.produced || engine->pending){
            if(st_cancelled(a)){cancelled=1;goto failure;}
            if(a->ms()-started>=8500u)goto failure;
            rc=sl_engine_tick(engine,&ops,&ctx);
            if(rc<0)goto failure;
            a->delay_us(300);
        }
    }
    if(sl_engine_release(engine,&ops,&ctx))goto failure;
    while(a->ms()-started<8500u){
        if(st_cancelled(a)){cancelled=1;goto failure;}
        rc=sl_engine_tick(engine,&ops,&ctx);
        if(rc<0)goto failure;
        if(rc==1){
            a->say("SOTTO TONE sent");
            return (int)engine->tx.encoded;
        }
        a->delay_us(500);
    }
failure:
    /* Both paths idempotently dekey; fail closed if anything is uncertain. */
    st_end(&ctx);
    a->say(cancelled?"SOTTO TONE cancel":"SOTTO TONE abort");
    return cancelled?-125:-12;
#endif
}
