/* Pre-decimation popcount capture: 86 guarded L64 packets for 4096 raw group counts.
 * DATA ONLY. This file never keys RF, manipulates DMA, SPI or runs a codec.
 * Source 8-bit popcounts (0..64) are *before* any GP0 decimator or Sotto.
 * DA wire magic is different from Sotto voice A9 and FDV1 control E2.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_GP0_POPCOUNT_AIR_H
#define FLUX_GP0_POPCOUNT_AIR_H
#include <stdint.h>
#include "dv_guard33_packet.h"

#define GPRAW_MAGIC 0xdau
#define GPRAW_VERSION 1u
#define GPRAW_GROUP_BITS 64u
#define GPRAW_SAMPLES 4096u
#define GPRAW_SAMPLES_PER_PACKET 48u
#define GPRAW_PACKETS ((GPRAW_SAMPLES+GPRAW_SAMPLES_PER_PACKET-1u)/GPRAW_SAMPLES_PER_PACKET)
#define GPRAW_WIRE_BYTES 66u
typedef char gpraw_packets_fit_in_byte[(GPRAW_PACKETS<=255u)?1:-1];

/* Whitened 64-byte packet, plus 0xA55A guard:
 *   0 DA magic; 1 version; 2 zero-based sequence; 3 total=67
 *   4..7 nonzero session token (LE)
 *   8..9 popcount_count=4096 (LE)
 *   10..11 PDM input bits per group=64 (NOT 8kHz sample rate) (LE)
 *   12..13 elapsed capture time in 100us ticks (LE, modulo 65536)
 *   14..61 up to 48 EXACT uint8 popcounts (0..64), each covering 64 PDM bit reads;
 *   final short fragment has zero padding.
 *   62..63 CRC16-CCITT-FALSE BE over plaintext [0..61].
 * No RF timebase inferred from packet arrival rate; capture_ticks has 0.1ms
 * precision and begins/ends at raw acquisition start/completion.
 */
static inline int gpraw_packet_words(uint16_t words[33],const uint8_t *counts,
                uint32_t token,uint16_t capture_100us,unsigned seq){
    unsigned i,j;
    uint8_t *p=(uint8_t*)words;
    uint16_t crc=0xffffu,pn9=0x01ffu;
    if(!words||!counts||!token||!capture_100us||capture_100us>15000u||seq>=GPRAW_PACKETS)
        return -1;
    for(i=0;i<62u;i++)p[i]=0u;
    p[0]=GPRAW_MAGIC;p[1]=GPRAW_VERSION;
    p[2]=(uint8_t)seq;p[3]=(uint8_t)GPRAW_PACKETS;
    for(i=0;i<4;i++)p[4u+i]=(uint8_t)(token>>(8u*i));
    p[8]=(uint8_t)GPRAW_SAMPLES;p[9]=(uint8_t)(GPRAW_SAMPLES>>8);
    p[10]=(uint8_t)GPRAW_GROUP_BITS;p[11]=0;
    p[12]=(uint8_t)capture_100us;p[13]=(uint8_t)(capture_100us>>8);
    for(i=0;i<GPRAW_SAMPLES_PER_PACKET;i++){
        j=seq*GPRAW_SAMPLES_PER_PACKET+i;
        if(j<GPRAW_SAMPLES){
            if(counts[j]>64u)return -1;
            p[14u+i]=counts[j];
        }
    }
    for(i=0;i<64u;i++){
        uint8_t b;
        if(i<62u){b=p[i];crc=dv_guard33_crc_step(crc,b);}
        else b=(uint8_t)(i==62u?(crc>>8):crc);
        p[i]=(uint8_t)(b^dv_guard33_pn9_step(&pn9));
    }
    words[32]=DV_GUARD33_GUARD_WORD;
    return 33;
}
#endif
