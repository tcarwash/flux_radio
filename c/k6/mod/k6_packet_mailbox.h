/* k6_packet_mailbox: host-owned V1 bounded KISS packet mailbox.
   Pure C, no k6_api use, host-testable. The storage (k6mb_bus_t) lives in
   HOST memory (firmware static), never in a module's scratch/arena, so it
   survives foreground module switching and holds no stale arena pointers:
   the payload is inline bytes, addressed only by generation, never by
   pointer. Single TX packet at a time; a small RX ring with backpressure.
   Every refusal is fail-closed (negative code, no state change, never key).
   A consumed generation never replays: claim moves STAGED->CLAIMED, and
   only the host release of a COMPLETE/FAILED slot frees it.
   Ownership phases (never nested): stage requires the TNC foreground phase
   (tnc_active==1); claim/complete require the provider phase
   (tnc_active==0), i.e. the provider module runs AFTER the TNC returned,
   never nested inside it. No division, no writable statics.
   SPDX-License-Identifier: MIT */
#ifndef K6_PACKET_MAILBOX_H
#define K6_PACKET_MAILBOX_H
#include <stdint.h>

#define K6MB_FN static __attribute__((unused))

/* Mailbox contract version. The API27 host exposes this in mb_version and
   the TNC refuses to stage unless it reads exactly this value: an old or
   foreign build never gets a staged packet by accident. */
#define K6MB_VERSION 1u
/* Bounded payload: covers the 75-byte APRS DATA path with headroom for a
   future 128-byte datagram provider. Anything longer is refused whole. */
#define K6MB_MAX_PAYLOAD 128u
/* RX ring depth (provider air->host direction). Full means FULL: the
   provider keeps (or drops, by its own documented policy) its frame; the
   mailbox never overwrites an untaken frame. */
#define K6MB_RX_DEPTH 4u
/* Distinct provider entry action (K6_ACTION_* 32..34 are taken). The host
   dispatcher runs this action in the provider module AFTER the TNC has
   returned; the provider reads the mailbox, verifies identity/capability
   and reports a result. Never invoked nested inside the TNC. */
#define K6_ACTION_PACKET_PROVIDER 35
/* TNC return code when it staged exactly one packet and released the
   foreground (UART already restored by the TNC). The host must dispatch a
   compatible provider next, not resume the TNC blindly. */
#define K6MB_TNC_STAGED (-40)

/* Capability bits: same values as K6_MODE_* / FLUXK_FLAG_*, restated here
   so this header stays standalone. VOICE-only bits never satisfy TX. */
#define K6MB_CAP_TX 0x01u
#define K6MB_CAP_RX 0x02u
#define K6MB_CAP_VOICE 0x04u

/* Slot states. */
#define K6MB_EMPTY 0u
#define K6MB_STAGED 1u
#define K6MB_CLAIMED 2u
#define K6MB_COMPLETE 3u
#define K6MB_FAILED 4u

/* Fail-closed error codes (all negative; 0 is success). */
#define K6MB_OK 0
#define K6MB_E_MALFORMED (-1)
#define K6MB_E_TOO_LONG (-2)
#define K6MB_E_BUSY (-3)
#define K6MB_E_NOT_FOUND (-4)
#define K6MB_E_REFUSED (-5)
#define K6MB_E_EXPIRED (-6)
#define K6MB_E_CORRUPT (-7)
#define K6MB_E_GENERATION (-8)
#define K6MB_E_NESTED (-9)
#define K6MB_E_FULL (-10)
#define K6MB_E_EMPTY (-11)

/* Provider completion report. */
#define K6MB_TX_SENT 0
#define K6MB_TX_FAILED 1
#define K6MB_TX_REFUSED 2

