/* TX-15Y isolated FD6818B/BK4829 DTMF dual-tone RF source probe.
 * OFFLINE/RADIO-INERT BY DEFAULT. No tests on live device implied.
 * REG_72 is tone2 frequency only in DTMF mode (REG_3F=0x0800);
 * in native-FSK mode the same register controls baud. NEVER run while
 * another FIFO/FSK bench is active. Shared queue lease + canonical
 * serial_lock required before ANY physical trial.
 *
 * Hardware fixture test, when separately authorized: 9 fixed intervals
 * select tone1, tone2, both and silence, while RF PA stays keyed. The
 * first interval uses the existing non-FSK workmode as control. One
 * compile-time variant leaves REG24 alone; another reproduces the
 * documented B-driver DTMF detector setup (read/restore REG24).
 * No arbitrary RF-IQ, mode compatibility or TX2 proof claimed.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TX15Y_BENCH_ENABLE
#define TX15Y_BENCH_ENABLE 0
#endif
#ifndef TX15Y_RF_ENABLE
#define TX15Y_RF_ENABLE 0
#endif
#ifndef TX15Y_FIXTURE_ACK
#define TX15Y_FIXTURE_ACK 0
#endif
#ifndef TX15Y_WATCHDOG_ACK
#define TX15Y_WATCHDOG_ACK 0
#endif
#ifndef TX15Y_DTMF_DETECT_SETUP
#define TX15Y_DTMF_DETECT_SETUP 0
#endif
#ifndef TX15Y_HZ
#define TX15Y_HZ 433500000u
#endif

#if TX15Y_RF_ENABLE && (!TX15Y_BENCH_ENABLE || !TX15Y_FIXTURE_ACK || !TX15Y_WATCHDOG_ACK)
#error "TX15Y RF requires guarded bench, operator fixture ACK and independent TX watchdog"
#endif
#if TX15Y_HZ != 433500000u
#error "TX15Y radio probe is confined to original 433.500-MHz dummy-load fixture"
#endif
#if TX15Y_DTMF_DETECT_SETUP != 0 && TX15Y_DTMF_DETECT_SETUP != 1
#error "TX15Y variant must select only 0/1 optional DTMF detector setup"
#endif

#define R_MODE 0x3fu
#define R_DTMF_DETECT 0x24u
#define R_GAIN 0x70u
#define R_TONE1 0x71u
#define R_TONE2_BAUD 0x72u
#define WORK_MODE_DTMF 0x0800u
#define DTMF_DETECT_WORD 0xc17fu  /* 0x807f|(130u<<7), B driver */
#define TONE1_WORD 8074u          /* 782 Hz, TX15S response-calibrated */
#define TONE2_WORD 14702u         /* 1424 Hz, distinct spectral line */
#define GAIN_CODE 24u             /* known safe TX15W gain */
#define TONE1_ON ((uint16_t)(0x8000u|(GAIN_CODE<<8)))
#define TONE2_ON ((uint16_t)(0x0080u|GAIN_CODE))
#define BOTH_ON ((uint16_t)(TONE1_ON|TONE2_ON))
#define LEASE_MS 1800u
#define LEAD_US 70000u
#define N_SEG 9u

#if TX15Y_BENCH_ENABLE
/* Intervals: control T1, DTMF T1, T2, both, off, T2, T1, both, off */
static const uint32_t segment_us[N_SEG]={
    120000u,120000u,120000u,120000u,80000u,
    120000u,120000u,120000u,90000u
};
static const uint16_t segment_gain[N_SEG]={
    TONE1_ON,TONE1_ON,TONE2_ON,BOTH_ON,0u,
    TONE2_ON,TONE1_ON,BOTH_ON,0u
};
static char *put(char *p,const char *s) {
    while(*s)*p++=*s++;
    return p;
}
static char *dec(char *p,uint32_t n) {
    static const uint32_t powers[]={
        1000000000u,100000000u,10000000u,1000000u,
        100000u,10000u,1000u,100u,10u,1u
    };
    uint32_t i,leading=0;
    for(i=0;i<10;i++) {
        uint32_t d=0;
        while(n>=powers[i]){n-=powers[i];d++;}
        if(d||leading||i==9){*p++=(char)('0'+d);leading=1;}
    }
    return p;
}
static int set_and_check(const k6_api_t *api,uint8_t r,uint16_t word) {
    if(api->fd_write(r,word)!=0)return -12;
    if(api->fd_read(r)!=word)return -14;
    return 0;
}
#endif

