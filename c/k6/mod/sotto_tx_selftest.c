/* Sotto module qualification: actual slim encoder + staged FDV1 packets.
 * This entry only generates synthetic PCM, encodes and checks packet
 * sequencing. It never calls any RF, microphone, timer or write API.
 * The encoder and packet functions are the intended production module core.
 * Build using tools/k6_sotto_module.py (explicit-context upstream adaptation).
 * SPDX-License-Identifier: MIT
 */
#ifdef SOTTO_HOST_RESOURCES
#define K6_MODULE_LEVEL 27
#else
#define K6_MODULE_LEVEL 5
#endif
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#ifdef SOTTO_LIVE_TEST
#include "sotto_live_engine.h"
#else
#include "sotto_tx_stage.h"
#endif

#ifdef SOTTO_LIVE_TEST
K6_MODULE_NAME("SOTTO LIVE");
#else
K6_MODULE_NAME("SOTTO TEST");
#endif
#define ST_BLOCKS 104u
/* The standard Flux host gives ordinary modules only 1152 bytes on
 * its dedicated stack. The encoder needs more, so the module explicitly
 * reserves its own stack at the end of scratch. No host-name exception,
 * no vector remap, and no fixed physical RAM address. */
#define STACK_RESERVE 2048u
#define STACK_GUARD 32u

static unsigned digest(unsigned h,const uint8_t *b,unsigned n){
    unsigned i;
    for(i=0;i<n;i++)h=(h^b[i])*16777619u;
    return h;
}
static void report(const k6_api_t *a,unsigned h){
    char line[]="SOTTO dry FNV 00000000";
    unsigned i;
    for(i=0;i<8;i++)line[14+i]="0123456789abcdef"[(h>>(28-i*4))&15u];
    a->say(line);
}
#ifdef SOTTO_LIVE_TEST
/* Dry-only backend: exercise real incremental codec and stable FIFO queue
 * with asynchronous start/poll, without ever opening an RF lease. */
typedef struct {
    slim_enc_t *enc;
    sotto_io_t *io;
    uint32_t digest;
    unsigned countdown;
} dry_ctx_t;
static int dry_encode(void *v,const int16_t *p,uint8_t *six){
    dry_ctx_t *c=v;int r=slim_encode10(c->enc,p,six);
    return c->io->fault?-1:r;
}
static int dry_flush(void *v,uint8_t *six){
    dry_ctx_t *c=v;int r=slim_flush(c->enc,six);
    return c->io->fault?-1:r;
}
static int dry_start(void *v,const uint16_t *p){
    dry_ctx_t *c=v;
    if(c->countdown)return -1;
    c->digest=digest(c->digest,(const uint8_t*)p,66);
    c->countdown=16;return 0;
}
static int dry_poll(void *v){
    dry_ctx_t *c=v;
    return c->countdown&&!--c->countdown;
}
static void dry_stop(void *v){(void)v;}
__attribute__((used,noinline))
static int sotto_dry_run(const k6_api_t *a,uint8_t *scratch){
    sl_engine_t *s=(sl_engine_t*)scratch;
    slim_enc_t *e=(slim_enc_t*)(scratch+sizeof(*s));
    sotto_io_t io;
    dry_ctx_t c;
    sl_ops_t ops;
    unsigned block,i,j;
    int rc;
    if(sio_init(&io,a))return -10;
    slim_enc_init(e);e->io=&io;
    c.enc=e;c.io=&io;c.digest=2166136261u;c.countdown=0;
    ops.encode=dry_encode;ops.flush=dry_flush;ops.start=dry_start;
    ops.poll=dry_poll;ops.stop_capture=dry_stop;ops.abort=dry_stop;ops.end=dry_stop;
    if(sl_engine_begin(s,0x534f5454u,0x4f544553u))return -11;
    for(block=0;block<ST_BLOCKS;block++){
        for(i=0;i<80;i++){
            j=(block*80u+i)%40u;
            if(sl_pcm_sample(&s->mic,(int16_t)(((int)(j<20?j:40-j)-10)*(400+(int)(block%8u)*20))))goto bad;
        }
        if(sl_engine_tick(s,&ops,&c)!=0)goto bad;
    }
    if(!s->tx.completed||sl_engine_release(s,&ops,&c))goto bad;
    for(i=0;i<200;i++){
        rc=sl_engine_tick(s,&ops,&c);
        if(rc<0)goto bad;
        if(rc==1)break;
    }
    if(i==200||s->tx.encoded!=26u||s->tx.completed!=6u)goto bad;
    report(a,c.digest);return (int)s->tx.encoded;
bad:
    sl_abort(&s->tx);a->say("SOTTO live dry-run failed");return -12;
}
#else
__attribute__((used,noinline))
static int sotto_dry_run(const k6_api_t *a,uint8_t *scratch){
    uint32_t blocks,i,j,h=2166136261u;
    uint8_t six[6];
    int16_t *x;
    sotto_stage_t *s;
    slim_enc_t *e;
    sotto_io_t io;
    int rc;
    s=(sotto_stage_t*)scratch;
    e=(slim_enc_t*)(void*)(scratch+sizeof(*s));
    if(sio_init(&io,a)){a->say("SOTTO tables invalid");return -10;}
    slim_enc_init(e);e->io=&io;
    /* The encoder consumes these samples before using w as FFT/search workspace. */
    x=e->w;
    if(sotto_stage_begin(s,112,0x534f5454u,0x4f544553u))return -11;
    for(blocks=0;blocks<ST_BLOCKS;blocks++){
        /* Integer, repeatable 200Hz triangle with changing amplitude. */
        for(i=0;i<80;i++){
            j=(blocks*80u+i)%40u;
            x[i]=(int16_t)(((int)(j<20?j:40-j)-10)*(400+(int)(blocks%8u)*20));
        }
        rc=slim_encode10(e,x,six);
        if(io.fault || (rc&&sotto_stage_record(s,six)))goto bad;
    }
    /* Encoder may emit delayed tail superframes. Bound flush independently. */
    for(i=0;i<4;i++){
        rc=slim_flush(e,six);
        if(io.fault || (rc&&sotto_stage_record(s,six)))goto bad;
        if(!rc)break;
    }
    if(i==4||s->frames!=26u||sotto_stage_seal(s,0,1))goto bad;
    while(s->phase!=SOTTO_STAGE_DONE){
        const uint16_t *w=sotto_stage_peek(s);
        if(!w)goto bad;
        h=digest(h,(const uint8_t*)w,66);
        /* Dry-run completion ONLY. Never use this ACK in real RF path. */
        if(sotto_stage_confirm(s,1))goto bad;
    }
    report(a,h);
    return (int)s->frames;
bad:
    sotto_stage_abort(s);
    a->say("SOTTO dry-run failed");return -12;
}