/* One TX descriptor: inline payload, no pointers. Naturally aligned. */
typedef struct {
  uint8_t version;                 /* K6MB_VERSION */
  uint8_t state;                   /* K6MB_* */
  uint16_t mode_id;                /* destination provider mode */
  uint16_t len;                    /* payload bytes used */
  uint16_t need_caps;              /* capability the provider must hold */
  uint32_t generation;             /* nonzero, consumed exactly once */
  uint32_t created_ms;             /* host clock at stage */
  uint32_t ttl_ms;                 /* expiry window */
  uint16_t crc;                    /* CRC-16/CCITT over header+payload */
  uint8_t payload[K6MB_MAX_PAYLOAD];
  int32_t result;                  /* valid in COMPLETE: K6MB_TX_* */
} k6mb_slot_t;

/* One RX frame (provider -> TNC direction). */
typedef struct {
  uint16_t mode_id;
  uint16_t len;
  uint32_t generation;
  uint8_t data[K6MB_MAX_PAYLOAD];
} k6mb_rxslots_t;

/* The whole host-owned bus: one TX slot plus an RX ring. */
typedef struct {
  uint8_t tnc_active;              /* 1: TNC owns foreground/UART */
  uint8_t rx_head;                 /* ring read index */
  uint8_t rx_count;                /* frames held (0..K6MB_RX_DEPTH) */
  uint8_t pad;
  uint32_t gen;                    /* generation allocator, never hands out 0 */
  k6mb_slot_t tx;
  k6mb_rxslots_t rx[K6MB_RX_DEPTH];
} k6mb_bus_t;

/* CRC-16/CCITT-FALSE step: fold one byte into a running CRC. */
K6MB_FN uint16_t k6mb_crc_step(uint16_t c, uint8_t b) {
  unsigned k;
  c ^= (uint16_t)((uint16_t)b << 8);
  for (k = 0; k < 8; k++)
    c = (uint16_t)((c & 0x8000u) ? (uint16_t)((c << 1) ^ 0x1021u)
                                 : (uint16_t)(c << 1));
  return c;
}

/* CRC input framing: mode_id LE, len LE, need_caps LE, then payload bytes.
   Single pass, no temp buffer. */
K6MB_FN uint16_t k6mb_slot_crc(uint16_t mode_id, uint16_t len,
                               uint16_t need_caps, const uint8_t *p) {
  uint8_t hdr[6];
  uint16_t c = 0xFFFFu;
  unsigned i;
  hdr[0] = (uint8_t)(mode_id & 255u);
  hdr[1] = (uint8_t)((mode_id >> 8) & 255u);
  hdr[2] = (uint8_t)(len & 255u);
  hdr[3] = (uint8_t)((len >> 8) & 255u);
  hdr[4] = (uint8_t)(need_caps & 255u);
  hdr[5] = (uint8_t)((need_caps >> 8) & 255u);
  for (i = 0; i < 6; i++)
    c = k6mb_crc_step(c, hdr[i]);
  for (i = 0; i < len; i++)
    c = k6mb_crc_step(c, p[i]);
  return c;
}

K6MB_FN void k6mb_init(k6mb_bus_t *b) {
  unsigned i;
  uint8_t *q = (uint8_t *)b;
  if (!b)
    return;
  for (i = 0; i < (unsigned)sizeof *b; i++)
    q[i] = 0;
  b->tx.version = (uint8_t)K6MB_VERSION;
}

/* Next nonzero generation (wraps safely past zero). */
K6MB_FN uint32_t k6mb_next_gen(k6mb_bus_t *b) {
  b->gen++;
  if (b->gen == 0)
    b->gen = 1;
  return b->gen;
}

/* TNC foreground stages one packet. Requires tnc_active==1 (else NESTED:
   nobody stages outside the TNC run), an EMPTY slot (else BUSY: single
   packet at a time, never overwrite), 1..128 payload bytes, and nonzero
   ttl. Copies bytes inline and seals with CRC + generation. */
