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
  uint8_t stop_requested; /* host stop acknowledged before leaving raw UART */
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

/* The API26 callback is NEW and is not wire-compatible with all older
   experimental API26+ images. Generic Data-pack bundles compile it OUT.
   Enable only for a TNC built and distributed together with a verified
   K6_MODE_REGISTRY firmware image. Exact level==26 avoids accidental
   invocation of unrelated extension slots in existing API29 radios. */
#if defined(FLUXTNC_ENABLE_HOST_MODE_REGISTRY) && FLUXTNC_ENABLE_HOST_MODE_REGISTRY == 1
#define FLUXTNC_CAN_DISCOVER(api) ((api)->level == 26 && (api)->mode_info != 0)
#else
#define FLUXTNC_CAN_DISCOVER(api) 0
#endif

/* Host-owned mailbox staging (c/k6/mod/k6_packet_mailbox.h). Compiled OUT
   unless FLUXTNC_ENABLE_PACKET_MAILBOX=1 AND the host is exactly API 27
   with mb_version == K6MB_VERSION (1) and a non-NULL stage_packet: never
   an arbitrary API29 tail slot on an old physical build. Staging only
   COPIES bytes to host storage; the TNC keys nothing on this path. */
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
#include "k6_packet_mailbox.h"
#define FLUXTNC_CAN_MAILBOX(api) \
  ((api)->level == 27 && (api)->mb_version == (uint32_t)K6MB_VERSION && \
   (api)->stage_packet != 0)
#else
#define FLUXTNC_CAN_MAILBOX(api) 0
#endif

/* API26 optional installed-provider registry. The firmware scans metadata
   without launching a second module or touching the TNC code arena.
   Only known Sotto *voice-only* entries may be selected. 1 found, 0
   absent, -1 malformed or SPI busy. */
static int fluxtnc_provider_find(const k6_api_t *api, uint16_t wanted,
                                 k6_mode_info_t *out) {
  unsigned i;
  if (!FLUXTNC_CAN_DISCOVER(api))
    return 0;
  for (i = 0; i < 32; i++) {
    int r = api->mode_info((uint32_t)i, out);
    if (r < 0)
      return -1;
    if (r == 0)
      return 0;
    if (!fluxk_provider_valid(out->mode_id, out->flags, out->max_payload,
                              out->name_len, out->name))
      return -1;
    if (out->mode_id == wanted)
      return 1;
  }
  return -1; /* unbounded or corrupted enumerator: fail closed */
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
      if (FLUXTNC_CAN_DISCOVER(api)) {
        k6_mode_info_t info;
        unsigned seen = 0;
        for (i = 0; i < 32; i++) {
          int r = api->mode_info((uint32_t)i, &info);
          unsigned bit;
          if (r < 0) {
            fluxtnc_send(api, s, op, FLUXK_ERR_BUSY, 0, 0);
            return;
          }
          if (r == 0)
            break;
          if (!fluxk_provider_valid(info.mode_id, info.flags,
                                    info.max_payload, info.name_len, info.name)) {
            fluxtnc_send(api, s, op, FLUXK_ERR_UNSUPPORTED, 0, 0);
            return;
          }
          bit = (info.mode_id == 0x0100u) ? 1u : 2u;
          if (seen & bit)
            continue; /* duplicate installed providers: one mode per identity */
          seen |= bit;
          blen = fluxk_list_body(e, info.mode_id, info.flags,
                                 info.max_payload, info.name, info.name_len);
          fluxtnc_send(api, s, op, FLUXK_OK, e, blen);
        }
      }
      blen = fluxk_list_body(e, 0xFFFFu, 0, 0, 0, 0);
      fluxtnc_send(api, s, op, FLUXK_OK, e, blen);
      return;
    }
    if (op == FLUXK_OP_STOP) {
      s->stop_requested = 1; /* no RF, orderly exit in main loop */
    } else if (op == FLUXK_OP_GET) {
      blen = fluxk_put_u16le(body, s->cur_mode);
    } else if (op == FLUXK_OP_SET) {
      uint16_t id =
          (uint16_t)((unsigned)args[0] | ((unsigned)args[1] << 8));
      const fluxk_backend_t *b = fluxk_backend_find(id);
      if (b) {
        if (!afsk_ok)
          st = FLUXK_ERR_UNSUPPORTED;
        else {
          s->cur_mode = id;          /* selection only: never transmits */
          blen = fluxk_put_u16le(body, id);
        }
      } else {
        k6_mode_info_t info;
        int found = fluxtnc_provider_find(api, id, &info);
        if (found < 0)
          st = FLUXK_ERR_BUSY;
        else if (!found) {
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
          if (FLUXTNC_CAN_MAILBOX(api) && id != 0 && id != 0xFFFFu) {
            /* Provider-selected, pending host verification: selection
               alone never transmits. DATA below stages the frame into
               the host mailbox (no keying here); the provider claim
               enforces identity/capability and refuses voice-only or
               unknown providers without RF. */
            s->cur_mode = id;
            blen = fluxk_put_u16le(body, id);
          } else
            st = FLUXK_ERR_UNKNOWN;
#else
          st = FLUXK_ERR_UNKNOWN;
#endif
        } else {
          s->cur_mode = id;          /* voice-only; DATA below must refuse */
          blen = fluxk_put_u16le(body, id);
        }
      }
    } else if (op == FLUXK_OP_CAPS) {
      blen = fluxk_put_u16le(body, 0);   /* max_rx: no native receive */
      blen += fluxk_put_u16le(body + blen,
                             (afsk_ok && s->cur_mode == FLUXK_MODE_APRS1200) ?
                               FLUXK_MAX_PAYLOAD : 0);
    } else {                       /* STATUS */
      blen = fluxk_put_u16le(body, s->tx_ok);
      blen += fluxk_put_u16le(body + blen, s->tx_fail);
    }
  }
  fluxtnc_send(api, s, op, st, body, blen);
}

