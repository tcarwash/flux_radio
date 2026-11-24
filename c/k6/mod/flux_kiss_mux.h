/* Flux mode-aware KISS TNC: shared protocol, parser and backend registry.
   Host-testable pure C (no k6_api use): c/k6/mod/fluxtnc.c is the module,
   tests/test_k6_flux_kiss_module.py exercises this file on the host.
   No libraries, no division, no writable statics. SPDX-License-Identifier: MIT */
#ifndef FLUX_KISS_MUX_H
#define FLUX_KISS_MUX_H
#include <stdint.h>

#define FLUXK_FN static __attribute__((unused))

/* ---- KISS framing (raw serial KISS, API level 14) ---- */
#define FLUXK_FEND 0xC0u
#define FLUXK_FESC 0xDBu
#define FLUXK_TFEND 0xDCu
#define FLUXK_TFESC 0xDDu
/* Command byte is (port << 4) | cmd; this TNC only serves port 0. */
#define FLUXK_CMD_DATA 0x00u       /* AX.25 bytes WITHOUT FCS, 17..75 bytes */
#define FLUXK_CMD_SETHW 0x06u      /* Flux SET_HARDWARE control plane */
/* Largest KISS payload this TNC accepts: 75-byte APRS DATA. Anything longer
   (including any valid SET_HARDWARE request, which is at most 6 bytes) is
   dropped at parse time and must never key the transmitter. */
#define FLUXK_MAX_PAYLOAD 75u
#define FLUXK_MIN_APRS 17u
#define FLUXK_PARSE_BUF 80u        /* type byte + payload headroom */

/* ---- Flux SET_HARDWARE payload: 'F','X',version,op,args... ---- */
#define FLUXK_VER 1u
enum {
  FLUXK_OP_LIST = 1,   /* no args; one response frame per mode + 0xFFFF terminator */
  FLUXK_OP_GET = 2,    /* no args; body is the selected mode id u16le (0 = none) */
  FLUXK_OP_SET = 3,    /* args: mode id u16le; body echoes the selected id */
  FLUXK_OP_CAPS = 4,   /* no args; body max_rx u16le, max_tx u16le */
  FLUXK_OP_STATUS = 5, /* no args; body tx_success u16le, tx_fail u16le */
  FLUXK_OP_STOP = 6    /* no args; orderly module exit, no TX */
};
enum {
  FLUXK_OK = 0,
  FLUXK_ERR_MALFORMED = 1,
  FLUXK_ERR_UNKNOWN = 2,
  FLUXK_ERR_UNSUPPORTED = 3,
  FLUXK_ERR_BUSY = 4,
  FLUXK_ERR_IO = 5,
  FLUXK_ERR_TOO_LONG = 6,
  FLUXK_ERR_REFUSED = 7
};
/* Responses: KISS type 0x06, data 'F','X',1,op|0x80,status,body... */

/* ---- Modes ---- */
#define FLUXK_MODE_APRS1200 1u
#define FLUXK_FLAG_TX 0x01u        /* packet TX implemented */
#define FLUXK_FLAG_RX 0x02u        /* packet RX implemented (native receive only) */
#define FLUXK_FLAG_VOICE 0x04u     /* voice path, never packet data */
/* RSSI busy threshold, same coarse carrier check as c/k6/mod/aprsbeacon.c. */
#define FLUXK_BUSY_DBM -82

/* Opaque-installed-provider discovery: API level 14 exposes no descriptor of
   installed modem providers (no ABI record to enumerate; see docs on the
   API26 gap), so this table lists only the natively supported mode. A mode
   is advertised only when its TX path is actually present at run time
   (APRS 1200 needs api->afsk_tx_send); voice Sotto is never advertised as
   packet data. Future providers append entries conforming to this shape. */
typedef struct {
  uint16_t mode_id;
  uint8_t flags;
  uint16_t max_payload;
  uint8_t name_len;                /* ASCII bytes in name, at most 12 */
  char name[12];
} fluxk_backend_t;

static const fluxk_backend_t fluxk_backends[] = {
  { FLUXK_MODE_APRS1200, FLUXK_FLAG_TX, FLUXK_MAX_PAYLOAD, 9,
    { 'A', 'P', 'R', 'S', '-', '1', '2', '0', '0', 0, 0, 0 } }
};

FLUXK_FN unsigned fluxk_n_backends(void) {
  return (unsigned)(sizeof fluxk_backends / sizeof fluxk_backends[0]);
}
FLUXK_FN const fluxk_backend_t *fluxk_backend_find(uint16_t id) {
  unsigned i;
  for (i = 0; i < fluxk_n_backends(); i++)
    if (fluxk_backends[i].mode_id == id)
      return &fluxk_backends[i];
  return 0;
}

/* Explicit installed-provider descriptor validation. Read-only API26 may
   enumerate voice modules; these records are never packet TX/RX backends.
   Compare the complete NUL-padded identity so a bogus/old payload entry
   can never inherit a native packet transmit capability. */
