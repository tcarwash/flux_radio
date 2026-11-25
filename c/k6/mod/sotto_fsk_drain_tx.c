/* API31 native B-chip FSK tail-guard experiment.
 * Adds sacrificial post-CRC bytes to a known synthetic 1kHz Sotto fixture.
 * Runs ONLY on explicit module launch at the test frequency/dummy load.
 * No microphone, streaming PCM, flash access or background TX.
 * SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 31
#endif
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#ifndef SOTTO_DRAIN_LIVE_ENCODE
#define SOTTO_DRAIN_LIVE_ENCODE 0
#endif
#if SOTTO_DRAIN_LIVE_ENCODE
K6_MODULE_NAME("SOTTO PCM RF");
#else
K6_MODULE_NAME("SOTTO DRAIN");
#endif

#ifndef SOTTO_DRAIN_ARMED
#define SOTTO_DRAIN_ARMED 0
#endif
#if SOTTO_DRAIN_ARMED
#include "sotto_live_session.h"
#if SOTTO_DRAIN_LIVE_ENCODE
#include "sotto_m0rt.h"
#include "sotto_tx_codec_io.h"
#include "sotto_slim_context.inc"
#include "../../fixed/fx_tables.c"
#include "sotto_tone_pcm.h"
#else
#include "sotto_tone_bits.h"
#endif
#ifndef SOTTO_DRAIN_EXTRA_WORDS
#define SOTTO_DRAIN_EXTRA_WORDS 10u
#endif
#ifndef SOTTO_DRAIN_HOLD_MS
#define SOTTO_DRAIN_HOLD_MS 0u
#endif
#if SOTTO_DRAIN_EXTRA_WORDS > 16u
#error "Cap extra guard at 16 words; do not exceed experimental FIFO margin"
#endif
#if SOTTO_DRAIN_HOLD_MS > 45u
#error "Cap post-IRQ PA hold at 45ms"
#endif
#define DRAIN_WORDS (33u+SOTTO_DRAIN_EXTRA_WORDS)
#ifndef SOTTO_DRAIN_KEEP_PA
#define SOTTO_DRAIN_KEEP_PA 0
#endif
#ifndef SOTTO_DRAIN_SETTLE_MS
#define SOTTO_DRAIN_SETTLE_MS 0u
#endif
#ifndef SOTTO_DRAIN_INTERFRAME_MS
#define SOTTO_DRAIN_INTERFRAME_MS 0u
#endif
#ifndef SOTTO_DRAIN_DEKEY_BETWEEN
#define SOTTO_DRAIN_DEKEY_BETWEEN 0
#endif
#if SOTTO_DRAIN_DEKEY_BETWEEN && !SOTTO_DRAIN_KEEP_PA
#error "PA de-key test is only meaningful with prefill PA-on"
#endif
#if SOTTO_DRAIN_SETTLE_MS > 30u || SOTTO_DRAIN_INTERFRAME_MS > 100u
#error "Bound the lab TX dwell times"
#endif
#ifndef SOTTO_DRAIN_PREFILL_WORDS
#define SOTTO_DRAIN_PREFILL_WORDS DRAIN_WORDS
#endif
#ifndef SOTTO_DRAIN_REFILL_MS
#define SOTTO_DRAIN_REFILL_MS 65u
#endif
#if SOTTO_DRAIN_PREFILL_WORDS < 12u || SOTTO_DRAIN_PREFILL_WORDS > DRAIN_WORDS
#error "Invalid prefill word count"
#endif
#if SOTTO_DRAIN_REFILL_MS < 35u || SOTTO_DRAIN_REFILL_MS > 90u
#error "Invalid RF refill timing"
#endif
static const uint8_t regs[9]={0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d};
static const uint16_t vals[9]={0x8000,0x00f8,0x00e1,0x0095,0xc194,
                                  0x076b,0x3e36,0x5625,(uint16_t)((DRAIN_WORDS*2u-1u)<<8)};
static int cancel(const k6_api_t *a){
    return a->key_down() || (a->cancel_poll&&a->cancel_poll()==1);
}
static void restore(const k6_api_t *a,const uint16_t saved[9]){
    unsigned i;
    (void)a->tx_pa(0);
    (void)a->fd_write(0x59,0x00f8);
    (void)a->fd_write(0x3f,0);
    (void)a->fd_write(0x02,0);
    for(i=9;i>0;i--)(void)a->fd_write(regs[i-1],saved[i-1]);
}
static int frame(const k6_api_t *a,const uint16_t *words,unsigned frame_idx,unsigned *elapsed){
    unsigned i, t;
#if SOTTO_DRAIN_PREFILL_WORDS < DRAIN_WORDS
    unsigned filled=0;
#endif
    /* Keep PA muted while preparing the FIFO. */
