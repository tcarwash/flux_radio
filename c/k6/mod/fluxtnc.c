/* Flux mode-aware KISS TNC loadable module (OFFLINE build, no RF here).
   Raw serial KISS on the programming-cable UART while it runs (API 14):
   KISS type 0x00 port 0 carries AX.25 bytes WITHOUT FCS for the APRS
   backend, exactly what ap_tx_encode expects (17..75 bytes; the host owns
   the callsign bytes, this module never rewrites them). KISS type 0x06 is
   the Flux SET_HARDWARE control plane (see flux_kiss_mux.h and
   docs/k6-flux-kiss-tnc.md). Mode 1 is APRS 1200 DATA; RX is not advertised
   (no native receive path) and voice Sotto is never advertised as data.
   TX reuses the guarded steps of c/k6/mod/aprsbeacon.c on the CURRENT
   receive frequency: ham TX band check, two-sample RSSI idle check,
   bounded tx_on(3000), afsk_tx_send, tx_off always, no retry. Host KISS
   DATA itself is the explicit TX request (no UI confirmation step), but
   mode selection (SET) never transmits.
   No libraries, no division, no writable statics: state lives in scratch.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 14
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "aprs_tx.h"
#include "flux_kiss_mux.h"

K6_MODULE_NAME("FLUX TNC");

#ifndef FLUXTNC_RUN_MS
#define FLUXTNC_RUN_MS 3600000u   /* 1 h, like aprskissrx; a key ends it sooner */
#endif
#define FLUXTNC_DRAIN 128u        /* UART bytes per loop turn: bounded, resyncs */

/* Everything the module owns lives here, in scratch (never .data/.bss). */
typedef struct {
  fluxk_rx_t kiss;                /* KISS frame reassembly */
  ap_tx_t enc;                    /* Bell 202 bitstream being sent */
  uint8_t pay[FLUXK_MAX_PAYLOAD]; /* one completed frame's payload */
  uint8_t out[96];                /* one encoded response frame */
  uint16_t tx_ok, tx_fail;        /* STATUS counters, saturating */
  uint16_t cur_mode;              /* 0 = none selected */
  uint8_t kiss_on;
  uint8_t keyed;
} fluxtnc_t;

static void fluxtnc_zero(fluxtnc_t *s) {
  uint8_t *p = (uint8_t *)s;
  unsigned i;
  for (i = 0; i < (unsigned)sizeof *s; i++)
    p[i] = 0;
}

/* One response frame: KISS type 0x06 + 'F','X',1,op|0x80,status,body. */
static void fluxtnc_send(const k6_api_t *api, fluxtnc_t *s, unsigned op,
                         unsigned st, const uint8_t *body, unsigned blen) {
  uint8_t d[5 + 2 + 1 + 2 + 1 + 12]; /* hdr + largest LIST entry */
  unsigned dn, n;
  if (api->level < 14 || !api->serial_write)
    return;
  dn = fluxk_resp_hdr(d, op, st);
  {
    unsigned i;
    for (i = 0; i < blen && dn < sizeof d; i++)
      d[dn++] = body[i];
  }
  n = fluxk_kiss_encode(s->out, (unsigned)sizeof s->out, FLUXK_CMD_SETHW,
                        d, dn);
  if (n)
    api->serial_write(s->out, (uint16_t)n);
}

/* SET_HARDWARE request dispatch. */
static void fluxtnc_sethw(const k6_api_t *api, fluxtnc_t *s,
                          const uint8_t *p, unsigned n, int afsk_ok) {
  unsigned op = 0, alen = 0, st;
  uint8_t body[2 + 2 + 1 + 12];
  unsigned blen = 0;
  if (n >= 4 && p[0] == (uint8_t)'F' && p[1] == (uint8_t)'X')
    op = (unsigned)p[3] & 0x7Fu;   /* best-effort echo for the reply */
  st = fluxk_fx_parse(p, n, &op, &alen);
  if (!st)
    st = fluxk_sethw_validate(op, alen);
  if (!st) {
    const uint8_t *args = p + 4;
    if (op == FLUXK_OP_LIST) {
      /* One frame per advertised mode, then the 0xFFFF terminator. */
      unsigned i;
      uint8_t e[2 + 1 + 2 + 1 + 12];
      if (afsk_ok) {
        for (i = 0; i < fluxk_n_backends(); i++) {
          const fluxk_backend_t *b = &fluxk_backends[i];
          unsigned el = fluxk_list_body(e, b->mode_id, b->flags,
                                        b->max_payload, b->name, b->name_len);
          fluxtnc_send(api, s, op, FLUXK_OK, e, el);
        }
      }
      blen = fluxk_list_body(e, 0xFFFFu, 0, 0, 0, 0);
      fluxtnc_send(api, s, op, FLUXK_OK, e, blen);
      return;
    }
    if (op == FLUXK_OP_GET) {
      blen = fluxk_put_u16le(body, s->cur_mode);
    } else if (op == FLUXK_OP_SET) {
      uint16_t id =
          (uint16_t)((unsigned)args[0] | ((unsigned)args[1] << 8));
      const fluxk_backend_t *b = fluxk_backend_find(id);
      if (!b)
        st = FLUXK_ERR_UNKNOWN;
      else if (!afsk_ok)
        st = FLUXK_ERR_UNSUPPORTED;
      else {
        s->cur_mode = id;          /* selection only: never transmits */
        blen = fluxk_put_u16le(body, id);
      }
    } else if (op == FLUXK_OP_CAPS) {
      blen = fluxk_put_u16le(body, 0);   /* max_rx: no native receive */
      blen += fluxk_put_u16le(body + blen, afsk_ok ? FLUXK_MAX_PAYLOAD : 0);
    } else {                       /* STATUS */
      blen = fluxk_put_u16le(body, s->tx_ok);
      blen += fluxk_put_u16le(body + blen, s->tx_fail);
    }
  }
  fluxtnc_send(api, s, op, st, body, blen);
}