FLUXK_FN int fluxk_provider_valid(uint16_t id, uint8_t flags,
                                   uint16_t mtu, uint8_t n,
                                   const char name[12]) {
  static const char test[12] = "SOTTO TEST";
  static const char rftest[12] = "SOTTO RFTEST";
  const char *want;
  unsigned expected, i;
  if (!name || flags != FLUXK_FLAG_VOICE || mtu != 0)
    return 0;
  if (id == 0x0100u) {
    want = test;
    expected = 10u;
  } else if (id == 0x0101u) {
    want = rftest;
    expected = 12u;
  } else {
    return 0;
  }
  if (n != expected)
    return 0;
  for (i = 0; i < 12; i++)
    if ((uint8_t)name[i] != (uint8_t)want[i])
      return 0;
  return 1;
}

/* ---- Small pure helpers ---- */
FLUXK_FN int fluxk_hz_ok(uint32_t hz) {
  return ((hz >= 144000000u && hz <= 148000000u) ||
          (hz >= 420000000u && hz <= 450000000u));
}
FLUXK_FN void fluxk_sat_inc(uint16_t *v) {
  if (*v != 0xFFFFu)
    ++(*v);
}
/* DATA path gate: 1 means "encode and run the guarded TX chain", 0 means
   drop without keying (bad length, no mode selected, no backend, no AFSK). */
FLUXK_FN int fluxk_data_tx_ok(uint16_t cur_mode, unsigned n, int afsk_ok) {
  if (n < FLUXK_MIN_APRS || n > FLUXK_MAX_PAYLOAD)
    return 0;
  if (cur_mode != FLUXK_MODE_APRS1200)
    return 0;
  if (!afsk_ok)
    return 0;
  if (!fluxk_backend_find(FLUXK_MODE_APRS1200))
    return 0;
  return 1;
}

/* ---- SET_HARDWARE request parsing ----
   Returns FLUXK_OK with *op set and *alen = bytes after the op byte,
   FLUXK_ERR_MALFORMED (unusable frame), or FLUXK_ERR_UNKNOWN (bad op). */
FLUXK_FN unsigned fluxk_fx_parse(const uint8_t *p, unsigned n,
                                 unsigned *op, unsigned *alen) {
  if (!p || !op || !alen)
    return FLUXK_ERR_MALFORMED;
  if (n < 4)
    return FLUXK_ERR_MALFORMED;
  if (p[0] != (uint8_t)'F' || p[1] != (uint8_t)'X' || p[2] != FLUXK_VER)
    return FLUXK_ERR_MALFORMED;
  if (p[3] < FLUXK_OP_LIST || p[3] > FLUXK_OP_STOP)
    return FLUXK_ERR_UNKNOWN;
  *op = p[3];
  *alen = n - 4;
  return FLUXK_OK;
}
/* Per-op argument check; call only after fluxk_fx_parse returned OK. */
FLUXK_FN unsigned fluxk_sethw_validate(unsigned op, unsigned alen) {
  if (op == FLUXK_OP_SET)
    return alen == 2 ? FLUXK_OK : FLUXK_ERR_MALFORMED;
  if (op >= FLUXK_OP_LIST && op <= FLUXK_OP_STOP)
    return alen == 0 ? FLUXK_OK : FLUXK_ERR_MALFORMED;
  return FLUXK_ERR_UNKNOWN;
}

/* ---- Response encoding ---- */
FLUXK_FN unsigned fluxk_resp_hdr(uint8_t *o, unsigned op, unsigned st) {
  o[0] = (uint8_t)'F';
  o[1] = (uint8_t)'X';
  o[2] = FLUXK_VER;
  o[3] = (uint8_t)((op & 0x7Fu) | 0x80u);
  o[4] = (uint8_t)st;
  return 5;
}
FLUXK_FN unsigned fluxk_put_u16le(uint8_t *o, unsigned v) {
  o[0] = (uint8_t)(v & 255u);
  o[1] = (uint8_t)((v >> 8) & 255u);
  return 2;
}
/* One LIST body: mode_id u16le, flags u8, max_payload u16le, name_len u8,
   name ASCII. The terminator is id 0xFFFF with zero fields. */
FLUXK_FN unsigned fluxk_list_body(uint8_t *o, uint16_t id, uint8_t flags,
                                  uint16_t maxp, const char *name,
                                  unsigned nlen) {
  unsigned n = 0, i;
  if (nlen > 12)
    nlen = 12;
  n += fluxk_put_u16le(o + n, id);
  o[n++] = flags;
  n += fluxk_put_u16le(o + n, maxp);
  o[n++] = (uint8_t)nlen;
  for (i = 0; i < nlen; i++)
    o[n++] = (uint8_t)name[i];
  return n;
}
/* KISS-encode one frame: FEND, type, escaped payload, FEND. Returns the
   frame length, or 0 when out (cap bytes) is too small. */
