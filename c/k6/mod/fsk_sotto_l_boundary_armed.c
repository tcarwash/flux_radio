/* Flux FD6818B FSK-10 L 64-byte two-frame boundary lab.
 * Default is inert. Enabled build: ONE bounded TX lease, exactly TWO
 * 32-word (64-byte) hardware FSK frames. PA stays enabled at the boundary.
 * A long zero-frequency-state marker straddles the frame boundary.
 * The FSK modem itself is STOPPED, FIFO cleared/reloaded and restarted;
 * this experiment measures THAT break. It does not implement continuous
 * FIFO refill or guarantee no RF leakage. Dummy-load use only.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef FSK_BOUNDARY_ENABLE
#define FSK_BOUNDARY_ENABLE 1
#endif
#ifndef FSK_BOUNDARY_FIXTURE_ACK
#define FSK_BOUNDARY_FIXTURE_ACK 1
#endif
#ifndef FSK_BOUNDARY_WATCHDOG_ACK
#define FSK_BOUNDARY_WATCHDOG_ACK 1
#endif
#if FSK_BOUNDARY_ENABLE && (!FSK_BOUNDARY_FIXTURE_ACK || !FSK_BOUNDARY_WATCHDOG_ACK)
#error "Two-frame FSK TX requires explicitly verified dummy load and independent watchdog"
#endif
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "fsk_lab_vector.h"

#define FSK_NUM_FRAMES 2u
#define FSK_WORDS 32u
#define FSK_FREQUENCY_HZ 433500000u
#define FSK_GAIN_CODE 21u
#define FSK_BAUD_CODE 0xc194u
#define FSK_MAX_TX_MS 1900u
#define FSK_FRAME_TIMEOUT_MS 700u

#define R_CHIP 0x00u
#define R_WORK 0x3fu
#define R_CONTROL 0x59u
#define R_PREAMBLE 0x58u
#define R_GAIN 0x70u
#define R_BAUD 0x72u
#define R_SYNC0 0x5au
#define R_SYNC1 0x5bu
#define R_SYNC2 0x5cu
#define R_LENGTH 0x5du
#define R_FIFO 0x5fu
#define R_CLEAR_IRQ 0x02u
#define R_STATUS 0x0cu
#define R_STATE 0x30u
#define R_RSSI 0x67u
#if FSK_BOUNDARY_ENABLE
static const uint8_t restore_regs[]={
    R_WORK,R_CONTROL,R_PREAMBLE,R_GAIN,R_BAUD,R_SYNC0,R_SYNC1,R_SYNC2,R_LENGTH
};
#define N_RESTORE (sizeof(restore_regs)/sizeof(restore_regs[0]))
static int wr(const k6_api_t *a,uint8_t r,uint16_t v) {
    return a->fd_write(r,v)==0 ? 0 : -1;
}
static int safe(const k6_api_t *a) {
    return !a->key_down() && !km_power_switch_off();
}
static int free_channel(const k6_api_t *a) {
    return KM_RSSI_DBM(a->fd_read(R_RSSI))<=-90;
}
static uint16_t word_for(unsigned frame,unsigned index) {
    /* Both frames use 256-bit diverse, fixed synchronization patterns and
       256 bits independently held out for RF decoder scoring.
       Frame A has 4 zero words at the end; frame B starts with 4 zero words.
       Indices 32..63 of fsk_lab_words are fixed seeded values. */
    if(frame==0)
        return index>=28 ? 0u : fsk_lab_words[32u+index];
    if(index<4) return 0u;
    if(index<8) return 0xffffu;
    return (uint16_t)~fsk_lab_words[32u+index];
}
static int send_frame(const k6_api_t *a,unsigned frame) {
    unsigned i;
    uint32_t start;
#define W(r,v) do {if(wr(a,(r),(uint16_t)(v))!=0)return -13;}while(0)
    /* Every frame is rearmed explicitly: this is NOT a transparent
       continuous FIFO. PA is NOT gated here, so RF may be emitted. */
    W(R_CONTROL,0x00f8u);
    W(R_CLEAR_IRQ,0u);
    W(R_LENGTH,0x3f00u);
    W(R_CONTROL,0x80f8u);
    W(R_CONTROL,0x00f8u);
    for(i=0;i<FSK_WORDS;i++) {
        if(!safe(a))return -4;
        W(R_FIFO,word_for(frame,i));
    }
    W(R_CONTROL,0x08f8u);
#undef W
    start=a->ms();
    while(1) {
        if(!safe(a))return -4;
        if(a->fd_read(R_STATUS)&1u)return (int)FSK_WORDS;
        if((uint32_t)(a->ms()-start)>FSK_FRAME_TIMEOUT_MS)return -14;
        a->delay_us(5000u);
    }
}
#endif