K6MB_FN int k6mb_stage(k6mb_bus_t *b, uint32_t now_ms, uint16_t mode_id,
                       uint16_t need_caps, const uint8_t *p, unsigned len,
                       uint32_t ttl_ms, uint32_t *gen_out) {
  unsigned i;
  if (!b || !p || !gen_out)
    return K6MB_E_MALFORMED;
  if (!b->tnc_active)
    return K6MB_E_NESTED;
  if (b->tx.state != K6MB_EMPTY)
    return K6MB_E_BUSY;
  if (len == 0)
    return K6MB_E_MALFORMED;
  if (len > K6MB_MAX_PAYLOAD)
    return K6MB_E_TOO_LONG;
  if (ttl_ms == 0)
    return K6MB_E_MALFORMED;
  if (mode_id == 0 || mode_id == 0xFFFFu)
    return K6MB_E_MALFORMED;
  b->tx.version = (uint8_t)K6MB_VERSION;
  b->tx.state = K6MB_STAGED;
  b->tx.mode_id = mode_id;
  b->tx.len = (uint16_t)len;
  b->tx.need_caps = need_caps;
  b->tx.generation = k6mb_next_gen(b);
  b->tx.created_ms = now_ms;
  b->tx.ttl_ms = ttl_ms;
  for (i = 0; i < len; i++)
    b->tx.payload[i] = p[i];
  b->tx.crc = k6mb_slot_crc(mode_id, (uint16_t)len, need_caps, p);
  b->tx.result = 0;
  *gen_out = b->tx.generation;
  return K6MB_OK;
}

/* Provider entry (action 35) claims the staged packet. Requires
   tnc_active==0 (else NESTED: never inside the TNC run), STAGED state,
   a live (unexpired, wrap-safe) generation, an exact mode_id match, the
   held capability covering need_caps (VOICE-only never covers TX), and a
   valid CRC. Any failure except NESTED/NOT_FOUND/GENERATION marks the slot
   FAILED so the payload can never replay; the host must release it. */
K6MB_FN int k6mb_claim(const k6mb_bus_t *b_const, uint32_t now_ms,
                       uint16_t mode_id, uint16_t held_caps,
                       uint32_t generation, uint8_t *out, unsigned cap,
                       unsigned *len_out) {
  k6mb_bus_t *b = (k6mb_bus_t *)b_const;
  unsigned i;
  if (!b || !out || !len_out)
    return K6MB_E_MALFORMED;
  if (b->tnc_active)
    return K6MB_E_NESTED;
  if (b->tx.state != K6MB_STAGED)
    return K6MB_E_NOT_FOUND;
  if (generation == 0 || generation != b->tx.generation) {
    b->tx.state = K6MB_FAILED;
    return K6MB_E_GENERATION;
  }
  if ((uint32_t)(now_ms - b->tx.created_ms) > b->tx.ttl_ms) {
    b->tx.state = K6MB_FAILED;
    return K6MB_E_EXPIRED;
  }
  if (mode_id != b->tx.mode_id ||
      (uint16_t)(held_caps & b->tx.need_caps) != b->tx.need_caps) {
    b->tx.state = K6MB_FAILED;
    return K6MB_E_REFUSED;
  }
  if (b->tx.crc != k6mb_slot_crc(b->tx.mode_id, b->tx.len, b->tx.need_caps,
                                 b->tx.payload)) {
    b->tx.state = K6MB_FAILED;
    return K6MB_E_CORRUPT;
  }
  if (cap < b->tx.len)
    return K6MB_E_MALFORMED;
  for (i = 0; i < b->tx.len; i++)
    out[i] = b->tx.payload[i];
  *len_out = b->tx.len;
  b->tx.state = K6MB_CLAIMED;
  return K6MB_OK;
}

/* Provider reports its TX result. Only CLAIMED + exact generation. */
K6MB_FN int k6mb_complete(k6mb_bus_t *b, uint32_t generation, int32_t result) {
  if (!b)
    return K6MB_E_MALFORMED;
  if (b->tnc_active)
    return K6MB_E_NESTED;
  if (b->tx.state != K6MB_CLAIMED)
    return K6MB_E_NOT_FOUND;
  if (generation == 0 || generation != b->tx.generation)
    return K6MB_E_GENERATION;
  if (result != K6MB_TX_SENT && result != K6MB_TX_FAILED &&
      result != K6MB_TX_REFUSED)
    return K6MB_E_MALFORMED;
  b->tx.result = result;
  b->tx.state = K6MB_COMPLETE;
  return K6MB_OK;
}

