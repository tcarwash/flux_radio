/* Flux MICVOICE laboratory recorder, adapted from upstream mickrec (MIT).
   FD6818B 0x4829 support is unverified. Default build is INERT.
   Explicit lab variant requires a dummy load and physical cable removal,
   requests guarded TX, disables PA before enabling microphone audio,
   records ~1 second as IMA ADPCM, and dumps results after cable reconnect.
   No automatic transmission or streaming. SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#ifndef FLUX_MICVOICE_LAB
#define FLUX_MICVOICE_LAB 0
#endif
#if !FLUX_MICVOICE_LAB
int k6_main(const k6_api_t *api) {
  if(api->abi != 1 || api->level < 13) return -3;
  api->say("MICVOICE LAB DISABLED");
  return -9;
}
#else
#include "micadc.h"
#include "pa11adc.h"
#include "cableout.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#ifndef PRE
#define PRE 1100u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef MAX_BYTES
#define MAX_BYTES 4096u
#endif
#ifndef HOLD_MS
#define HOLD_MS 5000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 300000u
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u
#endif
#ifndef SEL
#define SEL 4u
#endif
#define RSSI_ON -80 /* dBm: the sound's carrier is sent weak */
#define HOLD 3u
#define W 200u
#define AF_DAC 0x0200u
#define TX_MS 12000u

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hexn(char *p, uint32_t v, int n) {
  const char *x = "0123456789abcdef";
  int i;
  for (i = 4 * (n - 1); i >= 0; i -= 4) *p++ = x[(v >> i) & 15];
  return p;
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

/* IMA ADPCM, the standard coder: 4 bits a sample */
typedef struct {
  int32_t pred, index;
} ima_t;

static uint32_t ima_step(int32_t index) {
  static const uint16_t STEPS[89] = {
      7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,   28,
      31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,  118,
      130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,  494,
      544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878, 2066,
      2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845, 8630,
      9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
  return STEPS[index];
}

static uint32_t ima_code(ima_t *s, int32_t x) {
  int32_t step = (int32_t)ima_step(s->index), d = x - s->pred, q = step >> 3;
  uint32_t c = 0;
  if (d < 0) {
    c = 8;
    d = -d;
  }
  if (d >= step) {
    c |= 4;
    d -= step;
    q += step;
  }
  if (d >= step >> 1) {
    c |= 2;
    d -= step >> 1;
    q += step >> 1;
  }
  if (d >= step >> 2) {
    c |= 1;
    q += step >> 2;
  }
  s->pred += c & 8 ? -q : q;
  if (s->pred > 32767) s->pred = 32767;
  if (s->pred < -32768) s->pred = -32768;
  s->index += (c & 4) ? (int32_t)(2 * (c & 3)) + 2 : -1;
  if (s->index < 0) s->index = 0;
  if (s->index > 88) s->index = 88;
  return c;
}

typedef struct {
  uint32_t ticks, n, rest, over, s30, now;
} rec_t;

/* PA disabled for microphone recording into rec (n samples). No CW ID or deliberate RF keying.
   TX audio route may still momentarily energize RF; dummy load remains mandatory. */
static int record(const k6_api_t *api, uint32_t m_in, uint16_t r47, uint16_t r48, uint8_t *rec, rec_t *o) {
  pa11_t c = {0, 0, 0, 0};
  ima_t ima = {0, 0};
  int32_t mean = 0;
  uint32_t i, j, t0 = 0, want;
  int e;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
  if (api->tx_on(TX_MS) != 0) return -3;
  /* Immediately request PA off. No intentional RF identification/keying. */
  e = api->tx_pa(0) < 0 ? -5 : 0;
  if (!e) {
    o->s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA is still on: no mic bit */
  }
  want = (o->s30 | AF_DAC | MIC_ADC) & 0xFFFFu;
  if (!e && api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
  if (!e && (o->now = api->fd_read(0x30)) != want) e = -5;
  if (!e) {
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
    /* REG_48 gain layout on 0x4829 not yet established: leave it alone. */
    api->delay_us(20000);
  }
  for (i = 0; i < PRE + o->n && !e; i++) {
    int32_t y = (int32_t)pa11_sample(m_in, HOLD, W, &c) << 5, x;
    uint32_t code;
    if (!(i & 511u) && (api->fd_read(0x36) & 0x0080u)) e = -5;
    if (!(i & 511u) && api->key_poll()) e = -6;
    if (i < 3) mean = y; /* (the first two samples are not yet differences) */
    mean += (y - mean) >> 5;
    if (i < PRE) continue;
    if (i == PRE) {
      t0 = api->ticks();
      o->rest = (uint32_t)mean >> 5;
    }
    x = (y - mean) >> 2; /* 8 times the sample's distance from the running mean */
    if (x > 32767 || x < -32768) {
      x = x < 0 ? -32768 : 32767;
      o->over++;
    }
    code = ima_code(&ima, x);
    j = i - PRE;
    if (j & 1u) {
      rec[j >> 1] |= (uint8_t)(code << 4);
    } else {
      rec[j >> 1] = (uint8_t)code;
    }
  }
  o->ticks = (api->ticks() - t0) & 0xFFFFu;
  if (o->s30 && api->fd_read(0x30) != o->s30 && api->fd_write(0x30, (uint16_t)o->s30) != 0)
    mic_bus_write(api, 0x30, o->s30);
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->tx_off();
  return e;
}

int k6_main(const k6_api_t *api) {
  /* Prove ABI and chip identity before calling any expanded API callback or
     touching the PA11 / speaker / RF state. */
  if (api->abi != 1 || api->level < 13 || api->fd_read(0) != 0x4829u) return -1;
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           m_in = moder & ~(3u << (2 * PA11_PIN)), room = 0, bytes, i, j;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  char *line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *p;
  uint8_t *rec = (uint8_t *)line + 64;
  rec_t o;
  int ret = 0, e = 0;
  o.ticks=0; o.n=0; o.rest=0; o.over=0; o.s30=0; o.now=0;
  if (room < 1024) return -8;
  bytes = room - 68;
  if (bytes > MAX_BYTES) bytes = MAX_BYTES;
  bytes -= bytes % 26u;
  o.n = 2 * bytes;
  p = hexn(put(line, "mickrec start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 48="), r48, 4);
  p = hexn(put(p, " rx="), home, 8);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  co_show(api, "MICVOICE LAB", "PULL CABLE", "THEN ANY KEY");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  co_show(api, "MICVOICE LAB", "TALK, THEN PRESS", "ANY KEY TO RECORD");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  co_show(api, "MICVOICE LAB", "RECORDING NOW", "SPEAK CONTINUOUSLY");
  e = record(api, m_in, r47, r48, rec, &o);
  ret = e ? e : (int)o.n;
out:
  api->tx_off();
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  if (ret != -7) {
    co_show(api, "MICVOICE LAB", "PLUG CABLE IN", "THEN ANY KEY");
    co_key(api, KEY_WAIT_MS);
    co_show(api, "MICVOICE LAB", "SENDING", "");
    api->delay_us(1000000);
    p = hexn(put(line, "mickrec keyed 30="), o.s30, 4);
    p = hexn(put(p, " 36="), api->fd_read(0x36), 4);
    p = hexn(put(p, " now="), o.now, 4);
    *p = 0;
    api->say(line);
  }
  if (ret > 0) {
    for (i = 0; i < bytes; i += 26) {
      p = hexn(put(line, "p "), i, 4);
      *p++ = ' ';
      for (j = i; j < i + 26; j++) p = hexn(p, rec[j], 2);
      *p = 0;
      api->say(line);
      api->delay_us(15000); /* the serial port drains */
    }
    p = hexn(put(line, "micrec p ticks="), o.ticks, 4);
    p = hexn(put(p, " n="), o.n, 4);
    p = hexn(put(p, " w="), W, 2);
    p = hexn(put(p, " rest="), o.rest, 4);
    p = hexn(put(p, " over="), o.over, 4);
    *p = 0;
    api->say(line);
  }
  api->say("mickrec done");
  return ret;
}
#endif /* FLUX_MICVOICE_LAB */