#endif

/* Thumb AAPCS: r4 is callee-saved by the complete call tree. The initial
 * push keeps the original stack 8-byte aligned and restores it on return.
 * Only this tiny entry and the allocation checks use the host stack. */
__attribute__((naked,noinline))
static int sotto_call_stack(void *top,const k6_api_t *a,uint8_t *scratch){
    __asm volatile("push {r4, lr}\n"
                   "mov r4, sp\n"
                   "mov sp, r0\n"
                   "mov r0, r1\n"
                   "mov r1, r2\n"
                   "bl sotto_dry_run\n"
                   "mov sp, r4\n"
                   "pop {r4, pc}\n");
}
int k6_main(const k6_api_t *a){
#ifdef SOTTO_HOST_RESOURCES
    uint32_t room=0;
    if(!a||a->abi!=1||a->level<27||!a->resource_open||!a->resource_read)return -3;
    uint8_t *scratch=a->scratch(&room);
    if(!scratch||((uintptr_t)scratch&3u)||room<
#ifdef SOTTO_LIVE_TEST
       sizeof(sl_engine_t)+slim_enc_bytes()
#else
       sizeof(sotto_stage_t)+slim_enc_bytes()
#endif
       ){
        a->say("SOTTO insufficient scratch");return -8;
    }
    return sotto_dry_run(a,scratch);
#else
    uint32_t room=0,need,i;
    uint8_t *scratch,*top,*guard;
    int rc;
    if(!a||a->abi!=1||a->level<5||!a->scratch||!a->fl_read||!a->say)return -3;
    scratch=a->scratch(&room);
    need=sizeof(sotto_stage_t)+slim_enc_bytes();
    if(!scratch||((uintptr_t)scratch&3u)||
       room<need+STACK_RESERVE+STACK_GUARD+7u){
        a->say("SOTTO insufficient scratch");return -8;
    }
    top=(uint8_t*)(((uintptr_t)scratch+room)&~(uintptr_t)7u);
    guard=top-STACK_RESERVE-STACK_GUARD;
    for(i=0;i<STACK_GUARD;i++)guard[i]=0xa5u;
    rc=sotto_call_stack(top,a,scratch);
    for(i=0;i<STACK_GUARD;i++)if(guard[i]!=0xa5u){
        a->say("SOTTO stack guard damaged");return -13;
    }
    return rc;
#endif
}
