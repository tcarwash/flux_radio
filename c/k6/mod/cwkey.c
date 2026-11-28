/* cwkey: the K6 sends its own CW ID once, as true CW: the carrier keyed with the PA while
   the PLL stays locked (module API level 3, tx_on / tx_pa / tx_off).

   Jeff asked for it on 2026-11-06 ("can you do it once from the HT?"). It keys up only when
   the pager listens on CW_HZ (440.850 MHz, the bench frequency) and the channel has been clear
   for CW_CLEAR_MS (the upload's sender has unkeyed), sends CW_TEXT ("AG7EW") at
   CW_WPM, and keys down. The core's own guards stay in force: the pager's frequency only, the
   TX band lock, low power, at most 20 s, key-down when the module returns.

   With the PA off between elements the carrier is gone, so a receiver hears it as CW in CW or
   SSB mode; in FM it only hears the squelch open and close with the keying.

   It says
     K6 MOD say cw key AG7EW 20wpm rx=440850000
     K6 MOD say cw sent units=52 ms=3420
   and returns the units sent, or a negative error:
     -1  the image has no level-3 transmitter      -2  not on CW_HZ
     -3  tx_on refused                             -4  a key was pressed (keyed down at once)
     -5  tx_pa failed partway (the core keyed down: time up)
     -6  a character with no Morse code in CW_TEXT   -7  the channel stayed busy (CW_WAIT_MS)

   Build: python -m modem.k6 mod out.wav c/k6/mod/cwkey.c --api-level 3 --key-file KEY
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "k6ui.h"
#ifndef K6_NAME
#define K6_NAME "CW KEY"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef CW_TEXT
#define CW_TEXT "AG7EW"
#endif
#ifndef CW_WPM
#define CW_WPM 20
#endif
#ifndef CW_HZ
#define CW_HZ 440850000u
#endif
#ifndef CW_LEAD_MS
#define CW_LEAD_MS 300       /* key-up with the PA off before the first element: PLL settles */
#endif
#ifndef CW_CLEAR_DBM
#define CW_CLEAR_DBM -90     /* the channel counts as clear below this (the upload's sender gone) */
#endif
#ifndef CW_CLEAR_MS
#define CW_CLEAR_MS 1000     /* ... for this long, before it keys up */
#endif
#ifndef CW_WAIT_MS
#define CW_WAIT_MS 30000     /* give up (-7) if the channel isn't clear by then */
#endif
#define UNIT_US (1200000u / CW_WPM)

/* Morse by code, as cwid.c: start at 1, then 0 for a dot and 1 for a dash (E = 2, A = 5) */
static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";

static int code_of(char c) {
  int i;
  if (c >= 'a' && c <= 'z') c -= 32;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c && c != '~') return i;
  return 0;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {     /* no divide on the M0: subtract powers of ten */
  static const uint32_t pw[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                10000u, 1000u, 100u, 10u, 1u};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    char d = '0';
    while (v >= pw[i]) { v -= pw[i]; d++; }
    if (d != '0' || started || i == 9) { *p++ = d; started = 1; }
  }
  return p;
}

/* Short, interruptible delays instead of one 60 ms dit or 300 ms lead.
   The core's TX watchdog remains authoritative even if key polling stalls. */
#define CW_POLL_US 10000u
static int wait_keyable(const k6_api_t *api, uint32_t total_us) {
  while (total_us) {
    uint32_t step = total_us > CW_POLL_US ? CW_POLL_US : total_us;
    if (api->key_down()) return -4;  /* never knowingly re-enable a canceled PA */
    api->delay_us(step);
    total_us -= step;
  }
  return api->key_down() ? -4 : 0;
}

/* Hold the PA on or off for n Morse units, but check cancellation *before*
   every PA transition. TX-on is still guarded by the host TX band/timeout. */
static int hold(const k6_api_t *api, int on, int n) {
  if (api->key_down()) return -4;
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    int r = wait_keyable(api, UNIT_US);
    if (r) return r;
  }
  return 0;
}