int k6_main(const k6_api_t *api) {
    if(!api || api->abi!=1 || api->level<K6_MODULE_LEVEL)return -3;
    if(api->fd_read(R_CHIP)!=0x4829u)return -6;
#if !FSK_BOUNDARY_ENABLE
    api->say("fsk boundary inert; bench build required");
    return -9;
#else
    uint16_t snapshot[N_RESTORE];
    unsigned i,frame;
    int ret=0,keyed=0,restore=0;
    if(api->rx_hz()!=FSK_FREQUENCY_HZ ||
       (api->fd_read(R_STATE)&2u) || !safe(api))return -2;
    if(!free_channel(api))return -7;
    api->delay_us(50000);
    if(!safe(api)||!free_channel(api))return -7;
    if(api->fd_read(R_WORK)&0xa000u)return -8;
    if(api->tx_on(FSK_MAX_TX_MS)!=0)return -5;
    keyed=1;
    if(api->tx_pa(0)!=0){ret=-4;goto cleanup;}
    for(i=0;i<N_RESTORE;i++)snapshot[i]=api->fd_read(restore_regs[i]);
    if((snapshot[0]&0xa000u)||(snapshot[1]&0x0800u)){
        ret=-8;goto cleanup;
    }
    restore=1;
#define W(r,v) do{if(wr(api,(r),(uint16_t)(v))!=0){ret=-13;goto cleanup;}}while(0)
    W(R_CONTROL,0x00f8u);
    W(R_CLEAR_IRQ,0u);
    W(R_WORK,0x8000u);
    W(R_PREAMBLE,0x00e1u);
    W(R_GAIN,0x0080u|FSK_GAIN_CODE);
    W(R_BAUD,FSK_BAUD_CODE);
    W(R_SYNC0,0x076bu);
    W(R_SYNC1,0x3e36u);
    W(R_SYNC2,0x5625u);
    W(R_LENGTH,0x3f00u);
#undef W
    if(api->tx_pa(1)!=0){ret=-4;goto cleanup;}
    for(frame=0;frame<FSK_NUM_FRAMES;frame++){
        ret=send_frame(api,frame);
        if(ret!=(int)FSK_WORDS)goto cleanup;
    }
    ret=(int)(FSK_WORDS*FSK_NUM_FRAMES);
cleanup:
    if(keyed){
        (void)api->tx_pa(0);
        if(restore){
            if(wr(api,R_CONTROL,0x00f8u)!=0 ||
               wr(api,R_WORK,0u)!=0 ||
               wr(api,R_CLEAR_IRQ,0u)!=0)ret=-15;
            for(i=N_RESTORE;i>0;i--){
                unsigned j=i-1;
                if(wr(api,restore_regs[j],snapshot[j])!=0)ret=-15;
            }
        }
        api->tx_off();
    }
    if(ret==(int)(FSK_WORDS*FSK_NUM_FRAMES))
        api->say("fsk10 L: two frames sent; RX restored");
    else
        api->say("fsk10 L: aborted; RX restore checked");
    return ret;
#endif
}
