/* packet_provider_fixture: host-testable installed-provider registry and
   emulated packet TX path for the KISS mailbox. Pure C, no k6_api use.
   One NON-APRS packet-capable emulated provider (LOOPBACK-EMU, ID 0x0200,
   TX+RX, 128-byte MTU) demonstrates real dispatch bytes-in -> bytes-out
   with no RF: the "transmit" copies into a caller-owned emit buffer. APRS
   native mode (ID 1, TX, 75) stays served by the TNC's own guarded path.
   SOTTO TEST (0x0100) and SOTTO RFTEST (0x0101) report VOICE ONLY with zero
   packet MTU: k6pf_can_tx() refuses them, so packets to a voice-only or
   missing provider never key. No division, no writable statics.
   SPDX-License-Identifier: MIT */
#ifndef K6_PACKET_PROVIDER_FIXTURE_H
#define K6_PACKET_PROVIDER_FIXTURE_H
#include <stdint.h>

#define K6PF_FN static __attribute__((unused))

#define K6PF_CAP_TX 0x01u
#define K6PF_CAP_RX 0x02u
#define K6PF_CAP_VOICE 0x04u

/* Non-APRS packet-capable emulator identity. 0x0200 is outside the native
   APRS range and the Sotto voice reservations (0x0100/0x0101). */
#define K6PF_MODE_LOOPBACK_EMU 0x0200u
#define K6PF_MODE_APRS1200 1u
#define K6PF_MODE_SOTTO_TEST 0x0100u
#define K6PF_MODE_SOTTO_RFTEST 0x0101u
#define K6PF_EMU_MAX 128u

typedef struct {
  uint16_t mode_id;
  uint8_t flags;
  uint16_t max_payload;
  uint8_t name_len;
  char name[12];
} k6pf_provider_t;

static const k6pf_provider_t k6pf_providers[] = {
  { K6PF_MODE_APRS1200, K6PF_CAP_TX, 75, 9,
    { 'A', 'P', 'R', 'S', '-', '1', '2', '0', '0', 0, 0, 0 } },
  { K6PF_MODE_LOOPBACK_EMU, (uint8_t)(K6PF_CAP_TX | K6PF_CAP_RX),
    K6PF_EMU_MAX, 12,
    { 'L', 'O', 'O', 'P', 'B', 'A', 'C', 'K', '-', 'E', 'M', 'U' } },
  { K6PF_MODE_SOTTO_TEST, K6PF_CAP_VOICE, 0, 10,
    { 'S', 'O', 'T', 'T', 'O', ' ', 'T', 'E', 'S', 'T', 0, 0 } },
  { K6PF_MODE_SOTTO_RFTEST, K6PF_CAP_VOICE, 0, 12,
    { 'S', 'O', 'T', 'T', 'O', ' ', 'R', 'F', 'T', 'E', 'S', 'T' } },
};

K6PF_FN unsigned k6pf_n_providers(void) {
  return (unsigned)(sizeof k6pf_providers / sizeof k6pf_providers[0]);
}

K6PF_FN const k6pf_provider_t *k6pf_find(uint16_t id) {
  unsigned i;
  for (i = 0; i < k6pf_n_providers(); i++)
    if (k6pf_providers[i].mode_id == id)
      return &k6pf_providers[i];
  return 0;
}

/* 1 when this provider may carry a TX payload of len bytes, else 0.
   Voice-only (no TX bit), unknown, or over-MTU providers refuse. */
K6PF_FN int k6pf_can_tx(uint16_t id, unsigned len) {
  const k6pf_provider_t *p = k6pf_find(id);
  if (!p)
    return 0;
  if (!(p->flags & K6PF_CAP_TX))
    return 0;
  if (len == 0 || len > p->max_payload)
    return 0;
  return 1;
}

/* Emulated provider transmit: NO RF. Copies the claimed mailbox payload
   into the caller-owned emit buffer (cap bytes) and reports the count in
   *emitted. Refuses (no copy, *emitted untouched) for voice-only, unknown,
   or over-MTU providers, or a short emit buffer. */
K6PF_FN int k6pf_emu_tx(uint16_t id, const uint8_t *p, unsigned len,
                        uint8_t *emit, unsigned cap, unsigned *emitted) {
  unsigned i;
  if (!p || !emit || !emitted)
    return -1;
  if (!k6pf_can_tx(id, len))
    return -2;
  if (cap < len)
    return -3;
  for (i = 0; i < len; i++)
    emit[i] = p[i];
  *emitted = len;
  return 0;
}

#endif
