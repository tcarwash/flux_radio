/* First attended live Sotto TX module. Experimental, not a receiver.
 * Use a dummy load and unplug the programming cable before PTT.
 * Never TX on module entry, and never synthesize RF completion.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#include "sotto_live_engine.h"
#ifdef SOTTO_GP0_WIENER_FIR
#include "sotto_gp0_wiener_fir_q15.h"
#endif
#ifdef SOTTO_GP0_WIENER_FIR
K6_MODULE_NAME("SOTTO FIR");
#else
K6_MODULE_NAME("SOTTO GP0");
#endif
/* The expanded 14,976-byte arena leaves 4,120 bytes after the module
 * image and host callback-stack reserve. Keep the engine+codec requirement
 * bounded at 4,080 bytes; this is a compile-time guard, not a runtime guess. */
typedef char sotto_gp0_scratch_budget_check[
    (sizeof(sl_engine_t)+sizeof(slim_enc_t)<=4080u)?1:-1];
typedef struct{
    const k6_api_t *a;
    slim_enc_t *enc;
    sotto_io_t *io;
    uint32_t last_poll;
#ifdef SOTTO_GP0_WIENER_FIR
    gp0_wiener_fir_state_t fir;
#endif
} live_ctx_t;
static int encode(void*v,const int16_t*p,uint8_t*f){
    live_ctx_t*c=v;
#ifdef SOTTO_GP0_WIENER_FIR
    /* sl_engine_tick owns this consumed ring block until encode returns;
     * filter it in place to avoid a second 80-sample stack buffer. */
    gp0_wiener_fir_q15_block(&c->fir,(int16_t*)p,SL_PCM_SAMPLES);
#endif
    int r=slim_encode10(c->enc,p,f);
    return c->io->fault?-1:r;
}
static int flush(void*v,uint8_t*f){
    live_ctx_t*c=v;int r=slim_flush(c->enc,f);
    return c->io->fault?-1:r;
}
static int start(void*v,const uint16_t*p){
    live_ctx_t*c=v;return c->a->fsk_async(0,p,33);
}
static int poll(void*v){
    live_ctx_t*c=v;uint32_t n=c->a->ms();
    if(n-c->last_poll<5)return 0;
    c->last_poll=n;return c->a->fsk_async(1,0,0);
}
static void stop(void*v){
    live_ctx_t*c=v;c->a->mic_stream(1,0,0);
}
static void end(void*v){
    live_ctx_t*c=v;c->a->tx_off();
}
int k6_main(const k6_api_t*a){
    unsigned room=0;
    uint8_t*scratch;
    sl_engine_t*s;slim_enc_t*e;sotto_io_t io;live_ctx_t c;sl_ops_t o;
    uint32_t t0,pressed,last_progress;
    uint8_t last_produced;
    int r;
    const char *error="GP0 OTHER";
    if(!a||a->level<30||a->abi!=1)return -3;
    scratch=a->scratch(&room);
    if(!scratch||((uintptr_t)scratch&3u)||room<sizeof(*s)+slim_enc_bytes()){
        a->say("SOTTO no RAM");return -8;
    }
    s=(sl_engine_t*)scratch;e=(slim_enc_t*)(scratch+sizeof(*s));
    if(a->fd_read(0)!=0x4829||sio_init(&io,a)){
        a->say("SOTTO MIC codec");
        return -10;
    }
    slim_enc_init(e);e->io=&io;
    c.a=a;c.enc=e;c.io=&io;c.last_poll=0;
#ifdef SOTTO_GP0_WIENER_FIR
    for(pressed=0;pressed<sizeof(c.fir.history)/sizeof(c.fir.history[0]);pressed++)
        c.fir.history[pressed]=0;
    c.fir.pos=0;
#endif
    o.encode=encode;o.flush=flush;o.start=start;o.poll=poll;
    o.stop_capture=stop;o.abort=end;o.end=end;
    // First bench build: a stable released PTT followed by a fresh
    // 60-ms press, not a UART transition, is required. The operator
    // must unplug the cable and attach a dummy load.
    a->lcd_text(0,"SOTTO GP0 / TX");
    a->lcd_flush();
    /* The mode-owned UI redraws separately; avoid synchronous LCD SPI from TX. */
    /* Both an already-held PTT and a new press must stay asserted for
     * 60 ms. Reject UART ownership or other keypad input immediately. */
    t0=a->ms();
    while(a->ms()-t0<30000u){
        r=a->mic_stream(3,0,0);
        if(r<0||a->key_down())return -4;
        if(r){
            for(pressed=0;pressed<12u;pressed++){
                a->delay_us(5000);
                if(a->mic_stream(3,0,0)!=1||a->key_down())break;
            }
            if(pressed==12u)goto ptt_ready;
        }else a->delay_us(5000);
    }
    return -4;
ptt_ready:
    if(sl_engine_begin(s,a->ticks()|1u,a->ms()))return -11;
    if(a->tx_on(25000u))return -5;
    if(a->mic_stream(7,0,0))goto fail;
    /* Physical GP0 tone tests needed 800ms after the RFIC MICIN ADC was
     * enabled to settle its bias. Do this BEFORE DMA starts, and ensure
     * PTT is still held: no RF is emitted while waiting. */
    a->delay_us(800000u);
    if(a->mic_stream(3,0,0)!=1 || a->key_down())goto fail;
    if(a->mic_stream(8,(uint8_t*)&s->mic,87260u)){error="GP0 START FAIL";goto fail;}
    t0=a->ms();last_progress=t0;last_produced=0;
    while(a->ms()-t0<20000u){
        r=a->mic_stream(3,0,0);
        if(r<0||a->key_down())goto fail;
        if(!r&&s->phase==SL_CAPTURE){
            if(sl_engine_release(s,&o,&c))goto fail;
        }
        if(s->phase==SL_CAPTURE){
            if(s->mic.produced!=last_produced){
                last_produced=s->mic.produced;last_progress=a->ms();
            }else if(a->ms()-last_progress>100u){error="GP0 STALL";goto fail;}
        }
        r=sl_engine_tick(s,&o,&c);
        if(r<0){error="GP0 ENGINE";goto fail;}
        if(r==1)return (int)s->tx.encoded;
        if(s->mic.produced==s->mic.consumed&&!s->pending)a->delay_us(300);
    }
fail:
    stop(&c);end(&c);a->lcd_text(0,error);a->lcd_flush();a->delay_us(2500000u);return -12;
}