static int tx_main(const k6_api_t *api, ui_t *ku) {
  const char *s;
  char line[64], *p;
  int units = 0, need = 0, r = 0;
  uint32_t t0;
  if (api->level < 3) return -1;
  /* The diagnostic line is only 64 bytes. Reject a too-long compile-time
     callsign/text rather than constructing an overlong on-radio message.
     Reserve 36 bytes for 'cw key ', WPM, 'wpm rx=', a u32 and NUL. */
  if (sizeof(CW_TEXT) > sizeof line - 35u) return -6;
  for (s = CW_TEXT; *s; s++) {          /* check the text and count its units */
    int c = code_of(*s), b;
    if (*s == ' ') { need += 4; continue; }
    if (!c) return -6;
    for (b = 31; !(c >> b); b--) {}
    while (b--) need += ((c >> b) & 1 ? 3 : 1) + (b ? 1 : 3);
  }
  p = put(line, "cw key " CW_TEXT " ");
  p = dec(p, CW_WPM);
  p = put(p, "wpm rx=");
  p = dec(p, api->rx_hz());
  *p = 0;
  api->say(line);
  if (api->rx_hz() != CW_HZ) return -2;
  /* the module runs as its last chunk lands, while the sender may still be keyed (its own CW
     ID follows the upload): wait for the channel to stay clear */
  {
    uint32_t start = api->ms(), clear = start;
    for (;;) {
      uint32_t now = api->ms();
      if ((uint32_t)(now - start) >= CW_WAIT_MS) return -7;
      if (KM_RSSI_DBM(api->fd_read(0x67)) >= CW_CLEAR_DBM) clear = now;
      if (api->key_down()) return -4;
      if ((uint32_t)(now - clear) >= CW_CLEAR_MS) break;
      api->delay_us(CW_POLL_US);
    }
  }
  if (api->key_down()) return -4;
  /* Round *up* each whole Morse unit when budgeting the host's watchdog;
     never truncate the fractional milliseconds at a nondivisor WPM. */
  ui_text(ku, 1, "ON THE AIR");
  ui_now(ku);
  if (api->tx_on(CW_LEAD_MS + (uint32_t)need * ((UNIT_US + 999u) / 1000u) + 1000u) != 0) return -3;
  /* tx_on can assert PA momentarily. Command PA-off *immediately* after
     tx_on rather than spending another ms() read while RF is enabled. */
  if (api->tx_pa(0) < 0) r = -5;
  t0 = api->ms();
  if (!r) r = wait_keyable(api, CW_LEAD_MS * 1000u);
  for (s = CW_TEXT; *s && !r; s++) {
    int c, b;
    if (*s == ' ') {                     /* a word gap: 7 units, 3 already after the last char */
      r = hold(api, 0, 4);
      units += 4;
      continue;
    }
    c = code_of(*s);
    for (b = 31; !(c >> b); b--) {}      /* the start bit */
    while (b--) {
      int n = (c >> b) & 1 ? 3 : 1;
      if ((r = hold(api, 1, n)) != 0) break;
      if ((r = hold(api, 0, b ? 1 : 3)) != 0) break;
      units += n + (b ? 1 : 3);
    }
  }
  /* Always request PA-off and TX-off on *every* post-tx_on exit, even
     after cancellation, failed PA programming or expired watchdog. */
  if (api->tx_pa(0) < 0 && !r) r = -5;
  api->tx_off();
  if (r) return r;
  p = put(line, "cw sent units=");
  p = dec(p, (uint32_t)units);
  p = put(p, " ms=");
  p = dec(p, api->ms() - t0);
  *p = 0;
  api->say(line);
  return units;
}

/* the screen: what it sends, then how it went (k6ui.h; images before level 6 skip it) */
int k6_main(const k6_api_t *api) {
  ui_t u;
  int r;
  ui_begin(&u, api, "CW KEY 440.850", "WAITING FOR QUIET");
  ui_text(&u, 2, CW_TEXT);
  ui_text(&u, 5, "ANY KEY: STOP");
  ui_now(&u);
  r = tx_main(api, &u);
  ui_tx_end(&u, r, CW_HZ, "SENT " CW_TEXT " IN CW");
  return r;
}
