/* ARMv6-M compilation probe for the bounded guarded DV packetizer.
 * Explicit caller-owned state: NO .bss, heap, IRQ side effects, SPI, PA,
 * microphone sampling or TX. K6 module allocates its 204-byte state from
 * the scratch arena or its own stack before calling these functions.
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include "dv_live_guard.h"

int dv_arm_begin(dv_live_t *state,uint32_t first_msg_id) {
    return dv_live_begin(state,(unsigned)first_msg_id);
}
int dv_arm_push(dv_live_t *state,const uint8_t frame[6]) {
    return dv_live_push(state,frame);
}
int dv_arm_finish(dv_live_t *state) {
    return dv_live_end(state);
}
int dv_arm_peek(const dv_live_t *state,uint16_t words[33]) {
    return dv_live_peek_words(state,words,0,0);
}
int dv_arm_confirm(dv_live_t *state,int observed_fsk_done) {
    return dv_live_tx_done(state,observed_fsk_done);
}
unsigned dv_arm_queue_depth(const dv_live_t *state) {
    return state?state->queued:0u;
}
unsigned dv_arm_state_bytes(void) {
    return (unsigned)sizeof(dv_live_t);
}
