/* Flux guarded live-DV segment queue: 8 Sotto-1200 40ms frames per L64 packet.
 *
 * Zero allocation, no floating point, no SPI, no hardware access.
 * Producer: slim_encode10() outputs 0/1 compressed 6-byte frames.
 * Consumer: 33 native-FSK FIFO words, full preload, sacrificial final 0xA55A.
 *
 * A fresh Flux L64 message is sent for every 320ms group; msg id increases,
 * seq is always zero, and total describes the number of real speech slots
 * in that message. Unknown final PTT duration is never needed in advance.
 * The 1..7 frame final group is padded to eight slots and its total records
 * the real frames. Exactly 8 on final has no explicit EOT on air.
 *
 * Driver must preflight B-chip/PA/watchdog/slot/VFO outside this module,
 * with canonical radio lock and independently approved hardware lease.
 * The TX consumer calls tx_done(1) only after RFIC completion was observed;
 * it MUST stop (without retries) on any timeout/failed operation.
 * The queue is not safe for concurrent IRQ mutation: encoder foreground owns
 * the queue; timer ISR owns only the separate PCM ring.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_DV_GUARD_LIVE_H
#define FLUX_DV_GUARD_LIVE_H

#include <stdint.h>

#define DV_LIVE_CODEC_BYTES 6u
#define DV_LIVE_CODEC_SLOTS 8u
#define DV_LIVE_PACKET_BYTES 64u
#define DV_LIVE_RF_BYTES 66u
#define DV_LIVE_RF_WORDS 33u
#define DV_LIVE_GUARD_WORD 0xA55Au
#define DV_LIVE_QUEUE_SLOTS 2u

enum {
    DV_LIVE_OK=0,
    DV_LIVE_READY=1,    /* new RF frame queued */
    DV_LIVE_QUEUED=2,   /* codec frame stored, no RF frame yet */
    DV_LIVE_EINVAL=-1,
    DV_LIVE_ESTATE=-2,
    DV_LIVE_EFULL=-3,
    DV_LIVE_EPOCH=-4,  /* 8-bit message ID exhausted; start a new session */
    DV_LIVE_EFAILED=-5
};

typedef struct {
    uint8_t pending[DV_LIVE_CODEC_SLOTS * DV_LIVE_CODEC_BYTES];
    uint8_t pending_count;
    uint8_t frames[DV_LIVE_QUEUE_SLOTS][DV_LIVE_RF_BYTES];
    uint8_t frame_count[DV_LIVE_QUEUE_SLOTS];
    uint8_t msg_id[DV_LIVE_QUEUE_SLOTS];
    uint8_t head, queued, active, failed;
    uint16_t next_msg;       /* 256 represents exhausted epoch, no modulo wrap */
    uint32_t total_codec_frames, completed_packets, enqueue_failures;
} dv_live_t;

static void dv_live_zero(uint8_t *d,unsigned n) {
    unsigned i;
    for (i=0;i<n;i++) d[i]=0u;
}
static uint16_t dv_live_crc16(const uint8_t *data,unsigned n) {
    uint16_t crc=0xffffu;
    unsigned i,j;
    for (i=0;i<n;i++) {
        crc=(uint16_t)(crc ^ ((uint16_t)data[i] << 8));
        for(j=0;j<8;j++)
            crc=(uint16_t)((crc & 0x8000u) ? ((crc<<1)^0x1021u) : (crc<<1));
    }
    return crc;
}
/* Bit-identical to modem.pager.pn9: x^9+x^5+1, init 0x1ff, low 8 bits
   before eight right-shifts per encoded byte. */
static void dv_live_whiten(uint8_t *p,unsigned n) {
    uint16_t x=0x1ffu;
    unsigned i,j;
    for(i=0;i<n;i++) {
        p[i]=(uint8_t)(p[i]^(uint8_t)x);
        for(j=0;j<8;j++)
            x=(uint16_t)((x>>1)|(((x^(x>>5))&1u)<<8));
    }
}

static int dv_live_begin(dv_live_t *s,unsigned first_msg_id) {
    if(!s || first_msg_id>255u) return DV_LIVE_EINVAL;
    dv_live_zero((uint8_t *)s,sizeof(*s));
    s->next_msg=(uint16_t)first_msg_id;
    s->active=1u;
    return DV_LIVE_OK;
}

