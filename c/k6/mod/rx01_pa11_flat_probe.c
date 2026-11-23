/* rx01_pa11_flat_probe.c -- RX-01 (#24): RX-only PA11 delta-sigma + flat discriminator probe.
 *
 * Strictly RX-only (API level 2): never enables TX, PA, or FSK. Snapshots and
 * restores REG_2B, REG_47, GPIOA (MODER/OSPEEDR/PUPDR/ODR), and GPIOB PB2
 * (speaker amp) on both normal completion and key/power-switch abort.
 *
 * Inert by default: RX01_BENCH_ENABLE=0 compiles to a <256-byte stub returning -9
 * with zero register or GPIO writes.
 *
 * Measurements from the 0x4819 board (pa11adc.h) are NOT assumed to hold on
 * 0x4829: this probe reports raw CIC counts, AC mean-abs-dev, and first-diff
 * high-band energy across three short bursts:
 *   b=0: stock REG_2B, stock REG_47
 *   b=1: flat  REG_2B | 0x0700 (HPF/LPF/de-emphasis bypassed), stock REG_47
 *   b=2: flat  REG_2B | 0x0700, REG_47 AF select = 4 (unmuted)
 *
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include "../k6_mod.h"

#ifndef RX01_BENCH_ENABLE
#define RX01_BENCH_ENABLE 0
#endif

#ifndef RX01_HOLD
#define RX01_HOLD 3u
#endif

#ifndef RX01_W
#define RX01_W 90u
#endif

/* Power of two so mean/MAD division is a shift (no __aeabi_uidiv on Cortex-M0). */
#ifndef RX01_SAMPLES_LOG2
#define RX01_SAMPLES_LOG2 7u
#endif
#define RX01_SAMPLES (1u << RX01_SAMPLES_LOG2)

#if RX01_BENCH_ENABLE

#define GPIOA_BASE   0x48000000u
#define GPIOB_BASE   0x48000400u
#define GPIO_MODER   0x00u
#define GPIO_OSPEEDR 0x08u
#define GPIO_PUPDR   0x0Cu
#define GPIO_IDR     0x10u
#define GPIO_ODR     0x14u
#define GPIO_BSRR    0x18u

#define PA11_PIN     11u
#define PA15_PIN     15u
#define PB2_SPK_PIN  2u

#define REG_00_ID    0x00u
#define REG_2B_FILT  0x2Bu
#define REG_30_MAIN  0x30u
#define REG_36_PA    0x36u
#define REG_3F_IRQ   0x3Fu
#define REG_47_AF    0x47u
#define REG_58_FSK   0x58u

#define CHIP_4829    0x4829u
#define FLAT_2B_MASK 0x0700u

typedef struct {
  uint32_t i1, i2, z1, z2;
} pa11_cic_t;

static void __attribute__((noinline)) pa11_turns(uint32_t m_in, uint32_t hold,
                                                 uint32_t w, pa11_cic_t *c) {
  volatile uint32_t *g = (volatile uint32_t *)GPIOA_BASE;
  volatile uint32_t hv = hold;
  uint32_t i1 = c->i1, i2 = c->i2, h;
  uint32_t m_out = m_in | (1u << (2u * PA11_PIN));
  hold = hv;
  do {
    uint32_t b;
    g[GPIO_BSRR / 4u] = 1u << PA11_PIN;
    g[GPIO_MODER / 4u] = m_out;
    __asm volatile("nop\nnop\nnop\nnop");
    g[GPIO_MODER / 4u] = m_in;
    __asm volatile("nop\nnop\nnop\nnop\nnop\nnop\nnop\nnop");
    b = (g[GPIO_IDR / 4u] >> PA11_PIN) & 1u;
    g[GPIO_BSRR / 4u] = 1u << (PA11_PIN + 16u);
    g[GPIO_MODER / 4u] = m_in | (b << (2u * PA11_PIN));
    for (h = hold; h; h--) __asm volatile("nop");
    g[GPIO_MODER / 4u] = m_in;
    i1 += b;
    i2 += i1;
  } while (--w);
  c->i1 = i1;
  c->i2 = i2;
}

