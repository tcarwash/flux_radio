/* Experimental PRE-DECIMATOR GP0 POPCOUNT diagnostic over guarded RF.
 * Only launches after PTT held 60ms on qualified 443.500MHz.
 * 800ms RFIC MICIN settling, ~44ms of 4096 raw PDM group popcounts;
 * STOP/RELEASE MIC DMA before 86 finite L64 FSK packets. Never encodes Sotto.
 * No radio/firmware/SPI writes; a newly installed diagnostic module in a
 * separate verified slot is required before the operator can run this.
 * No TX on install or entry. Dummy load, PL2303 mic-cable DISCONNECTED.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_live_engine.h"
#include "gp0_popcount_air.h"

K6_MODULE_NAME("GP0 RAW AIR");

#define GPRAW_TEST_HZ 443500000u
#define GPRAW_GROUPS_HZ 87260u
#define GPRAW_MAX_TX_MS 24000u
#define GPRAW_MIN_PRESS_MS 60u
typedef char gpraw_pcm_is_644_bytes[(sizeof(sl_pcm_t)==644u)?1:-1];

static int gpraw_confirm_ptt(const k6_api_t *a){
    uint32_t start=a->ms();
    while(a->ms()-start<20000u){
        int r=a->mic_stream(3,0,0);
        if(r<0||a->key_down())return -4;
        if(r==1){
            unsigned i;
            for(i=0;i<12u;i++){
                a->delay_us(5000u);
                if(a->mic_stream(3,0,0)!=1||a->key_down())break;
            }
            if(i==12u)return 0;
        }else a->delay_us(5000u);
    }
    return -4;
}

int k6_main(const k6_api_t *a){
    uint8_t *scratch;
    uint32_t room=0,overall_started,capture_started,capture_started_tick;
    sl_pcm_t *ring;
    uint8_t *samples;
    uint16_t *words;
    uint32_t tag;
    uint16_t duration_ticks=0;

    int lease=0,route=0,capturing=0,fsks=0;
    int rc=-1;
    const char *reason="RAW AIR ERR";
    if(!a||a->abi!=1||a->level<30)return -3;
    if(a->fd_read(0)!=0x4829u)return -10;
    if(a->rx_hz()!=GPRAW_TEST_HZ)return -11; /* Explicit bench-only freq */
    scratch=a->scratch(&room);
    if(!scratch||((uintptr_t)scratch&3u)||
       room<sizeof(sl_pcm_t)+(uint32_t)GPRAW_SAMPLES+66u)
        return -8;
    ring=(sl_pcm_t*)scratch;
    samples=scratch+sizeof(*ring);
    words=(uint16_t*)(scratch+sizeof(*ring)+GPRAW_SAMPLES);
    a->lcd_text(0,"GP0 RAW AIR / TEST");
    a->lcd_text(1,"443.500MHz DUMMY LOAD");
    a->lcd_text(2,"Hold PTT until DONE");
    a->lcd_text(3,"Raw counts, NO SOTTO");
    a->lcd_flush();
    rc=gpraw_confirm_ptt(a);
    if(rc){reason="PTT REQUIRED";goto finish;}
    if(a->tx_on(25000u)){rc=-5;goto finish;}
    lease=1;
    overall_started=a->ms();
    if(a->mic_stream(7,0,0)){rc=-6;goto finish;}
    route=1;
    /* 800ms RFIC bias settle with 20ms physical PTT/key checks.
     * The lease does not authorize unattended RF after a released PTT. */
    for(unsigned settle=0;settle<40u;settle++){
        a->delay_us(20000u);
        if(a->mic_stream(3,0,0)!=1||a->key_down()){
            rc=-9;reason="PTT DURING BIAS";goto finish;
        }
    }
    for(unsigned i=0;i<sizeof(*ring);i++)((uint8_t*)ring)[i]=0;
    /* One host-managed GP0 producer, with protected raw DMA ring in the
     * pre-existing firmware's module arena; no new RAM0 allocation. */
    if(a->mic_stream(9,ring,GPRAW_SAMPLES)){rc=-7;reason="RAW API9 UNAVAILABLE";goto finish;}
    capturing=1;
    capture_started_tick=a->ticks();
    capture_started=a->ms();
    /* GP0 IRQ9 writes all 4096 sequential count bytes in module arena.
     * The full sample set is sealed before packets begin. No PCM queue drain.
     * A persistent bounded watchdog remains effective on capture faults. */
    while(!ring->produced){
        if(ring->lost){rc=-12;reason="RAW IRQ LOST";goto finish;}
        if(a->ms()-capture_started>350u){rc=-13;reason="RAW TIMEOUT";goto finish;}
        if(a->mic_stream(3,0,0)!=1||a->key_down()){
            rc=-9;reason="PTT RELEASE";goto finish;
        }
        a->delay_us(100u);
    }
    /* ticks() is a wrapping 16-bit 100us clock; our capture is <1.2s.
     * 0.1ms resolution is enough to discriminate a 3-4% GP0 rate error. */
    duration_ticks=(uint16_t)(a->ticks()-capture_started_tick);
    if(ring->lost||!duration_ticks||duration_ticks>3500u){rc=-15;goto finish;}
    /* No IRQ/PCM producer continues while sealing or transmitting RF. */
    if(a->mic_stream(1,0,0)!=0){rc=-22;reason="RAW STOP FAIL";goto finish;}
    capturing=0;route=0;
    tag=a->ticks()^(a->ms()<<8)^0xd901b00fu;
    if(!tag)tag=1u;
    for(unsigned seq=0;seq<GPRAW_PACKETS;seq++){
        if(a->ms()-overall_started>GPRAW_MAX_TX_MS){rc=-16;reason="RAW AIR LEASE";goto finish;}
        if(a->mic_stream(3,0,0)!=1||a->key_down()){
            rc=-17;reason="RAW AIR PTT";goto finish;
        }
        if(gpraw_packet_words(words,samples,tag,duration_ticks,seq)!=33){
            rc=-18;goto finish;
        }
        if(a->fsk_async(0,words,33)){rc=-19;reason="RAW AIR FSK";goto finish;}
        fsks=1;
        uint32_t start=a->ms();
        for(;;){
            int r;
            if(a->ms()-start>950u||a->ms()-overall_started>GPRAW_MAX_TX_MS){
                rc=-20;reason="RAW FSK TIMEOUT";goto finish;
            }
            if(a->mic_stream(3,0,0)!=1||a->key_down()){
                rc=-17;reason="RAW AIR PTT";goto finish;
            }
            r=a->fsk_async(1,0,0);
            if(r<0){rc=-21;reason="RAW FSK ERROR";goto finish;}
            if(r==1){fsks=0;break;}  /* physical RFIC completion, not guessed */
            a->delay_us(5000u);
        }
    }
    rc=(int)GPRAW_PACKETS;
finish:
    if(capturing||route)a->mic_stream(1,0,0);
    if(fsks)a->fsk_async(2,0,0);
    if(lease)a->tx_off();     /* Always restore RX and radio registers */
    if(rc==(int)GPRAW_PACKETS){
        a->lcd_text(0,"GP0 RAW TX DONE");
        a->lcd_text(1,"86 packets sent");
        a->lcd_text(2,"Check SDR CRCs");
        a->lcd_text(3,"4096 counts / 64bits");
    }else{
        a->lcd_text(0,"GP0 RAW AIR FAIL");
        a->lcd_text(1,reason);
        a->lcd_text(2,"No data claimed");
        a->lcd_text(3,"Check bench logs");
    }
    a->lcd_flush();
    return rc;
}
