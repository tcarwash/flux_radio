/* Host/regression driver for the very same freestanding K6 guarded DV
 * scheduler header linked into the Cortex-M0 module.  No RF or SPI.
 * SPDX-License-Identifier: MIT
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "../c/k6/mod/dv_live_guard.h"

#define CK(x) do {if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1;}}while(0)

static void frame(unsigned index,uint8_t out[6]) {
    unsigned i;
    for(i=0;i<6;i++)out[i]=(uint8_t)(index*17u+i*7u);
}
static int push(dv_live_t *s,unsigned idx) {
    uint8_t b[6];
    frame(idx,b);
    return dv_live_push(s,b);
}
static int inspect(dv_live_t *s,unsigned msg,unsigned n) {
    uint16_t words[33];
    unsigned id=999,count=999;
    CK(dv_live_peek_words(s,words,&id,&count)==DV_LIVE_OK);
    CK(id==msg && count==n);
    CK(words[32]==DV_LIVE_GUARD_WORD);
    CK((words[0]&0xffu)!=0u);
    return 0;
}
int main(void) {
    dv_live_t s;
    unsigned i,n=0;
    uint8_t b[6];
    CK(sizeof(dv_live_t)<300u);
    CK(dv_live_begin(NULL,1)==DV_LIVE_EINVAL);
    CK(dv_live_begin(&s,256)==DV_LIVE_EINVAL);
    CK(dv_live_begin(&s,42)==DV_LIVE_OK);
    CK(s.next_msg==42 && s.queued==0 && s.active);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_ESTATE);
    for(i=0;i<16;i++) CK(push(&s,i)==(i%8==7 ? DV_LIVE_READY : DV_LIVE_QUEUED));
    CK(s.queued==2u && s.next_msg==44 && s.total_codec_frames==16u);
    CK(inspect(&s,42u,8u)==0);
    for(i=16;i<23;i++) CK(push(&s,i)==DV_LIVE_QUEUED);
    frame(23,b);
    CK(dv_live_push(&s,b)==DV_LIVE_EFULL);
    CK(s.pending_count==7u && s.queued==2u && s.total_codec_frames==23u);
    CK(s.enqueue_failures==1u);
    CK(dv_live_end(&s)==DV_LIVE_EFULL); /* final seven pending no queue room */
    CK(s.active && s.pending_count==7u);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_OK);
    CK(s.completed_packets==1 && s.queued==1);
    CK(inspect(&s,43u,8u)==0);
    CK(dv_live_push(&s,b)==DV_LIVE_READY);
    CK(s.pending_count==0 && s.queued==2u && s.total_codec_frames==24u);
    CK(dv_live_end(&s)==DV_LIVE_OK && !s.active);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_OK);
    CK(inspect(&s,44u,8u)==0);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_OK);
    CK(s.queued==0 && s.completed_packets==3);
    CK(dv_live_peek_words(&s,(uint16_t [33]){0},NULL,NULL)==DV_LIVE_ESTATE);
    CK(dv_live_push(&s,b)==DV_LIVE_ESTATE);

    CK(dv_live_begin(&s,252)==DV_LIVE_OK);
    CK(push(&s,0)==DV_LIVE_QUEUED);
    CK(push(&s,1)==DV_LIVE_QUEUED);
    CK(dv_live_end(&s)==DV_LIVE_READY);
    CK(s.queued==1 && !s.active);
    CK(inspect(&s,252u,2u)==0);
    CK(dv_live_tx_done(&s,0)==DV_LIVE_EFAILED);
    CK(s.failed && s.queued==1);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_ESTATE);
    CK(dv_live_peek_words(&s,(uint16_t [33]){0},NULL,NULL)==DV_LIVE_ESTATE);

    CK(dv_live_begin(&s,255)==DV_LIVE_OK);
    for(i=0;i<8;i++) CK(push(&s,i)==(i==7 ? DV_LIVE_READY : DV_LIVE_QUEUED));
    CK(inspect(&s,255u,8u)==0);
    CK(s.next_msg==256);
    CK(push(&s,8)==DV_LIVE_EPOCH && s.total_codec_frames==8u);
    CK(dv_live_end(&s)==DV_LIVE_OK);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_OK);
    CK(s.next_msg==256 && s.completed_packets==1);

    CK(dv_live_begin(&s,1)==DV_LIVE_OK);
    CK(dv_live_push(&s,NULL)==DV_LIVE_EINVAL);
    for(i=0;i<7;i++) CK(push(&s,i)==DV_LIVE_QUEUED);
    CK(dv_live_end(&s)==DV_LIVE_READY);
    CK(inspect(&s,1u,7u)==0);
    CK(s.queued==1 && s.total_codec_frames==7u);
    CK(dv_live_tx_done(&s,1)==DV_LIVE_OK);
    (void)n;
    fprintf(stderr,"guarded live queue: bounded failure, retry, CRC frame, PTT, epoch tests PASSED; state %zu B\n",
            sizeof(s));
    return 0;
}
