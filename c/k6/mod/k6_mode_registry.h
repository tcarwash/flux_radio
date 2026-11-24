/* k6_mode_registry: read-only installed-provider discovery (API 26).
   Host-testable pure C (no k6_api use): the matcher below decides, from a
   slot's header fields alone (kl_info), which mode ID an installed library
   image advertises. Only explicitly validated identities enumerate: the
   canonical SOTTO TEST module (voice only, ID 0x0100, no payload) and, with
   an explicit opt-in, the experimental SOTTO RFTEST fixture (ID 0x0101).
   Any other name, any non-library flag, or a level outside the running API
   yields 0 (no entry): nothing is guessed from a name, and no packet TX or
   voice TX capability is ever asserted here. No code is loaded or run.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODE_REGISTRY_H
#define K6_MODE_REGISTRY_H
#include <stdint.h>
#include "../k6_lib.h"

#define K6MODE_FN static __attribute__((unused))

/* Byte-exact 12-byte name compare. kl_info NUL-pads short names, and the
   C info.name array is signed char, so compare as unsigned bytes. */
K6MODE_FN int k6mode_name_is(const char name[12], const char want[12]) {
  unsigned i;
  for (i = 0; i < 12; i++)
    if ((unsigned char)name[i] != (unsigned char)want[i])
      return 0;
  return 1;
}

/* Which mode ID slot (flags, level, name from kl_info) advertises, or 0 for
   none. flags must be exactly KM_F_LIB (a runnable kept module, not a page,
   deck, resident or theme envelope); level must lie inside the running API
   (1..api_level). rftest_opt_in admits the experimental fixture; without it
   only the canonical SOTTO TEST voice provider enumerates. */
K6MODE_FN uint16_t k6mode_match(uint8_t flags, uint8_t level, uint8_t api_level,
                                const char name[12], int rftest_opt_in) {
  static const char sotto_test[12] = "SOTTO TEST";
  static const char sotto_rftest[12] = "SOTTO RFTEST";
  if (flags != KM_F_LIB || level == 0 || level > api_level)
    return 0;
  if (k6mode_name_is(name, sotto_test))
    return (uint16_t)K6_MODE_SOTTO_TEST;
  if (rftest_opt_in && k6mode_name_is(name, sotto_rftest))
    return (uint16_t)K6_MODE_SOTTO_RFTEST;
  return 0;
}

/* Packet-provider identity for the level-27 mailbox dispatcher. Returns the
   LOOPBACK-EMU mode ID (0x0200) only for a byte-exact "LOOPBACK-EMU" library
   image whose level lies inside the running API (which must be >= 27, the
   first level with provider-phase callbacks); anything else yields 0. No
   substring, prefix, or case folding: "LOOPBACK", "LOOPBACK-EMU-X", and
   padded variants all refuse. SOTTO voice entries never match here. */
K6MODE_FN uint16_t k6mode_match_packet(uint8_t flags, uint8_t level,
                                       uint8_t api_level, const char name[12]) {
  static const char emu[12] = "LOOPBACK-EMU";
  if (api_level < 27)
    return 0;
  if (flags != KM_F_LIB || level == 0 || level > api_level)
    return 0;
  if (k6mode_name_is(name, emu))
    return (uint16_t)K6_MODE_LOOPBACK_EMU;
  return 0;
}

/* Explicit signed capability record for a validated provider identity. The
   host loads the image with kl_load_action(slot, ..., 35), which checks the
   SipHash tag and the K6_ACTION registration; only afterwards does this
   table say what the identity may carry. Returns 1 with *flags_out
   (K6_MODE_* capability bits) and *max_out (largest TX payload), else 0 for
   unknown identities. SOTTO TEST / RFTEST report VOICE only with zero
   packet MTU: can_tx and the mailbox claim both refuse them, so a
   voice-only provider can never be dispatched for packet data. */
K6MODE_FN int k6mode_provider_caps(uint16_t id, uint8_t *flags_out,
                                   uint16_t *max_out) {
  if (!flags_out || !max_out)
    return 0;
  if (id == (uint16_t)K6_MODE_LOOPBACK_EMU) {
    *flags_out = (uint8_t)(K6_MODE_PACKET_TX | K6_MODE_PACKET_RX);
    *max_out = 128;
    return 1;
  }
  if (id == (uint16_t)K6_MODE_SOTTO_TEST ||
      id == (uint16_t)K6_MODE_SOTTO_RFTEST) {
    *flags_out = (uint8_t)K6_MODE_VOICE;
    *max_out = 0;
    return 1;
  }
  return 0;
}

/* 1 when this validated identity may carry a TX payload of len bytes, else
   0. Voice-only (no TX bit), unknown, or over-MTU providers refuse. */
K6MODE_FN int k6mode_can_tx(uint16_t id, unsigned len) {
  uint8_t flags = 0;
  uint16_t maxp = 0;
  if (!k6mode_provider_caps(id, &flags, &maxp))
    return 0;
  if (!(flags & (uint8_t)K6_MODE_PACKET_TX))
    return 0;
  if (len == 0 || len > maxp)
    return 0;
  return 1;
}

#endif