/* DATA dispatch: encode + guarded TX chain, or drop without keying.
   Returns 1 when exactly one packet was staged into the host mailbox: the
   caller must then release the UART/foreground and return K6MB_TNC_STAGED
   so the host can dispatch a provider after this module returns (never
   nested). Returns 0 otherwise (sent natively, refused, or dropped). */
static int fluxtnc_data(const k6_api_t *api, fluxtnc_t *s,
                        const uint8_t *p, unsigned n, int afsk_ok) {
  uint32_t hz;
  if (!fluxk_data_tx_ok(s->cur_mode, n, afsk_ok)) {
    uint8_t mode[2];
    unsigned st = n > FLUXK_MAX_PAYLOAD ? FLUXK_ERR_TOO_LONG :
                  n < FLUXK_MIN_APRS ? FLUXK_ERR_MALFORMED :
                  FLUXK_ERR_UNSUPPORTED;
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
    /* Non-native selected mode with an exact level-27 mailbox host: stage
       for the host dispatcher instead of keying here. Known voice-only
       entries (Sotto) stay refused; the provider claim re-verifies
       capability, so a voice-only or unknown id can never reach RF. */
    if (st == FLUXK_ERR_UNSUPPORTED && FLUXTNC_CAN_MAILBOX(api)) {
      k6_mode_info_t voice;
      int known_voice = fluxtnc_provider_find(api, s->cur_mode, &voice);
      if (known_voice <= 0 && n <= K6MB_MAX_PAYLOAD) {
        if (api->stage_packet(s->cur_mode, p, (uint16_t)n) == 0)
          return 1;
        st = FLUXK_ERR_BUSY;   /* host could not take it: fail closed */
      }
    }
#endif
    fluxk_sat_inc(&s->tx_fail);
    fluxk_put_u16le(mode, s->cur_mode);
    /* Unsolicited Flux command-0 refusal: standard KISS applications
       ignore vendor command-6 replies, while Flux clients get an
       explicit no-RF result for unsupported voice-only modes. */
    fluxtnc_send(api, s, 0, st, mode, 2);
    return 0;
  }
  if (!ap_tx_encode(&s->enc, p, n) || s->enc.bits > 1024) {
    fluxk_sat_inc(&s->tx_fail);
    return 0;
  }
  hz = api->rx_hz();               /* TX on the current frequency */
  if (!fluxk_hz_ok(hz)) {
    fluxk_sat_inc(&s->tx_fail);
    return 0;
  }
  /* Two separated samples above the observed B-chip idle floor, as in
     aprsbeacon.c. This coarse carrier check is NOT a full CSMA scheme. */
  api->delay_us(200000);
  if (KM_RSSI_DBM(api->fd_read(0x67)) > FLUXK_BUSY_DBM) {
    fluxk_sat_inc(&s->tx_fail);
    return 0;
  }
  api->delay_us(100000);
  if (KM_RSSI_DBM(api->fd_read(0x67)) > FLUXK_BUSY_DBM) {
    fluxk_sat_inc(&s->tx_fail);
    return 0;
  }
  if (api->tx_on(3000) != 0) {
    fluxk_sat_inc(&s->tx_fail);
    return 0;
  }
  s->keyed = 1;
  if (api->afsk_tx_send(s->enc.data, s->enc.bits))
    fluxk_sat_inc(&s->tx_fail);
  else
    fluxk_sat_inc(&s->tx_ok);
  api->tx_off();
  s->keyed = 0;
  return 0;
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
          if (port == 0 && cmd == FLUXK_CMD_DATA) {
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
            /* Staged: release the UART/foreground now. The host runs the
               provider entry action after k6_main returns (never nested),
               then resumes the TNC session and reports completion. s is
               scratch/arena memory the host must NOT touch after return:
               the staged bytes already live in host storage. */
            if (fluxtnc_data(api, s, s->pay, n, afsk_ok)) {
              r = K6MB_TNC_STAGED;
              stopped = 1;   /* staged: outer loop must exit, no "down" */
              break;         /* out of the drain loop; checked below */
            }
#else
            fluxtnc_data(api, s, s->pay, n, afsk_ok);
#endif
          } else if (port == 0 && cmd == FLUXK_CMD_SETHW) {
            fluxtnc_sethw(api, s, s->pay, n, afsk_ok);
            if (s->stop_requested)
              break; /* never consume a queued DATA frame after STOP */
          }
          /* Other ports/commands: ignored, never key. */
        }
      }
    }
    if (s->stop_requested) {
      /* Let the bounded KISS UART ACK drain before restoring CPS mode. */
      api->delay_us(50000);
      break;
    }
    api->delay_us(5000);
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
    if (r == K6MB_TNC_STAGED)
      break;                       /* staged: release foreground now */
  }
#else
  }
#endif
  if (s->keyed)                    /* never leave the TX keyed */
    api->tx_off();
  if (s->kiss_on) {
    api->kiss_mode(0);
    s->kiss_on = 0;
  }
#if defined(FLUXTNC_ENABLE_PACKET_MAILBOX) && FLUXTNC_ENABLE_PACKET_MAILBOX == 1
  if (r == K6MB_TNC_STAGED)
    api->say("flux tnc: packet staged");
  else if (!stopped)
    api->say("flux tnc: down");
#else
  if (!stopped)
    api->say("flux tnc: down");
#endif
  return r;
}
