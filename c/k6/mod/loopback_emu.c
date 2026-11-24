/* LOOPBACK-EMU installable packet provider (OFFLINE exercise, no RF).
   Runs ONLY as the host-dispatched K6_ACTION(35) provider phase: after the
   Flux TNC staged one packet and returned K6MB_TNC_STAGED (-40), the host
   loads this separately authenticated installed module (kl_load_action
   checks the SipHash tag and the K6_ACTION registration below) and runs it
   with the provider-phase mailbox callbacks. The provider claims the staged
   packet for its own exact mode (0x0200) with TX+RX capability, echoes the
   claimed bytes back through the RX ring entirely in RAM, and completes
   SENT. No keying, no modulation, no tuning, no register writes: this file
   never calls tx_on/tx_pa/afsk_tx_send/fsk_tx_send/tune/fd_write, so there
   is no RF path to audit. Voice-only modes (SOTTO TEST/RFTEST) are refused
   by the mailbox claim itself (VOICE never covers need_caps) and are never
   claimed here: only K6_MODE_LOOPBACK_EMU is ever passed to claim_packet.
   Strict refusal: any level other than exactly 27, a wrong mb_version, a
   missing provider-phase callback, nothing staged, or a failed claim ends
   the run with a negative code and no state change outside the mailbox.
   No libraries, no division, no writable statics: state lives in scratch.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 27
#define K6_MODULE_OPT z
#include "../k6_mod.h"

K6_MODULE_NAME("LOOPBACK-EMU");
K6_ACTION(K6_ACTION_PACKET_PROVIDER);

/* Held capability of this identity: TX+RX packet, 128-byte MTU. Must stay
   identical to the k6mode_provider_caps record for 0x0200. */
#define EMU_HELD_CAPS ((uint8_t)(K6_MODE_PACKET_TX | K6_MODE_PACKET_RX))

int k6_main(const k6_api_t *api) {
  uint32_t room = 0;
  uint8_t *mem;
  uint32_t gen = 0, state = 0;
  int32_t result = 0;
  unsigned len = 0;
  /* Exact level-27 host only: dev-radio API29 images assign different
     functions to these tail slots, so never call them on a level guess. */
  if (api->level != 27)
    return -3;
  if (api->mb_version != (uint32_t)K6MB_VERSION)
    return -3;
  if (!api->poll_result || !api->claim_packet || !api->complete_packet ||
      !api->publish_rx)
    return -3;
  /* Provider phase never stages or takes: stage_packet/take_rx belong to
     the TNC run that already returned. */
  mem = (uint8_t *)api->scratch(&room);
  if (!mem || room < (uint32_t)K6MB_MAX_PAYLOAD)
    return -8;
  if (api->poll_result(&gen, &state, &result) != 0)
    return -4;
  if (gen == 0 || state != (uint32_t)K6MB_STAGED)
    return -5;                       /* nothing staged: touch nothing */
  /* Claim exactly our own mode: a staged frame for any other mode (native
     APRS, SOTTO voice, unknown) fails here fail-closed and the slot can
     never replay. */
  if (api->claim_packet((uint16_t)K6_MODE_LOOPBACK_EMU, gen, mem,
                        (uint16_t)K6MB_MAX_PAYLOAD, &len) != 0)
    return -6;
  if (len == 0 || len > (unsigned)K6MB_MAX_PAYLOAD) {
    api->complete_packet(gen, (int32_t)K6MB_TX_REFUSED);
    return -7;
  }
  /* RAM loopback: echo the claimed bytes into the RX ring. No RF. */
  if (api->publish_rx((uint16_t)K6_MODE_LOOPBACK_EMU, mem, (uint16_t)len) != 0) {
    api->complete_packet(gen, (int32_t)K6MB_TX_FAILED);
    return -7;
  }
  if (api->complete_packet(gen, (int32_t)K6MB_TX_SENT) != 0)
    return -9;
  api->say("loopback-emu: packet looped");
  return 0;
}