int k6_main(const k6_api_t *api) {
    if(api->abi!=1 || api->level<3 || api->fd_read(0)!=0x4829u)return -3;
#if !TX15Y_BENCH_ENABLE
    api->say("tx15y inert; offline test build");return -9;
#else
    uint16_t saved_mode=0,saved_gain=0;
    uint16_t saved_t1=0,saved_t2=0;
#if TX15Y_DTMF_DETECT_SETUP
    uint16_t saved_detect=0;
#endif
    uint32_t i;
    int keyed=0,snapshot=0,ret=0,restore_good=1;
    char line[63],*p;

    if(api->rx_hz()!=TX15Y_HZ || api->key_down())return -2;
    /* Radio still configured for unknown work-mode => refuse to touch. */
    if(KM_RSSI_DBM(api->fd_read(0x67u))>-90)return -7;
    api->delay_us(50000u);
    if(KM_RSSI_DBM(api->fd_read(0x67u))>-90)return -7;
    if(api->tx_on(LEASE_MS)!=0)return -5;
    keyed=1;
    if(api->tx_pa(0)!=0){ret=-12;goto done;}
    saved_mode=api->fd_read(R_MODE);
#if TX15Y_DTMF_DETECT_SETUP
    saved_detect=api->fd_read(R_DTMF_DETECT);
#endif
    saved_gain=api->fd_read(R_GAIN);
    saved_t1=api->fd_read(R_TONE1);
    saved_t2=api->fd_read(R_TONE2_BAUD);
    /* Reject conflicting modem/DTMF configuration with *no register
     * writes at all*: even re-writing the same REG72 FSK baud word
     * could interfere with an existing modem. We have modified no
     * registers yet, so no REG-level cleanup is needed on this branch.
     */
    if(saved_mode!=0u){ret=-17;goto done;}
    snapshot=1;
    if((ret=set_and_check(api,R_GAIN,0u))!=0)goto done;
    if((ret=set_and_check(api,R_TONE1,TONE1_WORD))!=0)goto done;
    /* Keep REG72 untouched in ordinary work mode: it may be FSK baud.
     * The prospective tone2 write occurs ONLY after DTMF mode is
     * explicitly selected and checked.
     */
    api->delay_us(LEAD_US);

    /* Segment zero is control in prior non-DTMF work mode. */
    if((ret=set_and_check(api,R_GAIN,segment_gain[0]))!=0)goto done;
#if TX15Y_RF_ENABLE
    if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
    api->delay_us(segment_us[0]);
    /* Now isolate the work-mode effect; no FSK FIFO is involved. */
#if TX15Y_DTMF_DETECT_SETUP
    if((ret=set_and_check(api,R_DTMF_DETECT,DTMF_DETECT_WORD))!=0)goto done;
#endif
    if((ret=set_and_check(api,R_GAIN,0u))!=0)goto done;
    if((ret=set_and_check(api,R_MODE,WORK_MODE_DTMF))!=0)goto done;
    if((ret=set_and_check(api,R_TONE2_BAUD,TONE2_WORD))!=0)goto done;
    for(i=1;i<N_SEG;i++) {
        if(api->key_down()){ret=-4;goto done;}
        if((ret=set_and_check(api,R_GAIN,segment_gain[i]))!=0)goto done;
        api->delay_us(segment_us[i]);
    }
    ret=(int)N_SEG;
done:
    if(keyed){
        (void)api->tx_pa(0);
        if(snapshot){
            if(api->fd_write(R_GAIN,0u)!=0)restore_good=0;
            if(api->fd_write(R_TONE1,saved_t1)!=0)restore_good=0;
            if(api->fd_write(R_TONE2_BAUD,saved_t2)!=0)restore_good=0;
#if TX15Y_DTMF_DETECT_SETUP
            if(api->fd_write(R_DTMF_DETECT,saved_detect)!=0)restore_good=0;
#endif
            if(api->fd_write(R_MODE,saved_mode)!=0)restore_good=0;
            if(api->fd_write(R_GAIN,saved_gain)!=0)restore_good=0;
            if(api->fd_read(R_GAIN)!=saved_gain||
               api->fd_read(R_TONE1)!=saved_t1||
               api->fd_read(R_TONE2_BAUD)!=saved_t2||
               api->fd_read(R_MODE)!=saved_mode)restore_good=0;
#if TX15Y_DTMF_DETECT_SETUP
            if(api->fd_read(R_DTMF_DETECT)!=saved_detect)restore_good=0;
#endif
        }
        api->tx_off();
    }
    if(!restore_good)return -16;
    p=put(line,"tx15y n=");p=dec(p,ret<0?0:(uint32_t)ret);
    p=put(p," dtmf_setup=");p=dec(p,TX15Y_DTMF_DETECT_SETUP);
    p=put(p," pa=");p=dec(p,TX15Y_RF_ENABLE);
    *p=0;api->say(line);
    return ret;
#endif
}
