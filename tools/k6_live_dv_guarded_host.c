/* Flux OFFLINE integration test: actual slim_encode10 10ms CPU workload ->
 * bounded native L64 C queue -> 33-word sacrificial-guard FSK frames.
 *
 * Usage: program private-pcm.raw private-guarded-frames.bin private-slim.bit
 *        private-schedule.csv
 * Input: signed 16-bit LE PCM, 8000Hz, multiples of 80 samples.
 *
 * This is NOT an on-radio module or realtime scheduler: the 151.2ms
 * radio duration is simulated, no SPI, FIFO or transmit function is called.
 * It tests the exact C packet and queue ABI ahead of guarded hardware work.
 *
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/stat.h>
#include "sotto_slim.h"
#include "../c/k6/mod/dv_live_guard.h"

#define TX_MS 152u
#define START_MSG 112u

typedef struct {
    dv_live_t *s;
    FILE *out;
    FILE *timeline;
    unsigned now_ms,ready_at[256],send_count;
    unsigned in_flight,done_ms,last_ready_ms;
    unsigned msg_id,frames;
} tx_t;

static void fail(const char *msg) {
    fprintf(stderr,"guarded live scheduler: %s\n",msg);
    exit(3);
}

static void service_tx(tx_t *tx, unsigned ms) {
    uint16_t words[DV_LIVE_RF_WORDS];
    unsigned i;
    if(ms<tx->now_ms) fail("nonmonotonic simulated time");
    tx->now_ms=ms;
    if(tx->in_flight && ms>=tx->done_ms) {
        if(dv_live_tx_done(tx->s,1)!=DV_LIVE_OK)fail("TX callback state error");
        tx->in_flight=0;
    }
    if(!tx->in_flight && tx->s->queued) {
        unsigned msg,frames;
        if(dv_live_peek_words(tx->s,words,&msg,&frames)!=DV_LIVE_OK)
            fail("queue peek failed");
        if(tx->send_count>=256) fail("unexpected packet count");
        for(i=0;i<DV_LIVE_RF_WORDS;i++) {
            unsigned char b[2]={(unsigned char)words[i],
                                (unsigned char)(words[i]>>8)};
            if(fwrite(b,1,2,tx->out)!=2)fail("guarded packet output failed");
        }
        tx->done_ms=ms+TX_MS;
        tx->in_flight=1;
        if(fprintf(tx->timeline,"%u,%u,%u,%u,%u,%u\n",
                   tx->send_count,msg,frames,tx->ready_at[msg],
                   ms,tx->done_ms)<0)fail("timeline write failed");
        tx->send_count++;
    }
}
static void event(tx_t *tx,const uint8_t f[6],unsigned ms) {
    int r;
    service_tx(tx,ms);
    r=dv_live_push(tx->s,f);
    if(r==DV_LIVE_EFULL) {
        /* A real firmware must retain this frame and let the radio drain
         * the queue asynchronously. Here advance the modeled hardware,
         * retry EXACTLY once after a completion; never drop a frame. */
        if(!tx->in_flight)fail("queue full with no scheduled transmission");
        service_tx(tx,tx->done_ms);
        r=dv_live_push(tx->s,f);
    }
    if(r<0)fail("encoder frame rejected / epoch exhausted");
    if(r==DV_LIVE_READY) {
        unsigned msg=(unsigned)(tx->s->next_msg-1u);
        tx->ready_at[msg]=ms;
        service_tx(tx,tx->now_ms);
    }
}
static FILE *create_private(const char *path) {
    FILE *f;
    if(access(path,F_OK)==0)fail("refuse overwrite of private voice output");
    f=fopen(path,"wb");
    if(!f)fail("failed creating private voice file");
    return f;
}
int main(int argc,char **argv) {
#ifdef DV_LIVE_FLASH_MODEL
    extern void dv_flash_host_setup(const char *deck_file);
#endif
    FILE *src,*rf,*compressed,*csv;
    slim_enc_t *enc;
    dv_live_t txstate;
    tx_t tx={0};
    int16_t pcm[80];
    unsigned char encoded[6];
    unsigned blocks=0,frames=0,flush_frames=0;
    int r;
    if(argc!=
#ifdef DV_LIVE_FLASH_MODEL
       6
#else
       5
#endif
       ) {
        fprintf(stderr,"usage: %s PCM.raw GUARD66.bin SOTTO.bit TIMELINE.csv\n",argv[0]);
        return 2;
    }
    umask(0077);
#ifdef DV_LIVE_FLASH_MODEL
    dv_flash_host_setup(argv[5]);
#endif
    src=fopen(argv[1],"rb");
    if(!src)fail("input PCM missing");
    rf=create_private(argv[2]);
    compressed=create_private(argv[3]);
    csv=create_private(argv[4]);
    if(dv_live_begin(&txstate,START_MSG)!=DV_LIVE_OK)
        fail("initial queue state");
    enc=calloc(1u,slim_enc_bytes());
    if(!enc)fail("no slim encoder memory");
    slim_enc_init(enc);
    tx.s=&txstate;tx.out=rf;tx.timeline=csv;
    if(fprintf(csv,"segment,message_id,frames,ready_ms,start_ms,finish_ms\n")<0)
        fail("CSV header");
    for(;;) {
        size_t n=fread(pcm,sizeof(pcm[0]),80,src);
        if(!n)break;
        if(n!=80)fail("PCM must have complete 10ms blocks");
        blocks++;
        service_tx(&tx,blocks*10u);
        r=slim_encode10(enc,pcm,encoded);
        if(r<0||r>1)fail("slim_encode10 did not return 0 or 1");
        if(r) {
            if(fwrite(encoded,1,6,compressed)!=6)fail("Sotto output");
            frames++;
            event(&tx,encoded,blocks*10u);
        }
    }
    while((r=slim_flush(enc,encoded))>0) {
        if(r!=1)fail("invalid slim encoder flush");
        if(fwrite(encoded,1,6,compressed)!=6)fail("compressed flush output");
        frames++;flush_frames++;
        event(&tx,encoded,blocks*10u);
        if(flush_frames>3u)fail("unexpected number of flush frames");
    }
    if(r<0)fail("slim flush failed");
    r=dv_live_end(&txstate);
    if(r<0)fail("PTT end cannot complete final L64 segment");
    if(r==DV_LIVE_READY) {
        unsigned msg=(unsigned)(txstate.next_msg-1u);
        tx.ready_at[msg]=blocks*10u;
    }
    /* Emulate serialized radio packet completion, NEVER a real TX. */
    for(unsigned i=0;i<256u && (tx.in_flight||txstate.queued);i++) {
        service_tx(&tx,tx.in_flight?tx.done_ms:tx.now_ms);
    }
    if(tx.in_flight||txstate.queued || txstate.active ||
       txstate.total_codec_frames!=frames ||
       txstate.completed_packets!=tx.send_count)
        fail("encoder/guarded TX drain incomplete");
    if(fclose(src)||fclose(rf)||fclose(compressed)||fclose(csv))
        fail("closing output");
    free(enc);
    fprintf(stderr,"guarded live scheduler: %u 10ms blocks, %u Sotto frames, "
            "%u packets, %u flush frames, %u queue rejects (offline)\n",
            blocks,frames,tx.send_count,flush_frames,txstate.enqueue_failures);
    return 0;
}
