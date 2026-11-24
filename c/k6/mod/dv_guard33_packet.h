/* Flux guarded Sotto-1200 L64 packet builder (no radio side effects).
 *
 * PRODUCES: 33 FD6818B FIFO words (little-endian), comprising an unchanged
 * 64-byte Flux L64 data packet plus 0xA55A sacrificial final word.
 *
 * Live microphone segment format:
 *   one message per 1..8 newly encoded 40-ms Sotto superframes,
 *   profile L / Sotto-1200, interleave depth 1, packet seq 0,
 *   msg_id monotonically increasing WITHIN a bounded session (caller must
 *   refuse wrap before transmission).
 *
 * The original L64 CRC and PN9 whitening are exactly as modem/pager.py.
 * This packet builder assumes pre-encoded 6-byte Sotto frames. It does NOT
 * sample PA11, encode Sotto, access SPI, key PTT or transmit anything.
 *
 * No heap, no temporary 64-byte copy, no printf or dynamic memory. Output
 * is 66 bytes, including the sacrificial guard word. CRC is accumulated
 * across the 62 pre-whitened L64 header/body bytes; whitening is one
 * continuous PN9 stream starting from 0x1FF for each 64-byte packet.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_K6_DV_GUARD33_PACKET_H
#define FLUX_K6_DV_GUARD33_PACKET_H
#include <stdint.h>

#define DV_GUARD33_WORDS 33u
#define DV_GUARD33_L64_BYTES 64u
#define DV_GUARD33_MAX_SUPERFRAMES 8u
#define DV_GUARD33_SUPERFRAME_BYTES 6u
#define DV_GUARD33_GUARD_WORD 0xA55Au

static inline uint16_t dv_guard33_crc_step(uint16_t crc, uint8_t b) {
    unsigned bit;
    crc = (uint16_t)(crc ^ (uint16_t)((uint16_t)b << 8));
    for (bit = 0; bit < 8; ++bit)
        crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000u) ? 0x1021u : 0u));
    return crc;
}

static inline uint8_t dv_guard33_pn9_step(uint16_t *state) {
    unsigned bit;
    uint16_t s = *state;
    const uint8_t b = (uint8_t)s;
    for (bit = 0; bit < 8; ++bit)
        s = (uint16_t)((s >> 1) | ((uint16_t)((s ^ (s >> 5)) & 1u) << 8));
    *state = s;
    return b;
}

/* Returns 33 FIFO words on success; zero on invalid frame count/pointer.
 * On failure, out_words is untouched. Input points to count*6 bytes,
 * at most 48. It must not alias out_words.
 */
static inline unsigned dv_guard33_build_words(uint16_t out_words[DV_GUARD33_WORDS],
                                               const uint8_t *sotto6,
                                               unsigned frame_count,
                                               uint8_t msg_id) {
    unsigned i;
    uint16_t crc = 0xffffu;
    uint16_t pn9 = 0x01ffu;

    if (!out_words || !sotto6 || frame_count < 1u ||
        frame_count > DV_GUARD33_MAX_SUPERFRAMES)
        return 0u;

    for (i = 0u; i < DV_GUARD33_WORDS; ++i)
        out_words[i] = 0u;

    for (i = 0u; i < DV_GUARD33_L64_BYTES; ++i) {
        uint8_t b;
        if (i == 0u) b = 0xa9u; /* Sotto-1200 */
        else if (i == 1u) b = msg_id;
        else if (i == 2u)
            b = 0xc0u; /* depth=1 => 0xC000, seq=0; short-group flag
                          is NEVER set at depth 1 (only one packet/group).
                          The total=1..8 in bytes 4-5 marks final slots. */
        else if (i == 3u) b = 0u; /* seq low byte */
        else if (i == 4u) b = 0u; /* total 1..8, big-endian */
        else if (i == 5u) b = (uint8_t)frame_count;
        else if (i < 54u) {
            const unsigned j = i - 6u;
            b = j < frame_count * 6u ? sotto6[j] : 0u;
        } else if (i < 62u) b = 0u; /* 8 reserved bytes */
        else if (i == 62u) b = (uint8_t)(crc >> 8);
        else b = (uint8_t)crc;
        if (i < 62u) crc = dv_guard33_crc_step(crc, b);
        b ^= dv_guard33_pn9_step(&pn9);
        if (i & 1u) out_words[i >> 1] |= (uint16_t)((uint16_t)b << 8);
        else out_words[i >> 1] = b;
    }
    out_words[32] = DV_GUARD33_GUARD_WORD;
    return DV_GUARD33_WORDS;
}
#endif /* FLUX_K6_DV_GUARD33_PACKET_H */