static uint32_t pa11_sample(uint32_t m_in, uint32_t hold, uint32_t w, pa11_cic_t *c) {
  uint32_t d, y;
  pa11_turns(m_in, hold, w, c);
  d = c->i2 - c->z1;
  c->z1 = c->i2;
  y = d - c->z2;
  c->z2 = d;
  return y;
}

static int aborted(const k6_api_t *api) {
  volatile uint32_t *ga = (volatile uint32_t *)GPIOA_BASE;
  if (api->key_down()) return 1;
  if ((ga[GPIO_IDR / 4u] >> PA15_PIN) & 1u) return 1;
  return 0;
}

static char *put_str(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *put_u32(char *p, uint32_t v) {
  char tmp[6];
  int n = 0;
  if (v > 65535u) v = 65535u;
  if (v == 0u) {
    *p++ = '0';
    return p;
  }
  while (v > 0u) {
    /* Exact for all v <= 65535: q = floor(v / 10) = (v * 52429) >> 19. */
    uint32_t q = (v * 52429u) >> 19;
    tmp[n++] = (char)('0' + (v - q * 10u));
    v = q;
  }
  while (n--) *p++ = tmp[n];
  return p;
}

static char *put_hex4(char *p, uint32_t v) {
  int shift;
  for (shift = 12; shift >= 0; shift -= 4) {
    uint32_t nib = (v >> shift) & 0xFu;
    *p++ = (char)(nib < 10u ? ('0' + nib) : ('a' + (nib - 10u)));
  }
  return p;
}

static uint32_t iabs_diff(uint32_t a, uint32_t b) {
  return (a >= b) ? (a - b) : (b - a);
}

#endif /* RX01_BENCH_ENABLE */

int k6_main(const k6_api_t *api) {
#if !RX01_BENCH_ENABLE
  (void)api;
  return -9;
#else
  volatile uint32_t *ga = (volatile uint32_t *)GPIOA_BASE;
  volatile uint32_t *gb = (volatile uint32_t *)GPIOB_BASE;
  uint16_t reg2b_orig, reg47_orig, r30, r36, r3f, r58;
  uint32_t ga_moder_orig, ga_ospeed_orig, ga_pupd_orig, ga_odr_orig;
  uint32_t pb2_orig;
  uint32_t m_in;
  uint16_t samples[RX01_SAMPLES];
  int rc = 0;
  uint32_t burst;

  if (!api || api->abi != 1u || api->level < 2u) return -1;
  if (api->fd_read(REG_00_ID) != CHIP_4829) return -6;

  /* Strictly RX-only: refuse if TX, PA, or FSK are active. */
  r30 = api->fd_read(REG_30_MAIN);
  r36 = api->fd_read(REG_36_PA);
  r3f = api->fd_read(REG_3F_IRQ);
  r58 = api->fd_read(REG_58_FSK);
  if ((r30 & 0x400Au) != 0u || (r36 & 0x00FFu) != 0u) return -2;
  if ((r58 & 0x0001u) != 0u || (r3f & 0xF000u) != 0u) return -8;

  /* Snapshot all touched BK4829 registers and MCU GPIOs. */
  reg2b_orig = api->fd_read(REG_2B_FILT);
  reg47_orig = api->fd_read(REG_47_AF);
  ga_moder_orig = ga[GPIO_MODER / 4u];
  ga_ospeed_orig = ga[GPIO_OSPEEDR / 4u];
  ga_pupd_orig = ga[GPIO_PUPDR / 4u];
  ga_odr_orig = ga[GPIO_ODR / 4u];
  pb2_orig = gb[GPIO_ODR / 4u] & (1u << PB2_SPK_PIN);

  /* Mute speaker amp (PB2 low) so PA11 pulses do not couple into audio amp. */
  gb[GPIO_BSRR / 4u] = 1u << (PB2_SPK_PIN + 16u);

  /* Configure PA11 for fast push/pull delta-sigma without disturbing other pins. */
  m_in = ga_moder_orig & ~(3u << (2u * PA11_PIN));
  ga[GPIO_PUPDR / 4u] = ga_pupd_orig & ~(3u << (2u * PA11_PIN));
  ga[GPIO_OSPEEDR / 4u] = ga_ospeed_orig | (3u << (2u * PA11_PIN));
  ga[GPIO_MODER / 4u] = m_in;

  for (burst = 0u; burst < 3u; burst++) {
    uint16_t r2b, r47;
    pa11_cic_t cic;
    uint32_t i, sum = 0u, mn = 0xFFFFFFFFu, mx = 0u, mean, ac_sum = 0u, hf_sum = 0u;
    char msg[60];
    char *p = msg;

    if (aborted(api)) {
      rc = -4;
      break;
    }

    if (burst == 0u) {
      r2b = reg2b_orig;
      r47 = reg47_orig;
    } else if (burst == 1u) {
      r2b = (uint16_t)(reg2b_orig | FLAT_2B_MASK);
      r47 = reg47_orig;
    } else {
      r2b = (uint16_t)(reg2b_orig | FLAT_2B_MASK);
      r47 = (uint16_t)((reg47_orig & ~0x0F40u) | 0x0400u);
    }

    if (api->fd_write(REG_2B_FILT, r2b) != 0 || api->fd_write(REG_47_AF, r47) != 0) {
      rc = -3;
      break;
    }
    api->delay_us(500u);

    cic.i1 = 0u;
    cic.i2 = 0u;
    cic.z1 = 0u;
    cic.z2 = 0u;
    /* Drop 4 warm-up samples so 2nd-order CIC differences settle. */
    for (i = 0u; i < 4u; i++) {
      (void)pa11_sample(m_in, RX01_HOLD, RX01_W, &cic);
    }

    for (i = 0u; i < RX01_SAMPLES; i++) {
      uint32_t s = pa11_sample(m_in, RX01_HOLD, RX01_W, &cic);
      samples[i] = (uint16_t)s;
      sum += s;
      if (s < mn) mn = s;
      if (s > mx) mx = s;
    }

    mean = sum >> RX01_SAMPLES_LOG2;
    for (i = 0u; i < RX01_SAMPLES; i++) {
      uint32_t cur = samples[i];
      ac_sum += iabs_diff(cur, mean);
      if (i > 0u) hf_sum += iabs_diff(cur, samples[i - 1u]);
    }

    p = put_str(p, "rx01 b=");
    p = put_u32(p, burst);
    p = put_str(p, " r2b=");
    p = put_hex4(p, r2b);
    p = put_str(p, " r47=");
    p = put_hex4(p, r47);
    p = put_str(p, " p2p=");
    p = put_u32(p, mx - mn);
    p = put_str(p, " ac=");
    p = put_u32(p, ac_sum >> RX01_SAMPLES_LOG2);
    p = put_str(p, " hf=");
    p = put_u32(p, hf_sum >> RX01_SAMPLES_LOG2);
    *p = '\0';
    api->say(msg);
    rc += (int)RX01_SAMPLES;
  }

  /* Restore GPIOA, GPIOB PB2 speaker amp, and BK4829 REG_2B / REG_47 unconditionally. */
  ga[GPIO_ODR / 4u] = ga_odr_orig;
  ga[GPIO_OSPEEDR / 4u] = ga_ospeed_orig;
  ga[GPIO_PUPDR / 4u] = ga_pupd_orig;
  ga[GPIO_MODER / 4u] = ga_moder_orig;
  gb[GPIO_BSRR / 4u] = pb2_orig ? (1u << PB2_SPK_PIN) : (1u << (PB2_SPK_PIN + 16u));
  (void)api->fd_write(REG_2B_FILT, reg2b_orig);
  (void)api->fd_write(REG_47_AF, reg47_orig);
  return rc;
#endif
}