/* Host frees a finished slot after consuming the result. Only the host
   (never a module) calls this, and only on COMPLETE/FAILED. */
K6MB_FN int k6mb_release(k6mb_bus_t *b) {
  unsigned i;
  if (!b)
    return K6MB_E_MALFORMED;
  if (b->tx.state != K6MB_COMPLETE && b->tx.state != K6MB_FAILED)
    return K6MB_E_NOT_FOUND;
  b->tx.state = K6MB_EMPTY;
  b->tx.mode_id = 0;
  b->tx.len = 0;
  b->tx.need_caps = 0;
  b->tx.generation = 0;
  b->tx.created_ms = 0;
  b->tx.ttl_ms = 0;
  b->tx.crc = 0;
  b->tx.result = 0;
  for (i = 0; i < (unsigned)sizeof b->tx.payload; i++)
    b->tx.payload[i] = 0;
  return K6MB_OK;
}

/* TNC/host polls the finished result without consuming it. */
K6MB_FN int k6mb_poll(const k6mb_bus_t *b, uint32_t *gen_out,
                      uint32_t *state_out, int32_t *result_out) {
  if (!b || !gen_out || !state_out || !result_out)
    return K6MB_E_MALFORMED;
  *gen_out = b->tx.generation;
  *state_out = b->tx.state;
  *result_out = b->tx.result;
  return K6MB_OK;
}

/* RX direction: provider offers one received frame. Backpressure: FULL
   when the ring holds K6MB_RX_DEPTH untaken frames; the mailbox never
   overwrites. Requires provider phase (not nested in TNC). */
K6MB_FN int k6mb_rx_put(k6mb_bus_t *b, uint16_t mode_id, const uint8_t *p,
                        unsigned len, uint32_t *gen_out) {
  unsigned i, slot;
  if (!b || !p || !gen_out)
    return K6MB_E_MALFORMED;
  if (b->tnc_active)
    return K6MB_E_NESTED;
  if (b->rx_count >= K6MB_RX_DEPTH)
    return K6MB_E_FULL;
  if (len == 0 || len > K6MB_MAX_PAYLOAD)
    return len == 0 ? K6MB_E_MALFORMED : K6MB_E_TOO_LONG;
  slot = (unsigned)((b->rx_head + b->rx_count) % K6MB_RX_DEPTH);
  b->rx[slot].mode_id = mode_id;
  b->rx[slot].len = (uint16_t)len;
  b->rx[slot].generation = k6mb_next_gen(b);
  for (i = 0; i < len; i++)
    b->rx[slot].data[i] = p[i];
  b->rx_count++;
  *gen_out = b->rx[slot].generation;
  return K6MB_OK;
}

/* RX direction: TNC takes the oldest frame. Takes invalidate: the frame
   is zeroed and the head advances, so it can never be taken twice. */
K6MB_FN int k6mb_rx_take(k6mb_bus_t *b, uint8_t *out, unsigned cap,
                        uint16_t *mode_out, unsigned *len_out) {
  unsigned i, slot;
  if (!b || !out || !mode_out || !len_out)
    return K6MB_E_MALFORMED;
  if (b->rx_count == 0)
    return K6MB_E_EMPTY;
  slot = b->rx_head;
  if (cap < b->rx[slot].len)
    return K6MB_E_MALFORMED;
  for (i = 0; i < b->rx[slot].len; i++)
    out[i] = b->rx[slot].data[i];
  *mode_out = b->rx[slot].mode_id;
  *len_out = b->rx[slot].len;
  for (i = 0; i < (unsigned)sizeof b->rx[slot].data; i++)
    b->rx[slot].data[i] = 0;
  b->rx[slot].mode_id = 0;
  b->rx[slot].len = 0;
  b->rx[slot].generation = 0;
  b->rx_head = (uint8_t)((b->rx_head + 1) % K6MB_RX_DEPTH);
  b->rx_count--;
  return K6MB_OK;
}

#endif
