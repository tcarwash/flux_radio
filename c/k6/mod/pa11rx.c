/* pa11rx: a bench-ready receive-only PA11 audio-capture diagnostic for the Flux FD6818B
   (chip 0x4829). While the radio is already in RX, it listens to the existing AF output
   through PA11 run as a one-pin delta-sigma converter (pa11adc.h) and reports, in 60-byte
   bounded serial lines via api->say: the sample count, mean, variance, min/max, elapsed time
   and a rough tone hint (zero crossings plus the sign of the lag-1 correlation). No
   continuous UART pipeline yet: this is a feasibility probe for future digital modems, not a
   modem or a firmware release (docs/k6-pa11-rx-probe.md).

   Read-only by design: it never calls tx_on/tx_off/tx_pa, never writes an FD6818 register
   (REG_47/48 are read for the report only), never touches calibration, SPI flash, the module
   ABI, or RF state. The 0x4819 AF-output select map is NOT assumed on the 0x4829: the AF
   select is left exactly as found, which the report states as a limitation. It saves and
   restores GPIOA's PA11 mode/speed/output latch and the PB2 speaker amplifier on every exit,
   including key-abort and timeout. Returns the samples taken (N), -2 on a key, -3 on an old
   image, -5 on timeout. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"
#include "pa11adc.h"

#define GPIOA_MODER 0x48000000u
#define GPIOA_OSPEEDR 0x48000008u
#define GPIOA_ODR 0x48000014u
#define GPIOB_ODR 0x48000414u
#define GPIOB_BSRR 0x48000418u
#define GPIOB_BRR 0x48000428u
#define SPK (1u << 2)

#ifndef PA11RX_N
#define PA11RX_N 32u
#endif
#if PA11RX_N != 32u
#error "pa11rx needs exactly 32 samples for its bounded integer statistics"
#endif
#ifndef PA11RX_HOLD
#define PA11RX_HOLD 3u
#endif
#ifndef PA11RX_W
#define PA11RX_W 90u
#endif
#ifndef PA11RX_TIMEOUT_MS
#define PA11RX_TIMEOUT_MS 15000u
#endif

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {
  static const uint32_t pw[] = {1000000000, 100000000, 10000000, 1000000, 100000,
                                10000,      1000,      100,      10,      1};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    int d = 0;
    while (v >= pw[i]) v -= pw[i], d++;
    if (d || started || i == 9) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}

static char *sdec(char *p, int32_t v) {
  uint32_t u;
  if (v < 0) *p++ = '-', v = -v;
  u = (uint32_t)v;
  return dec(p, u);
}

int k6_main(const k6_api_t *api) {
  char line[64], *p;
  uint32_t moder, ospeed, odr, spk, m_in, t0, t1, i, n = 0;
  uint32_t sum = 0, mean, var = 0, lo = 0xFFFFFFFFu, hi = 0, zc = 0, lagp = 0;
  int32_t lagn = 0, prev = 0;
  uint16_t r47, r48;
  pa11_t c;
  uint32_t buf[PA11RX_N];
  int ret;

  if (api->abi != 1 || api->level < 2) return -3;
  moder = rd(GPIOA_MODER);
  ospeed = rd(GPIOA_OSPEEDR);
  odr = rd(GPIOA_ODR);
  spk = rd(GPIOB_ODR) & SPK;
  m_in = moder & ~(3u << (2 * PA11_PIN));
  r47 = api->fd_read(0x47);
  r48 = api->fd_read(0x48);

  p = put(line, "pa11rx start n=");
  p = dec(p, PA11RX_N);
  p = put(p, " h=");
  p = dec(p, PA11RX_HOLD);
  p = put(p, " w=");
  p = dec(p, PA11RX_W);
  *p = 0;
  api->say(line);
  p = put(line, "pa11rx r47=");
  p = dec(p, r47);
  p = put(p, " r48=");
  p = dec(p, r48);
  p = put(p, " af untouched");
  *p = 0;
  api->say(line);

  wr(GPIOA_OSPEEDR, ospeed | (3u << (2 * PA11_PIN))); /* fast drive: tens of ns */
  wr(GPIOB_BRR, SPK);                                /* amp off: the pulses reach it */

  c.i1 = 0, c.i2 = 0, c.z1 = 0, c.z2 = 0;
  (void)pa11_sample(m_in, PA11RX_HOLD, PA11RX_W, &c); /* not differences yet: drop */
  (void)pa11_sample(m_in, PA11RX_HOLD, PA11RX_W, &c);
  t0 = api->ms();
  ret = (int)PA11RX_N;
  for (i = 0; i < PA11RX_N; i++) {
    uint32_t s;
    if (api->key_down()) {
      ret = -2;
      break;
    }
    if (api->ms() - t0 > PA11RX_TIMEOUT_MS) {
      ret = -5;
      break;
    }
    s = pa11_sample(m_in, PA11RX_HOLD, PA11RX_W, &c);
    if (s > (uint32_t)PA11RX_W * (uint32_t)PA11RX_W) s = (uint32_t)PA11RX_W * (uint32_t)PA11RX_W;
    buf[i] = s;
    sum += s;
    if (s < lo) lo = s;
    if (s > hi) hi = s;
    n++;
  }
  t1 = api->ms();

  mean = sum >> 5; /* N is 32: no division needed on the M0 */
  for (i = 0; i < n; i++) {
    int32_t d = (int32_t)buf[i] - (int32_t)mean;
    uint32_t sq = (uint32_t)(d < 0 ? -d : d);
    var += sq * sq; /* at most N * W^4: fits 32 bits */
    if (i) {
      int32_t dp = (int32_t)buf[i - 1] - (int32_t)mean;
      int32_t a = d < 0 ? -d : d, b = dp < 0 ? -dp : dp;
      uint32_t m = (uint32_t)a * (uint32_t)b;
      lagn += (d < 0) == (dp < 0) ? (int32_t)m : -(int32_t)m;
      if ((d < 0) != (prev < 0)) zc++;
    }
    prev = d;
  }
  if (n > 1) lagp = lagn > 0 ? 1u : 0u;

  /* every exit below restores the pins first (out: pattern, as in afsweep/spksweep) */
  wr(GPIOA_ODR, odr);
  wr(GPIOA_MODER, moder);
  wr(GPIOA_OSPEEDR, ospeed);
  if (spk)
    wr(GPIOB_BSRR, SPK);
  else
    wr(GPIOB_BRR, SPK);

  if (ret == -2) {
    api->say("pa11rx key");
    return -2;
  }
  if (ret == -5) {
    api->say("pa11rx timeout");
    return -5;
  }
  p = put(line, "pa11rx n=");
  p = dec(p, n);
  p = put(p, " mean=");
  p = dec(p, mean);
  p = put(p, " var=");
  p = dec(p, var);
  *p = 0;
  api->say(line);
  p = put(line, "pa11rx lo=");
  p = dec(p, lo);
  p = put(p, " hi=");
  p = dec(p, hi);
  p = put(p, " zc=");
  p = dec(p, zc);
  p = put(p, " lag=");
  p = sdec(p, lagn);
  *p = 0;
  api->say(line);
  p = put(line, "pa11rx t=");
  p = dec(p, t1 - t0);
  p = put(p, "ms tonal=");
  p = dec(p, lagp);
  p = put(p, " done");
  *p = 0;
  api->say(line);
  return ret;
}