/* DATA dispatch: encode + guarded TX chain, or drop without keying. */
static void fluxtnc_data(const k6_api_t *api, fluxtnc_t *s,
                         const uint8_t *p, unsigned n, int afsk_ok) {
  uint32_t hz;
  if (!fluxk_data_tx_ok(s->cur_mode, n, afsk_ok))
    return;                        /* malformed / unselected: never key */
  if (!ap_tx_encode(&s->enc, p, n) || s->enc.bits > 1024) {
    fluxk_sat_inc(&s->tx_fail);
    return;
  }
  hz = api->rx_hz();               /* TX on the current frequency */
  if (!fluxk_hz_ok(hz)) {
    fluxk_sat_inc(&s->tx_fail);
    return;
  }
  /* Two separated samples above the observed B-chip idle floor, as in
     aprsbeacon.c. This coarse carrier check is NOT a full CSMA scheme. */
  api->delay_us(200000);
  if (KM_RSSI_DBM(api->fd_read(0x67)) > FLUXK_BUSY_DBM) {
    fluxk_sat_inc(&s->tx_fail);
    return;
  }
  api->delay_us(100000);
  if (KM_RSSI_DBM(api->fd_read(0x67)) > FLUXK_BUSY_DBM) {
    fluxk_sat_inc(&s->tx_fail);
    return;
  }
  if (api->tx_on(3000) != 0) {
    fluxk_sat_inc(&s->tx_fail);
    return;
  }
  s->keyed = 1;
  if (api->afsk_tx_send(s->enc.data, s->enc.bits))
    fluxk_sat_inc(&s->tx_fail);
  else
    fluxk_sat_inc(&s->tx_ok);
  api->tx_off();
  s->keyed = 0;
}

int k6_main(const k6_api_t *api) {
  uint32_t room, t0;
  unsigned i;
  int r = 0, stopped = 0;
  fluxtnc_t *s;
  int afsk_ok;
  if (api->level < 14 || !api->kiss_mode || !api->serial_read ||
      !api->serial_write)
    return -3;
  s = (fluxtnc_t *)api->scratch(&room);
  if (!s || room < (uint32_t)sizeof *s)
    return -8;
  fluxtnc_zero(s);
  /* afsk_tx_send is level 13: present when level >= 14, but check the
     pointer anyway so a non-B image degrades to control-plane only. */
  afsk_ok = api->level >= 13 && api->afsk_tx_send != 0;
  if (api->kiss_mode(1) != 0) {
    api->say("flux tnc: KISS mode refused");
    return -4;
  }
  s->kiss_on = 1;
  api->say("flux tnc: KISS TNC up");
  t0 = api->ms();
  for (;;) {
    if (km_power_switch_off()) {
      r = -2;
      break;
    }
    if (api->level >= 4 ? api->key_poll() : api->key_down()) {
      r = -2;
      stopped = 1;
      break;
    }
    if (api->ms() - t0 >= FLUXTNC_RUN_MS)
      break;
    for (i = 0; i < FLUXTNC_DRAIN; i++) {
      int c = api->serial_read();
      if (c < 0)
        break;
      {
        unsigned type = 0, n = 0;
        int fr = fluxk_rx_feed(&s->kiss, (uint8_t)(c & 255), &type,
                              s->pay, &n);
        if (fr == FLUXK_KISS_FRAME) {
          unsigned port = (type >> 4) & 15u, cmd = type & 15u;
          if (port == 0 && cmd == FLUXK_CMD_DATA)
            fluxtnc_data(api, s, s->pay, n, afsk_ok);
          else if (port == 0 && cmd == FLUXK_CMD_SETHW)
            fluxtnc_sethw(api, s, s->pay, n, afsk_ok);
          /* Other ports/commands: ignored, never key. */
        }
      }
    }
    api->delay_us(5000);
  }
  if (s->keyed)                    /* never leave the TX keyed */
    api->tx_off();
  if (s->kiss_on) {
    api->kiss_mode(0);
    s->kiss_on = 0;
  }
  if (!stopped)
    api->say("flux tnc: down");
  return r;
}