#if SOTTO_DRAIN_KEEP_PA
#if SOTTO_DRAIN_DEKEY_BETWEEN
    /* Re-key before FIFO preload; never start an RF frame with muted PA. */
    if(frame_idx && a->tx_pa(1))return -6;
#endif
#else
    if(a->tx_pa(0))return -6;
#endif
    if(a->fd_write(0x59,0x00f8) || a->fd_write(0x02,0))return -7;
    for(i=0;i<9;i++)if(a->fd_write(regs[i],vals[i]))return -8;
    if(a->fd_read(0x5d)!=vals[8])return -9;
    if(a->fd_write(0x59,0x80f8) || a->fd_write(0x59,0x00f8))return -10;
    for(i=0;i<SOTTO_DRAIN_PREFILL_WORDS;i++){
        uint16_t word;
        if(cancel(a))return -11;
        word=(i<33u)?words[i]:(i&1u?0xaaaa:0x5555);
        if(a->fd_write(0x5f,word))return -12;
    }
    if(a->fd_read(0x0c)&1u)return -13;
#if SOTTO_DRAIN_SETTLE_MS
    {
        unsigned wait=a->ms();
        while(a->ms()-wait<SOTTO_DRAIN_SETTLE_MS){
            if(cancel(a))return -22;
            a->delay_us(1000u);
        }
    }
#endif
#if SOTTO_DRAIN_KEEP_PA
    if(cancel(a) || a->fd_write(0x59,0x08f8))return -14;
#else
    if(cancel(a) || a->tx_pa(1) || a->fd_write(0x59,0x08f8))return -14;
