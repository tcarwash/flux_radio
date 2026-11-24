/* INERT PROBE ONLY. No PTT, SPI, microphone, timers or radio writes.
 * Export a <=4-argument ABI for the actual Thumb/Cortex-M0 emulator.
 * This is not a runnable K6 module or a firmware update image. */
#include <stdint.h>
#include "dv_guard33_session.h"

__attribute__((used))
unsigned dv_arm_session_build(const dv_session_ctrl_t *s, uint16_t *out) {
    if (!s) return 0;
    return dv_session_build_words(out, s->kind, s->tag_hi, s->tag_lo,
                                  s->first_id, s->last_id,
                                  s->last_frames, s->segments);
}
__attribute__((used))
unsigned dv_arm_session_decode(const uint8_t *packet, dv_session_ctrl_t *out) {
    return (unsigned)dv_session_decode64(packet, out);
}
__attribute__((used))
unsigned dv_arm_session_select66(const uint8_t *physical,
                                  dv_session_ctrl_t *out, unsigned *offset) {
    return (unsigned)dv_session_select66(physical,out,offset);
}
__attribute__((used))
unsigned dv_arm_session_state_bytes(void) {
    return sizeof(dv_session_ctrl_t);
}
