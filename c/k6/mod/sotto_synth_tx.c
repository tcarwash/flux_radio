/* Synthetic 8-kHz PCM -> actual Cortex-M0 Sotto encoder -> async RF FIFO.
 * Deliberate serial RUN only: inert unless compiled with explicit ARM flag.
 * ADC and mic IRQ are bypassed, but codec tables/SPI and FSK radio are REAL.
 * A 10 ms wall-clock producer catches up after slow encode calls; therefore
 * it detects the same four-PCM-block backlog pressure as a live producer,
 * without substituting a software-only FSK acknowledgment.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 29
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#include "sotto_live_engine.h"
K6_MODULE_NAME("SOTTO SYNTH");
#ifndef SOTTO_SYNTH_TX_ARMED
#define SOTTO_SYNTH_TX_ARMED 0
#endif
#ifndef SOTTO_SYNTH_PROFILE
#define SOTTO_SYNTH_PROFILE 1
#endif
#if SOTTO_SYNTH_PROFILE > 3
#error "Synthetic PCM profile must be 0..3"
#endif
#if SOTTO_SYNTH_TX_ARMED
#define SYNTH_BLOCKS 104u
#define SYNTH_MAX_TX_MS 9000u
#ifndef SYNTH_HZ
#define SYNTH_HZ 443500000u
#endif
_Static_assert(sizeof(sl_engine_t)+sizeof(slim_enc_t)<=4080u,"Sotto synth needs expanded arena");
typedef struct {
    const k6_api_t *a;
    slim_enc_t *enc;
    sotto_io_t *io;
    uint32_t last_poll;
    uint32_t encode_max_ticks;
} synth_ctx_t;
static int enc(void*v,const int16_t *pcm,uint8_t *out){
    synth_ctx_t*c=(synth_ctx_t*)v;
    uint32_t t=c->a->ticks();
    int r=slim_encode10(c->enc,pcm,out);
    uint32_t dt=(c->a->ticks()-t)&65535u;
    if(dt>c->encode_max_ticks)c->encode_max_ticks=dt;
    return c->io->fault?-1:r;
}
static int fl(void*v,uint8_t *out){
    synth_ctx_t*c=(synth_ctx_t*)v;
    int r=slim_flush(c->enc,out);
    return c->io->fault?-1:r;
}
static int start(void*v,const uint16_t *words){
    synth_ctx_t*c=(synth_ctx_t*)v;
    return c->a->fsk_async(0,words,33);
}
static int poll(void*v){
    synth_ctx_t*c=(synth_ctx_t*)v;
    uint32_t now=c->a->ms();
    if(now-c->last_poll<5u)return 0;
    c->last_poll=now;
    return c->a->fsk_async(1,0,0);
}
static void no_mic(void*v){(void)v;}
static void dekey(void*v){
    synth_ctx_t*c=(synth_ctx_t*)v;
    c->a->tx_off();
}
static int16_t triangle(uint32_t p){
    int32_t x=(int32_t)(p>>20);
    x=(x<2048)?x:(4095-x);
    return (int16_t)((x-1024)*6);
}
static int16_t synth_sample(unsigned index,unsigned block,uint32_t *phase1,uint32_t *phase2,uint32_t *chirp){
#if SOTTO_SYNTH_PROFILE == 0
    (void)index;(void)block;(void)phase1;(void)phase2;(void)chirp;
    return 0;
#else
    int32_t v;
    (void)index;
    *phase1+=*chirp;
#if SOTTO_SYNTH_PROFILE == 2
    *chirp+=167772u;
#else
    (void)chirp;
#endif
    v=triangle(*phase1);
#if SOTTO_SYNTH_PROFILE == 1
    *phase2+=467077693u;
    v+=triangle(*phase2)/3;
#endif
#if SOTTO_SYNTH_PROFILE == 3
    if((block/8u)&1u)v=0;
#endif
    return (int16_t)v;
#endif
}
/* Only after TX has been dekeyed: compact status on the existing console. */
static void report(const k6_api_t*a,char category,uint32_t number){
    static const char hex[]="0123456789ABCDEF";
    char b[]="SYN:X:00000000";unsigned i;
    b[4]=category;
    for(i=0;i<8;i++)b[6+i]=hex[(number>>(28u-4u*i))&15u];
    a->say(b);
}
#endif
int k6_main(const k6_api_t *a){
    if(!a||a->abi!=1||a->level<29)return -3;
#if !SOTTO_SYNTH_TX_ARMED
    a->say("SOTTO SYNTH inert");return -9;
#else
    unsigned room=0, produced=0, highwater=0, i;
    uint8_t *scratch;
    sl_engine_t *s;
    slim_enc_t *e;
    sotto_io_t io;
    synth_ctx_t c;
    sl_ops_t ops;
    uint32_t t0,phase1=0,phase2=0;
    uint32_t chirp=171798692u;
    int r;
    if(a->rx_hz()!=SYNTH_HZ||a->key_down()||!a->fsk_async||!a->tx_off||!a->tx_on)return -4;
    scratch=a->scratch(&room);
    if(!scratch||((uintptr_t)scratch&3u)||room<sizeof(*s)+slim_enc_bytes())return -8;
    s=(sl_engine_t*)scratch;
    e=(slim_enc_t*)(scratch+sizeof(*s));
    if(a->fd_read(0)!=0x4829||sio_init(&io,a))return -10;
    slim_enc_init(e);e->io=&io;
    c.a=a;c.enc=e;c.io=&io;c.last_poll=0;
    c.encode_max_ticks=0;
    ops.encode=enc;ops.flush=fl;ops.start=start;ops.poll=poll;
    ops.stop_capture=no_mic;ops.abort=dekey;ops.end=dekey;
    if(sl_engine_begin(s,a->ticks()|1u,a->ms()))return -11;
    if(a->tx_on(SYNTH_MAX_TX_MS)!=0)return -5;
    if(a->tx_pa(0)!=0)goto fail;
    t0=a->ms();
    for(;;){
        uint32_t now=a->ms();
        if(a->key_down()||now-t0>=SYNTH_MAX_TX_MS-400u)goto fail;
        /* Catch up a wall-clock producer. Never make time "pause" during
         * codec work: a slow encoder must see four-block queue overflow. */
        while(produced<SYNTH_BLOCKS&&now-t0>=10u*(produced+1u)){
            for(i=0;i<80u;i++){
                if(sl_pcm_sample(&s->mic,synth_sample(produced*80u+i,produced,&phase1,&phase2,&chirp)))goto fail;
            }
            produced++;
            {unsigned depth=(uint8_t)(s->mic.produced-s->mic.consumed);
             if(depth>highwater)highwater=depth;}
        }
        if(produced==SYNTH_BLOCKS&&s->phase==SL_CAPTURE){
            if(sl_engine_release(s,&ops,&c))goto fail;
        }
        r=sl_engine_tick(s,&ops,&c);
        if(r<0)goto fail;
        if(r==1){
            report(a,'M',c.encode_max_ticks);
            report(a,'Q',highwater);
            return (int)s->tx.encoded;
        }
        if(s->mic.produced==s->mic.consumed&&!s->pending)a->delay_us(250u);
    }
fail:
    a->fsk_async(2,0,0);
    a->tx_off();
    report(a,'M',c.encode_max_ticks);
    report(a,'Q',highwater);
    a->say("SYNTH FAIL");
    return -12;
#endif
}
