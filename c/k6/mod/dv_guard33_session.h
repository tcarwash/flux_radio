/* Flux live DV guarded L64-length session-control prototype, V1.
 *
 * Distinct magic 0xE2, NOT any established K6 voice (A7/A9/AC/AD),
 * configuration (A8), firmware/module transfer (AA/AB/AE/AF) or
 * card-air (D6) magic. Old K6 pager receivers reject/ignore this payload.
 * Does NOT authorize any receiver action, PTT, radio or flash write.
 *
 * The entire 64-byte packet is whitened PN9 and CRC16/CCITT-FALSE sealed
 * identically to legacy L64. 0xA55A 16-bit sacrificial guard is appended
 * to survive the RFIC's observed leading 0x00F8 word fault.
 *
 * Plain bytes:
 * 0 magic E2, 1 version 01, 2 type 01 BEGIN / 02 END, 3 flags 00.
 * 4..11 opaque public session tag (64-bit nonzero; NOT authentication).
 * 12 first voice msg ID, 13 last ID (0 for BEGIN),
 * 14 final segment codec frames (0 for BEGIN, 1..8 for END),
 * 15 segment count (0 for BEGIN, 1..144 for END),
 * 16..19 ASCII "FDV1"; 20..61 zero; 62..63 CRC BE.
 *
 * END must match BEGIN and last_id == first_id + count - 1, no wrap.
 * No live B-chip receiver, ISR, ADC or transmitting function here.
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_DV_GUARD33_SESSION_H
#define FLUX_DV_GUARD33_SESSION_H

#include <stdint.h>
#include "dv_guard33_packet.h"

#define DV_SESSION_MAGIC 0xe2u
#define DV_SESSION_VERSION 1u
#define DV_SESSION_BEGIN 1u
#define DV_SESSION_END 2u
#define DV_SESSION_MAX_SEGMENTS 144u

typedef struct {
    uint32_t tag_hi;
    uint32_t tag_lo;
    uint8_t kind;
    uint8_t first_id;
    uint8_t last_id;
    uint8_t last_frames;
    uint8_t segments;
} dv_session_ctrl_t;

static inline int dv_session_valid(unsigned kind, uint32_t hi, uint32_t lo,
                                   unsigned first, unsigned last,
                                   unsigned final_frames, unsigned segments) {
    if ((hi | lo) == 0u || first > 255u || last > 255u)
        return 0;
    if (kind == DV_SESSION_BEGIN)
        return last == 0u && final_frames == 0u && segments == 0u;
    if (kind == DV_SESSION_END)
        return segments >= 1u && segments <= DV_SESSION_MAX_SEGMENTS &&
               final_frames >= 1u && final_frames <= 8u &&
               last >= first && (last - first + 1u) == segments;
    return 0;
}

static inline unsigned dv_session_build_words(uint16_t out[33], unsigned kind,
                    uint32_t tag_hi, uint32_t tag_lo, unsigned first,
                    unsigned last, unsigned final_frames, unsigned segments) {
    unsigned i;
    uint16_t crc=0xffffu, pn9=0x01ffu;
    if (!out || !dv_session_valid(kind,tag_hi,tag_lo,first,last,final_frames,segments))
        return 0u;
    for (i=0; i<64u; i++) {
        uint8_t b=0;
        if (i==0u) b=DV_SESSION_MAGIC;
        else if (i==1u) b=DV_SESSION_VERSION;
        else if (i==2u) b=(uint8_t)kind;
        else if (i>=4u && i<=7u) b=(uint8_t)(tag_hi >> (8u*(7u-i)));
        else if (i>=8u && i<=11u) b=(uint8_t)(tag_lo >> (8u*(11u-i)));
        else if (i==12u) b=(uint8_t)first;
        else if (i==13u) b=(uint8_t)last;
        else if (i==14u) b=(uint8_t)final_frames;
        else if (i==15u) b=(uint8_t)segments;
        else if (i==16u) b='F';
        else if (i==17u) b='D';
        else if (i==18u) b='V';
        else if (i==19u) b='1';
        else if (i==62u) b=(uint8_t)(crc >> 8);
        else if (i==63u) b=(uint8_t)crc;
        if (i<62u) crc=dv_guard33_crc_step(crc,b);
        b ^= dv_guard33_pn9_step(&pn9);
        if (i & 1u) out[i>>1] |= (uint16_t)((uint16_t)b << 8);
        else out[i>>1]=b;
    }
    out[32]=DV_GUARD33_GUARD_WORD;
    return 33u;
}

/* Returns 1 only for a strict V1, CRC-valid session control.
 * On failure out is UNTOUCHED. No action on observed control packets.
 */
static inline int dv_session_decode64(const uint8_t packet[64], dv_session_ctrl_t *out) {
    uint8_t h[20];
    uint16_t pn9=0x01ffu, crc=0xffffu, wire_crc=0u;
    unsigned i;
    dv_session_ctrl_t parsed;
    if (!packet || !out) return 0;
    for (i=0; i<64u; i++) {
        const uint8_t b=(uint8_t)(packet[i] ^ dv_guard33_pn9_step(&pn9));
        if (i<20u) h[i]=b;
        if (i<62u) {
            crc=dv_guard33_crc_step(crc,b);
            if (i>=20u && b!=0u) return 0;
        } else if (i==62u) wire_crc=(uint16_t)((uint16_t)b<<8);
        else wire_crc=(uint16_t)(wire_crc|b);
    }
    if (crc!=wire_crc || h[0]!=DV_SESSION_MAGIC ||
        h[1]!=DV_SESSION_VERSION || h[3]!=0 ||
        h[16]!='F' || h[17]!='D' || h[18]!='V' || h[19]!='1') return 0;
    parsed.kind=h[2];
    parsed.tag_hi=((uint32_t)h[4]<<24)|((uint32_t)h[5]<<16)|
                  ((uint32_t)h[6]<<8)|h[7];
    parsed.tag_lo=((uint32_t)h[8]<<24)|((uint32_t)h[9]<<16)|
                  ((uint32_t)h[10]<<8)|h[11];
    parsed.first_id=h[12];
    parsed.last_id=h[13];
    parsed.last_frames=h[14];
    parsed.segments=h[15];
    if (!dv_session_valid(parsed.kind,parsed.tag_hi,parsed.tag_lo,
                          parsed.first_id,parsed.last_id,
                          parsed.last_frames,parsed.segments)) return 0;
    *out=parsed;
    return 1;
}

/* Checks both candidate 64-byte slices of physical 66-byte frame.
 * Returns 1 on unique valid control, 0 otherwise. Fails closed if two.
 */
static inline int dv_session_select66(const uint8_t physical[66],
                                      dv_session_ctrl_t *out, unsigned *offset) {
    dv_session_ctrl_t a,b;
    int x,y;
    if (!physical || !out || !offset) return 0;
    x=dv_session_decode64(physical,&a);
    y=dv_session_decode64(physical+2,&b);
    if ((x+y)!=1) return 0;
    *out=x?a:b;
    *offset=x?0u:2u;
    return 1;
}

#endif /* FLUX_DV_GUARD33_SESSION_H */