static int dv_live_enqueue(dv_live_t *s) {
    uint8_t *p;
    uint16_t crc;
    unsigned j,k,i;
    if(s->queued>=DV_LIVE_QUEUE_SLOTS) {
        s->enqueue_failures++;
        return DV_LIVE_EFULL;
    }
    if(s->next_msg>255u) return DV_LIVE_EPOCH;
    if(s->pending_count==0u || s->pending_count>DV_LIVE_CODEC_SLOTS)
        return DV_LIVE_ESTATE;
    i=(unsigned)(s->head+s->queued)%DV_LIVE_QUEUE_SLOTS;
    p=s->frames[i];
    dv_live_zero(p,DV_LIVE_RF_BYTES);
    p[0]=0xA9u;                  /* Sotto 1200 */
    p[1]=(uint8_t)s->next_msg;  /* monotonically increasing segment id */
    p[2]=0xC0u;                 /* L64 depth=1, seq=0, not a short-last flag */
    p[3]=0x00u;
    p[4]=0x00u;
    p[5]=s->pending_count;      /* final partial voice group 1..7 */
    for(j=0;j<s->pending_count;j++)
        for(k=0;k<DV_LIVE_CODEC_BYTES;k++)
            p[6u+j*DV_LIVE_CODEC_BYTES+k]=s->pending[j*DV_LIVE_CODEC_BYTES+k];
    crc=dv_live_crc16(p,62u);
    p[62]=(uint8_t)(crc>>8);
    p[63]=(uint8_t)crc;
    dv_live_whiten(p,DV_LIVE_PACKET_BYTES);
    /* Last *physical* 16 bits are intentionally not part of CRC. */
    p[64]=(uint8_t)DV_LIVE_GUARD_WORD;
    p[65]=(uint8_t)(DV_LIVE_GUARD_WORD>>8);
    s->frame_count[i]=s->pending_count;
    s->msg_id[i]=(uint8_t)s->next_msg;
    s->next_msg++;
    s->queued++;
    s->pending_count=0;
    return DV_LIVE_READY;
}

/* Encoder receives either 0 or 1 codec superframe per 10ms block.
   The caller must retry a failed 8th push only AFTER draining a packet.
   Returning EFULL/EEPOCH leaves original pending audio untouched. */
static int dv_live_push(dv_live_t *s,const uint8_t frame[DV_LIVE_CODEC_BYTES]) {
    unsigned i;
    if(!s || !frame) return DV_LIVE_EINVAL;
    if(!s->active || s->failed) return DV_LIVE_ESTATE;
    if(s->pending_count>=DV_LIVE_CODEC_SLOTS) return DV_LIVE_ESTATE;
    if(s->pending_count==DV_LIVE_CODEC_SLOTS-1u &&
       s->queued==DV_LIVE_QUEUE_SLOTS) {
        s->enqueue_failures++;
        return DV_LIVE_EFULL;
    }
    if(s->next_msg>255u) return DV_LIVE_EPOCH;
    for(i=0;i<DV_LIVE_CODEC_BYTES;i++)
        s->pending[s->pending_count*DV_LIVE_CODEC_BYTES+i]=frame[i];
    s->pending_count++;
    s->total_codec_frames++;
    if(s->pending_count==DV_LIVE_CODEC_SLOTS) return dv_live_enqueue(s);
    return DV_LIVE_QUEUED;
}

static int dv_live_end(dv_live_t *s) {
    int r=DV_LIVE_OK;
    if(!s || !s->active || s->failed) return DV_LIVE_ESTATE;
    if(s->pending_count) {
        r=dv_live_enqueue(s);
        if(r<0) return r;  /* preserve pending for a later retry */
    }
    s->active=0;
    return r;
}

/* Returns a COPY of all 33 words to the caller.  Preload ALL these words
 * BEFORE REG_59=0x08F8 starts TX, with REG_5D=0x4100 (66 bytes). */
static int dv_live_peek_words(const dv_live_t *s,uint16_t out[DV_LIVE_RF_WORDS],
                              unsigned *msg_id,unsigned *codec_frames) {
    unsigned j;
    const uint8_t *p;
    if(!s || !out) return DV_LIVE_EINVAL;
    if(!s->queued || s->failed) return DV_LIVE_ESTATE;
    p=s->frames[s->head];
    for(j=0;j<DV_LIVE_RF_WORDS;j++)
        out[j]=(uint16_t)(p[2u*j]|((uint16_t)p[2u*j+1u]<<8));
    if(msg_id) *msg_id=s->msg_id[s->head];
    if(codec_frames) *codec_frames=s->frame_count[s->head];
    return DV_LIVE_OK;
}

/* On RF failure freeze the queue until explicitly reset by begin().
 * Prevents silent duplicated voice packets, hidden retries or advancing an
 * unverified transmitter.  Actual watchdog, PA and SPI rollback are owned
 * by the caller's hardware transaction and must never be skipped. */
static int dv_live_tx_done(dv_live_t *s,int observed_completion) {
    if(!s || !s->queued || s->failed) return DV_LIVE_ESTATE;
    if(!observed_completion) {
        s->failed=1u;
        return DV_LIVE_EFAILED;
    }
    s->head=(uint8_t)((s->head+1u)%DV_LIVE_QUEUE_SLOTS);
    s->queued--;
    s->completed_packets++;
    return DV_LIVE_OK;
}
#endif