FLUXK_FN unsigned fluxk_kiss_encode(uint8_t *out, unsigned cap, unsigned type,
                                    const uint8_t *p, unsigned n) {
  unsigned w = 0, i;
  if (!out || (!p && n))
    return 0;
  if (cap < 3)
    return 0;
  out[w++] = FLUXK_FEND;
  /* Worst case every byte escapes; keep room for the byte(s) + closing FEND. */
  for (i = 0; i < 1 + n; i++) {
    uint8_t b = i == 0 ? (uint8_t)type : p[i - 1];
    if (b == FLUXK_FEND || b == FLUXK_FESC) {
      if (w + 3 > cap)
        return 0;
      out[w++] = FLUXK_FESC;
      out[w++] = (uint8_t)(b == FLUXK_FEND ? FLUXK_TFEND : FLUXK_TFESC);
    } else {
      if (w + 2 > cap)
        return 0;
      out[w++] = b;
    }
  }
  out[w++] = FLUXK_FEND;
  return w;
}

/* ---- Bounded KISS stream parser ----
   Feed raw UART bytes; on FLUXK_KISS_FRAME, *type is the command byte and
   pay[0..*n) the unescaped payload. Oversized frames, invalid escapes,
   escapes cut by FEND, empty frames and type-only frames (a command byte
   with no payload: this TNC has no zero-payload commands) drop without a
   frame (DROPPED for the corrupt ones, NEED for mere delimiters) and
   resync on the next FEND, so a malformed frame can never become a TX
   payload. */
enum { FLUXK_KISS_NEED = 0, FLUXK_KISS_FRAME = 1, FLUXK_KISS_DROPPED = -1 };
typedef struct {
  uint8_t buf[FLUXK_PARSE_BUF];
  uint16_t len;      /* bytes stored, including the type byte */
  uint8_t esc;       /* previous byte was FESC */
  uint8_t in;        /* inside a frame (seen opening FEND) */
  uint8_t overflow;  /* discarding until the closing FEND */
} fluxk_rx_t;

FLUXK_FN void fluxk_rx_reset(fluxk_rx_t *r) {
  r->len = 0;
  r->esc = 0;
  r->in = 0;
  r->overflow = 0;
}
FLUXK_FN int fluxk_rx_feed(fluxk_rx_t *r, uint8_t b, unsigned *type,
                           uint8_t *pay, unsigned *n) {
  if (b == FLUXK_FEND) {
    if (r->esc || r->overflow) {       /* cut escape / oversized: drop */
      int dropped = (int)r->esc | (int)r->overflow;
      fluxk_rx_reset(r);
      r->in = 1; /* FEND is both end and start of the next KISS frame. */
      return dropped ? FLUXK_KISS_DROPPED : FLUXK_KISS_NEED;
    }
    if (!r->in || r->len <= 1) {        /* delimiter / empty / type-only */
      r->in = 1;
      r->len = 0;
      r->esc = 0;
      return FLUXK_KISS_NEED;
    }
    {
      /* Over-long payloads are dropped whole, never truncated into a
         TX candidate: the caller must not key on them. */
      unsigned i, m = (unsigned)r->len - 1;
      if (m > FLUXK_MAX_PAYLOAD) {
        fluxk_rx_reset(r);
        r->in = 1;
        return FLUXK_KISS_DROPPED;
      }
      *type = r->buf[0];
      for (i = 0; i < m; i++)
        pay[i] = r->buf[1 + i];
      *n = m;
      fluxk_rx_reset(r);
      r->in = 1; /* Shared KISS delimiter may start the next frame. */
      return FLUXK_KISS_FRAME;
    }
  }
  if (!r->in)
    return FLUXK_KISS_NEED;            /* inter-frame noise */
  if (r->overflow)
    return FLUXK_KISS_NEED;            /* still discarding */
  if (r->esc) {
    uint8_t v;
    r->esc = 0;
    if (b == FLUXK_TFEND)
      v = FLUXK_FEND;
    else if (b == FLUXK_TFESC)
      v = FLUXK_FESC;
    else {                             /* invalid escape: drop, resync */
      fluxk_rx_reset(r);
      return FLUXK_KISS_DROPPED;
    }
    if (r->len >= sizeof r->buf) {
      r->overflow = 1;
      return FLUXK_KISS_DROPPED;
    }
    r->buf[r->len++] = v;
    return FLUXK_KISS_NEED;
  }
  if (b == FLUXK_FESC) {
    r->esc = 1;
    return FLUXK_KISS_NEED;
  }
  if (r->len >= sizeof r->buf) {
    r->overflow = 1;
    return FLUXK_KISS_DROPPED;
  }
  r->buf[r->len++] = b;
  return FLUXK_KISS_NEED;
}

#endif
