/* Flux DV wire decoder + on-radio packet queue glue (no hardware).
 * Compatible with the frozen FSK-13/14 33-word packet and FDV1 control.
 * Do NOT call transmit, key PA/PTT, access SPI, GPIO, IRQ or serial here.
 */
#include "sotto_dv_wire.h"

static int probe_voice64(const uint8_t packet[64], uint8_t *id, unsigned *count) {
    unsigned i, n=0;
    uint16_t pn9=0x01ffu, crc=0xffffu, seen_crc=0u;
    uint8_t mid=0u;
    if (!packet || !id || !count) return 0;
    for (i=0u; i<64u; ++i) {
        uint8_t b=(uint8_t)(packet[i] ^ dv_guard33_pn9_step(&pn9));
        if (i==0u && b!=0xa9u) return 0;
        if (i==1u) mid=b;
        if (i==2u && b!=0xc0u) return 0;
        if (i==3u && b!=0u) return 0;
        if (i==4u && b!=0u) return 0;
        if (i==5u) { n=b; if (n<1u || n>8u) return 0; }
        if (i>=6u && i<62u && i>=6u+6u*n && b!=0u) return 0;
        if (i<62u) crc=dv_guard33_crc_step(crc,b);
        else if (i==62u) seen_crc=(uint16_t)((uint16_t)b<<8);
        else seen_crc=(uint16_t)(seen_crc|b);
    }
    if (crc!=seen_crc) return 0;
    *id=mid;
    *count=n;
    return 1;
}

unsigned flux_dv_wire_rx66(const uint8_t physical[66], uint8_t frames[48],
                           uint8_t *message_id, unsigned *frame_count,
                           unsigned *offset_bytes) {
    uint8_t id0=0,id2=0;
    unsigned n0=0,n2=0,i,off,n;
    uint16_t pn9=0x01ffu;
    const uint8_t *chosen;
    int a,b;
    if (!physical || !frames || !message_id || !frame_count || !offset_bytes)
        return 0u;
    a=probe_voice64(physical,&id0,&n0);
    b=probe_voice64(physical+2,&id2,&n2);
    if ((a+b)!=1) return 0u; /* no packet or 2 matching CRC windows */
    off=a?0u:2u;
    n=a?n0:n2;
    chosen=physical+off;
    for (i=0u;i<54u;++i) {
        const uint8_t plain=(uint8_t)(chosen[i] ^ dv_guard33_pn9_step(&pn9));
        if (i>=6u)
            frames[i-6u]=plain; /* includes zero-padding through slot 7 */
    }
    *message_id=a?id0:id2;
    *frame_count=n;
    *offset_bytes=off;
    return 1u;
}

unsigned flux_dv_wire_session_tx(const dv_session_ctrl_t *ctrl,
                                 uint16_t physical_words[33]) {
    if (!ctrl || !physical_words) return 0u;
    return dv_session_build_words(physical_words, ctrl->kind,
                                  ctrl->tag_hi, ctrl->tag_lo,
                                  ctrl->first_id, ctrl->last_id,
                                  ctrl->last_frames, ctrl->segments);
}

unsigned flux_dv_wire_session_rx66(const uint8_t physical[66],
                                   dv_session_ctrl_t *ctrl,
                                   unsigned *offset_bytes) {
    return (unsigned)dv_session_select66(physical,ctrl,offset_bytes);
}
unsigned flux_dv_wire_state_bytes(void) {
    return (unsigned)sizeof(dv_guard33_stream_t);
}
int flux_dv_wire_begin(dv_guard33_stream_t *state, unsigned message_id) {
    return dv_stream_begin(state,message_id);
}
int flux_dv_wire_push(dv_guard33_stream_t *state, const uint8_t codec6[6]) {
    return dv_stream_push(state,codec6);
}
int flux_dv_wire_finish(dv_guard33_stream_t *state) {
    return dv_stream_finish(state);
}
const uint16_t *flux_dv_wire_peek(const dv_guard33_stream_t *state) {
    return dv_stream_peek(state);
}
int flux_dv_wire_ack(dv_guard33_stream_t *state) {
    return dv_stream_ack(state);
}