#endif
    t=a->ms();
    while(a->ms()-t<400u){
        if(cancel(a) || (a->fd_read(0x30)&2u)==0u)return -15;
#if SOTTO_DRAIN_PREFILL_WORDS < DRAIN_WORDS
        if(!filled && a->ms()-t>=SOTTO_DRAIN_REFILL_MS){
            for(i=SOTTO_DRAIN_PREFILL_WORDS;i<DRAIN_WORDS;i++){
                uint16_t word=(i<33u)?words[i]:(i&1u?0xaaaau:0x5555u);
                if(cancel(a) || a->fd_write(0x5f,word))return -21;
            }
            filled=1;
        }
#endif
        if(a->fd_read(0x0c)&1u){
            *elapsed=a->ms()-t;
#if SOTTO_DRAIN_HOLD_MS
            {
                unsigned hold=a->ms();
                while(a->ms()-hold<SOTTO_DRAIN_HOLD_MS){
                    if(cancel(a))return -16;
                    a->delay_us(500u);
                }
            }
#endif
            return 0;
        }
        a->delay_us(2500u);
    }
    return -17;
}
static void report(const k6_api_t *a,unsigned n,unsigned ms){
    char line[]="DRAIN 0 000hex";
    static const char h[]="0123456789ABCDEF";
    line[6]='0'+(char)n;
    line[8]=h[(ms>>8)&15u];
    line[9]=h[(ms>>4)&15u];
    line[10]=h[ms&15u];
    a->say(line);
}
#endif
int k6_main(const k6_api_t *a){
    if(!a||a->abi!=1||a->level<K6_MODULE_LEVEL)return -3;
#if !SOTTO_DRAIN_ARMED
    a->say("SOTTO DRAIN inert");return -9;
#else
    unsigned room=0,i,j,ms;
    uint8_t *p;
    sotto_live_t *s;
    uint16_t saved[9];
    const uint16_t *w;
    int result;
#if SOTTO_DRAIN_LIVE_ENCODE
    unsigned block,flush_count;
    slim_enc_t *encoder;
    int16_t *pcm;
    uint8_t six[6];
    sotto_io_t io;
#endif
    if(a->rx_hz()!=433500000u || cancel(a) || a->fd_read(0)!=0x4829u)return -4;
    p=a->scratch(&room);
    if(!p || ((uintptr_t)p&3u)||room<sizeof(*s))return -8;
    s=(sotto_live_t*)p;
    if(sl_begin(s,0x5354544fu,0x4e453132u))return -11;
#if SOTTO_DRAIN_LIVE_ENCODE
    /* Encode PCM while still receiving: no TX lease or PA is active. */
    if(room<sizeof(*s)+slim_enc_bytes()+ST_TONE_SAMPLES*sizeof(int16_t))return -8;
    encoder=(slim_enc_t*)(p+sizeof(*s));
    pcm=(int16_t*)(p+sizeof(*s)+slim_enc_bytes());
    if(sio_init(&io,a)){a->say("SOTTO PCM resources");return -10;}
    slim_enc_init(encoder);encoder->io=&io;
    for(block=0;block<ST_TONE_BLOCKS;block++){
        for(i=0;i<ST_TONE_SAMPLES;i++)
            pcm[i]=st_tone_sample(block*ST_TONE_SAMPLES+i);
        result=slim_encode10(encoder,pcm,six);
        if(result<0 || result>1 || io.fault)return -13;
        if(result && sl_push(s,six))return -14;
        if(cancel(a))return -15;
    }
    for(flush_count=0;flush_count<4;flush_count++){
        result=slim_flush(encoder,six);
        if(result<0 || result>1 || io.fault)return -13;
        if(!result)break;
        if(sl_push(s,six))return -14;
    }
    if(flush_count==4)return -13;
#else
    for(i=0;i<8;i++)if(sl_push(s,st_tone_bits+6u*i))return -11;
#endif
    if(sl_finish(s) || s->count!=3u || s->encoded!=8u)return -11;
    result=a->tx_on(2800u);
    if(result)return -50+result;
    if(a->tx_pa(SOTTO_DRAIN_KEEP_PA?1u:0u)){a->tx_off();return -6;}
    for(i=0;i<9;i++)saved[i]=a->fd_read(regs[i]);
    if((saved[0]&0xa000u)||(saved[1]&0x0800u)){
        a->tx_off();return -7;
    }
    result=0;
    for(j=0;(w=sl_peek(s))!=0;j++){
        if(j>=3){result=-18;break;}
        if((result=frame(a,w,j,&ms)))break;
#if SOTTO_DRAIN_KEEP_PA
        /* Prior FSK-13/14 PHY left PA enabled between guarded bursts. */
        if(a->fd_write(0x59,0x00f8)){result=-23;break;}
#if SOTTO_DRAIN_DEKEY_BETWEEN
        if(a->tx_pa(0)){result=-25;break;}
#endif
        {
            unsigned wait=a->ms();
            while(a->ms()-wait<SOTTO_DRAIN_INTERFRAME_MS){
                if(cancel(a)){result=-24;break;}
                a->delay_us(1000u);
            }
            if(result)break;
        }
#else
        /* API29-style full reset and PA-off after each burst. */
        restore(a,saved);
#endif
        report(a,j,ms);
        if(sl_ack(s,1)){result=-19;break;}
    }
    restore(a,saved);
    a->tx_off();
    if(result){a->say("SOTTO DRAIN abort");return result;}
    a->say("SOTTO DRAIN sent");return (int)s->encoded;
#endif
}
