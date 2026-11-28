/* Pre-vocoder PCM capture: 67 guarded L64 packets for 200ms of exact 8kHz s16le.
 * DATA ONLY. This file never keys RF, manipulates DMA, SPI or runs a codec.
 * Source samples are *before* Sotto. No optional lossy companding.
 * D9 wire magic is different from Sotto voice A9 and FDV1 control E2.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_GP0_PCM_AIR_H
#define FLUX_SOTTO_GP0_PCM_AIR_H
#include <stdint.h>
#include "dv_guard33_packet.h"

#define GPRAW_MAGIC 0xd9u
#define GPRAW_VERSION 1u
#define GPRAW_PCM_HZ 8000u
#define GPRAW_SAMPLES 1600u
#define GPRAW_SAMPLES_PER_PACKET 24u
#define GPRAW_PACKETS ((GPRAW_SAMPLES+GPRAW_SAMPLES_PER_PACKET-1u)/GPRAW_SAMPLES_PER_PACKET)
#define GPRAW_WIRE_BYTES 66u
typedef char gpraw_packets_fit_in_byte[(GPRAW_PACKETS<=255u)?1:-1];

/* Whitened 64-byte packet, plus 0xA55A guard:
 *   0 D9 magic; 1 version; 2 zero-based sequence; 3 total=67
 *   4..7 nonzero session token (LE)
 *   8..9 sample_count=1600 (LE)
 *   10..11 PCM declared sample rate=8000 (LE)
 *   12..13 elapsed capture time in 100us ticks (LE, modulo 65536)
 *   14..61 up to 24 EXACT signed 16-bit little-endian PCM samples;
 *   final short fragment has zero padding.
 *   62..63 CRC16-CCITT-FALSE BE over plaintext [0..61].
 * No RF timebase inferred from packet arrival rate; capture_ticks has 0.1ms
 * precision and begins/ends at PCM service admission/stop.
 */
static inline int gpraw_packet_words(uint16_t words[33],const int16_t *pcm,
                uint32_t token,uint16_t capture_100us,unsigned seq){
    unsigned i,j;
    uint8_t *p=(uint8_t*)words;
    uint16_t crc=0xffffu,pn9=0x01ffu;
    if(!words||!pcm||!token||!capture_100us||capture_100us>15000u||seq>=GPRAW_PACKETS)
        return -1;
    for(i=0;i<62u;i++)p[i]=0u;
    p[0]=GPRAW_MAGIC;p[1]=GPRAW_VERSION;
    p[2]=(uint8_t)seq;p[3]=(uint8_t)GPRAW_PACKETS;
    for(i=0;i<4;i++)p[4u+i]=(uint8_t)(token>>(8u*i));
    p[8]=(uint8_t)GPRAW_SAMPLES;p[9]=(uint8_t)(GPRAW_SAMPLES>>8);
    p[10]=(uint8_t)GPRAW_PCM_HZ;p[11]=(uint8_t)(GPRAW_PCM_HZ>>8);
    p[12]=(uint8_t)capture_100us;p[13]=(uint8_t)(capture_100us>>8);
    for(i=0;i<GPRAW_SAMPLES_PER_PACKET;i++){
        j=seq*GPRAW_SAMPLES_PER_PACKET+i;
        if(j<GPRAW_SAMPLES){
            uint16_t v=(uint16_t)pcm[j];
            p[14u+2u*i]=(uint8_t)v;p[15u+2u*i]=(uint8_t)(v>>8);
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
