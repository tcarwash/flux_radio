/* Flux FD6818B FSK-10B prospective eight-packet Sotto 1200 blind RF burst.
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

#define FSK_NUM_FRAMES 8u
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
static const uint16_t sotto_packet_words[8][32] = {
    {
        0xa856u, 0x9addu, 0xdeecu, 0xa633u, 0xb07fu, 0x37ddu, 0x8c50u, 0x1a2bu,
        0xbb79u, 0x7b08u, 0x8290u, 0x969fu, 0x437cu, 0x94dau, 0xc79du, 0x84d1u,
        0x4d77u, 0xd086u, 0xfbb2u, 0x54dau, 0x6f99u, 0x9cdau, 0x5af0u, 0x840au,
        0x5872u, 0xf482u, 0x9ce2u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0xe5e8u,
    },
    {
        0xa856u, 0x9bddu, 0xdeecu, 0x3228u, 0x684eu, 0xa4f6u, 0x8055u, 0x9aabu,
        0xb170u, 0xc41fu, 0x552au, 0xda9eu, 0x4f55u, 0x3263u, 0x08aeu, 0x21ceu,
        0x07e3u, 0x1e97u, 0xb6e5u, 0x5183u, 0xb9ecu, 0xc6ecu, 0xf6eeu, 0xe83bu,
        0xe861u, 0x44f1u, 0xe9d7u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x4924u,
    },
    {
        0xa856u, 0x98ddu, 0xdeecu, 0xfd44u, 0xf6efu, 0x9487u, 0xd467u, 0xeeaau,
        0x8c47u, 0xea20u, 0x97bbu, 0x8799u, 0x664cu, 0x1adcu, 0xe5e8u, 0x7ab3u,
        0x7a13u, 0xcbf0u, 0x0bebu, 0x806fu, 0xc8e9u, 0xee42u, 0xb6a4u, 0x5f7cu,
        0x212cu, 0xb7a9u, 0x3592u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0xadddu,
    },
    {
        0xa856u, 0x99ddu, 0xdeecu, 0xfd56u, 0x0c25u, 0x388eu, 0x4c14u, 0x8830u,
        0x5500u, 0x854bu, 0x9969u, 0xd729u, 0x0fcau, 0xdb2fu, 0x991bu, 0xcfbbu,
        0xc3feu, 0x6137u, 0x5021u, 0x9486u, 0x2daeu, 0xacf7u, 0xc54eu, 0x3c3cu,
        0xf740u, 0x8fb9u, 0x0be4u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x563du,
    },
    {
        0xa856u, 0x9eddu, 0xdeecu, 0x103fu, 0x3bdfu, 0x2ec1u, 0x7616u, 0xc8bau,
        0xfa3au, 0x918cu, 0x3376u, 0x46abu, 0x757eu, 0x6e5fu, 0xa99du, 0x3afcu,
        0x90e6u, 0x84b4u, 0xa3abu, 0x289eu, 0x4affu, 0x6e97u, 0x1921u, 0xab44u,
        0x2121u, 0x727au, 0x8ea5u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x35d8u,
    },
    {
        0xa856u, 0x9fddu, 0xdeecu, 0xf793u, 0x5a23u, 0xa3e6u, 0xb742u, 0xd710u,
        0x3860u, 0xec32u, 0xdbb4u, 0xfaa8u, 0x8b28u, 0x567du, 0x70fau, 0xceabu,
        0xace9u, 0xeec1u, 0xbdf3u, 0x2d97u, 0x3bd6u, 0x5795u, 0xfb3au, 0x7c43u,
        0x9e22u, 0x2d6du, 0x53a3u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x4637u,
    },
    {
        0xa856u, 0x9cddu, 0xdeecu, 0x7552u, 0xb60cu, 0xab89u, 0x4211u, 0x8150u,
        0x2f30u, 0x014du, 0x936du, 0x458cu, 0x96c7u, 0x2b2fu, 0x5c95u, 0x36f8u,
        0xeb94u, 0xc2b4u, 0xdb9au, 0xd5b0u, 0x87a7u, 0xbad5u, 0x050eu, 0x4c0du,
        0xee72u, 0xaa63u, 0xcae9u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x4259u,
    },
    {
        0xa856u, 0x9dddu, 0xdeecu, 0x9a0eu, 0x278fu, 0xaf40u, 0x5814u, 0x2ef6u,
        0x2508u, 0x8c49u, 0x8800u, 0xf2deu, 0x0c04u, 0x6296u, 0xecdbu, 0x4fa9u,
        0x8081u, 0xb0c1u, 0xbcf6u, 0x8ea5u, 0x55eeu, 0x5793u, 0x6ef6u, 0x3244u,
        0x012cu, 0x5015u, 0x0773u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0xd105u,
    },
};
static uint16_t word_for(unsigned frame,unsigned index) {
    /* Real, already Sotto-1200 encoded and CRC-protected 64-byte L packets.
       Frozen at preflight; no dynamic TX FIFO refilling. */
    return sotto_packet_words[frame][index];
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
        api->say("fsk10B: eight packets sent; RX restored");
    else
        api->say("fsk10B: aborted; RX restore checked");
    return ret;
#endif
}
