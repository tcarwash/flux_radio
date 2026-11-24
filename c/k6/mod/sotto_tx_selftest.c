/* Sotto module qualification: actual slim encoder + staged FDV1 packets.
 * This entry only generates synthetic PCM, encodes and checks packet
 * sequencing. It never calls any RF, microphone, timer or write API.
 * The encoder and packet functions are the intended production module core.
 * Build using tools/k6_sotto_module.py (explicit-context upstream adaptation).
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 5
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#include "sotto_tx_stage.h"

K6_MODULE_NAME("SOTTO TEST");
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
}
