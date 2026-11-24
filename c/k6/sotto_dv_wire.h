/* Flux DV wire-only runtime ABI, Cortex-M0/C11, no RF/hardware access.
 *
 * Include with the resident Sotto-1200 development target. It exposes
 * caller-owned packet queue and CRC-qualified 66-byte voice/control decoder.
 * TX on radio is NOT invoked by any of these functions.
 */
#ifndef FLUX_SOTTO_DV_WIRE_H
#define FLUX_SOTTO_DV_WIRE_H

#include <stdint.h>
#include "mod/dv_guard33_stream.h"
#include "mod/dv_guard33_session.h"

unsigned flux_dv_wire_rx66(const uint8_t physical[66], uint8_t frames[48],
                           uint8_t *message_id, unsigned *frame_count,
                           unsigned *offset_bytes);
unsigned flux_dv_wire_session_tx(const dv_session_ctrl_t *ctrl,
                                 uint16_t physical_words[33]);
unsigned flux_dv_wire_session_rx66(const uint8_t physical[66],
                                   dv_session_ctrl_t *ctrl,
                                   unsigned *offset_bytes);
unsigned flux_dv_wire_state_bytes(void);
int flux_dv_wire_begin(dv_guard33_stream_t *state, unsigned message_id);
int flux_dv_wire_push(dv_guard33_stream_t *state, const uint8_t codec6[6]);
int flux_dv_wire_finish(dv_guard33_stream_t *state);
const uint16_t *flux_dv_wire_peek(const dv_guard33_stream_t *state);
int flux_dv_wire_ack(dv_guard33_stream_t *state);

#endif
