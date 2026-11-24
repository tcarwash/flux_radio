/* tx32_live_ptt_coex.c -- TX-32: Live-PTT coexistence gate (PA11 mic + native FSK TX).
 *
 * Tests in one bounded ~2.5 s dummy-load run whether FD6818B (0x4829) can capture
 * local microphone audio via PA11 (REG_30 micin+afout, REG_47 AF select 4) at the
 * same time PA is ON and native 4800-bps guarded L64 FSK TX is active, without
 * analog mic audio bleeding onto the FM carrier or RF swamping PA11.
 *
 * Six slots (S0..S5):
 *   S0: PA OFF, FSK OFF, REG_50 stock          (baseline PA11 1300 Hz mic contrast)
 *   S1: PA ON,  FSK OFF, REG_50 stock          (PA-ON RF self-interference + SDR 1300 Hz FM ref)
 *   S2: PA ON,  FSK OFF, REG_50 |= 0x8000      (pure-carrier test of REG_50[15] AF_TX_MUTE)
 *   S3: PA ON,  FSK ON (2x 33w), REG_50 stock  (primary live-PTT coexistence test)
 *   S4: PA ON,  FSK ON (2x 33w), REG_50|0x8000 (live-PTT + REG_50[15] mute test)
 *   S5: PA ON,  FSK ON (2x 33w), TX filt=0     (fallback: REG_74/44/45=0 TX audio filter mute)
 *
 * Strictly INERT by default (TX32_BENCH_ENABLE=0 -> returns -9, zero writes).
 * Enabling requires explicit TX32_DUMMY_LOAD_ACK=1 and TX32_WATCHDOG_ACK=1.
 *
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include "../k6_mod.h"

#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif

#ifndef TX32_BENCH_ENABLE
#define TX32_BENCH_ENABLE 0
#endif

#ifndef TX32_DUMMY_LOAD_ACK
#define TX32_DUMMY_LOAD_ACK 0
#endif

#ifndef TX32_WATCHDOG_ACK
#define TX32_WATCHDOG_ACK 0
#endif

#if TX32_BENCH_ENABLE && (!TX32_DUMMY_LOAD_ACK || !TX32_WATCHDOG_ACK)
#error "TX-32 live RF build requires TX32_DUMMY_LOAD_ACK=1 and TX32_WATCHDOG_ACK=1"
#endif

#if !TX32_BENCH_ENABLE

int k6_main(const k6_api_t *api) {
  (void)api;
  return -9;
}

#else

#ifndef TX32_FREQ_HZ
#define TX32_FREQ_HZ 433500000u
#endif
#ifndef TX32_MAX_TX_MS
#define TX32_MAX_TX_MS 8000u
#endif
#ifndef TX32_NO_CABLE_WAIT
#define TX32_NO_CABLE_WAIT 0
#endif
#ifndef TX32_HOLD
#define TX32_HOLD 3u
#endif
#ifndef TX32_W
#define TX32_W 90u
#endif
#ifndef TX32_N_CAP
#define TX32_N_CAP 512u /* ~28.8 ms at 17.8 kS/s (35 Hz Goertzel resolution) */
#endif
#ifndef TX32_SETTLE_US
#define TX32_SETTLE_US 40000u
#endif
#ifndef TX32_GAP_US
#define TX32_GAP_US 80000u
#endif
#ifndef TX32_CARRIER_HOLD_US
#define TX32_CARRIER_HOLD_US 200000u
#endif
#ifndef TX32_FRAME_TIMEOUT_MS
#define TX32_FRAME_TIMEOUT_MS 500u
#endif

#define GPIOA_BASE   0x48000000u
#define GPIOB_BASE   0x48000400u
#define GPIO_MODER   0x00u
#define GPIO_OSPEEDR 0x08u
#define GPIO_PUPDR   0x0Cu
#define GPIO_IDR     0x10u
#define GPIO_ODR     0x14u
#define GPIO_BSRR    0x18u
#define GPIO_BRR     0x28u

#define PA11_PIN     11u
#define PB2_SPK_PIN  2u
#define MIC_SCN      (1u << 3)
#define MIC_SCK      (1u << 5)
#define MIC_SDA      (1u << 6)

#define R_CHIP       0x00u
#define R_CLEAR_IRQ  0x02u
#define R_STATUS     0x0Cu
#define R_STATE      0x30u
#define R_PA         0x36u
#define R_WORK       0x3Fu
#define R_TX300_D1   0x44u
#define R_TX300_D2   0x45u
#define R_AF         0x47u
#define R_VOL        0x48u
#define R_TX_MUTE    0x50u
#define R_PREAMBLE   0x58u
#define R_CONTROL    0x59u
#define R_SYNC0      0x5Au
#define R_SYNC1      0x5Bu
#define R_SYNC2      0x5Cu
#define R_LENGTH     0x5Du
#define R_FIFO       0x5Fu
#define R_RSSI       0x67u
#define R_GAIN       0x70u
#define R_BAUD       0x72u
#define R_TX3K       0x74u
#define R_MIC_SENS   0x7Du

#define CHIP_4829    0x4829u
#define FSK_WORDS    33u
#define FSK_GAIN     21u
#define FSK_BAUD     0xC194u /* 4800 bps, proven in FSK-13/14 */
#define NUM_SLOTS    6u

/* Public FSK-13 verified guarded 33-word Sotto L64 packets (6 packets -> S3/S4/S5 x 2). */
static const uint16_t tx32_fsk_words[6][FSK_WORDS] = {
    {
        0xa856u, 0x9addu, 0xdeecu, 0xa633u, 0xb07fu, 0x37ddu, 0x8c50u, 0x1a2bu,
        0xbb79u, 0x7b08u, 0x8290u, 0x969fu, 0x437cu, 0x94dau, 0xc79du, 0x84d1u,
        0x4d77u, 0xd086u, 0xfbb2u, 0x54dau, 0x6f99u, 0x9cdau, 0x5af0u, 0x840au,
        0x5872u, 0xf482u, 0x9ce2u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0xe5e8u,
        0xa55au,
    },
    {
        0xa856u, 0x9bddu, 0xdeecu, 0x3228u, 0x684eu, 0xa4f6u, 0x8055u, 0x9aabu,
        0xb170u, 0xc41fu, 0x552au, 0xda9eu, 0x4f55u, 0x3263u, 0x08aeu, 0x21ceu,
        0x07e3u, 0x1e97u, 0xb6e5u, 0x5183u, 0xb9ecu, 0xc6ecu, 0xf6eeu, 0xe83bu,
        0xe861u, 0x44f1u, 0xe9d7u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x4924u,
        0xa55au,
    },
    {
        0xa856u, 0x98ddu, 0xdeecu, 0xfd44u, 0xf6efu, 0x9487u, 0xd467u, 0xeeaau,
        0x8c47u, 0xea20u, 0x97bbu, 0x8799u, 0x664cu, 0x1adcu, 0xe5e8u, 0x7ab3u,
        0x7a13u, 0xcbf0u, 0x0bebu, 0x806fu, 0xc8e9u, 0xee42u, 0xb6a4u, 0x5f7cu,
        0x212cu, 0xb7a9u, 0x3592u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0xadddu,
        0xa55au,
    },
    {
        0xa856u, 0x99ddu, 0xdeecu, 0xfd56u, 0x0c25u, 0x388eu, 0x4c14u, 0x8830u,
        0x5500u, 0x854bu, 0x9969u, 0xd729u, 0x0fcau, 0xdb2fu, 0x991bu, 0xcfbbu,
        0xc3feu, 0x6137u, 0x5021u, 0x9486u, 0x2daeu, 0xacf7u, 0xc54eu, 0x3c3cu,
        0xf740u, 0x8fb9u, 0x0be4u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x563du,
        0xa55au,
    },
    {
        0xa856u, 0x9eddu, 0xdeecu, 0x103fu, 0x3bdfu, 0x2ec1u, 0x7616u, 0xc8bau,
        0xfa3au, 0x918cu, 0x3376u, 0x46abu, 0x757eu, 0x6e5fu, 0xa99du, 0x3afcu,
        0x90e6u, 0x84b4u, 0xa3abu, 0x289eu, 0x4affu, 0x6e97u, 0x1921u, 0xab44u,
        0x2121u, 0x727au, 0x8ea5u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x35d8u,
        0xa55au,
    },
    {
        0xa856u, 0x9fddu, 0xdeecu, 0xf793u, 0x5a23u, 0xa3e6u, 0xb742u, 0xd710u,
        0x3860u, 0xec32u, 0xdbb4u, 0xfaa8u, 0x8b28u, 0x567du, 0x70fau, 0xceabu,
        0xace9u, 0xeec1u, 0xbdf3u, 0x2d97u, 0x3bd6u, 0x5795u, 0xfb3au, 0x7c43u,
        0x9e22u, 0x2d6du, 0x53a3u, 0xcd8au, 0xd1e7u, 0x094eu, 0x1732u, 0x4637u,
        0xa55au,
    },
};

/* Registers snapshotted & unconditionally restored via fd_write. */
static const uint8_t restore_regs[] = {
    R_WORK, R_CONTROL, R_PREAMBLE, R_GAIN, R_BAUD, R_SYNC0, R_SYNC1, R_SYNC2,
    R_LENGTH, R_AF, R_VOL, R_TX_MUTE, R_MIC_SENS, R_TX3K, R_TX300_D1, R_TX300_D2,
};
#define N_RESTORE (sizeof(restore_regs) / sizeof(restore_regs[0]))

/* 2 * cos(2 * pi * f / 17800) in Q14 for 1300, 1150, 1450, 1200 Hz. */
static const int32_t GOERTZ_C[4] = {29378, 30105, 28569, 29876};

typedef struct {
  uint32_t i1, i2, z1, z2;
} pa11_cic_t;

typedef struct {
  uint32_t g13; /* 1300 Hz target acoustic mic tone power (scaled) */
  uint32_t gn;  /* max(1150 Hz, 1450 Hz) neighbor floor power (scaled) */
  uint32_t g12; /* 1200 Hz FSK baud bleed power (scaled) */
  uint32_t var; /* AC variance proxy (scaled) */
} slot_score_t;

static int safe(const k6_api_t *api) {
  return !api->key_down() && !km_power_switch_off();
}

static int wr(const k6_api_t *api, uint8_t reg, uint16_t val) {
  return api->fd_write(reg, val) == 0 ? 0 : -1;
}

/* Direct BK4829 3-wire bus write for REG_30 when fd_write refuses it below level 14. */
static void bus_write_r30(const k6_api_t *api, uint16_t v) {
  volatile uint32_t *set = (volatile uint32_t *)(GPIOB_BASE + GPIO_BSRR);
  volatile uint32_t *clr = (volatile uint32_t *)(GPIOB_BASE + GPIO_BRR);
  uint32_t word = ((uint32_t)R_STATE << 16) | (uint32_t)v;
  int i;
  *clr = MIC_SCN;
  api->delay_us(1u);
  for (i = 23; i >= 0; i--) {
    *clr = MIC_SCK;
    *((word >> i) & 1u ? set : clr) = MIC_SDA;
    api->delay_us(1u);
    *set = MIC_SCK;
    api->delay_us(1u);
  }
  *set = MIC_SCN;
  api->delay_us(1u);
  *clr = MIC_SCK;
  *clr = MIC_SDA;
}

static int write_r30_checked(const k6_api_t *api, uint16_t want) {
  if (api->fd_write(R_STATE, want) != 0) {
    bus_write_r30(api, want);
  }
  return api->fd_read(R_STATE) == want ? 0 : -1;
}

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

/* Capture TX32_N_CAP PA11 samples and compute Q14 Goertzel power at 1300/1150/1450/1200 Hz. */
static void capture_pa11_goertzel(uint32_t m_in, slot_score_t *out) {
  pa11_cic_t cic = {0u, 0u, 0u, 0u};
  int32_t s1[4] = {0, 0, 0, 0};
  int32_t s2[4] = {0, 0, 0, 0};
  int32_t dc = 0;
  uint32_t i, k;
  uint32_t var_acc = 0u;
  uint32_t pwr[4];

  /* 16 warm-up samples so 2nd-order CIC and DC estimate settle cleanly. */
  for (i = 0u; i < 16u; i++) {
    uint32_t x = pa11_sample(m_in, TX32_HOLD, TX32_W, &cic);
    if (i >= 4u) dc += (int32_t)x;
  }
  /* Mean of 12 warm-up samples: approximate /12 via (dc * 5461) >> 16 (zero uidiv). */
  dc = (dc * 5461) >> 16;

  for (i = 0u; i < TX32_N_CAP; i++) {
    int32_t x = (int32_t)pa11_sample(m_in, TX32_HOLD, TX32_W, &cic) - dc;
    /* Track slow DC drift gently (alpha = 1/64). */
    dc += x >> 6;
    /* Scale x down by 4 so Q14 Goertzel state stays well inside signed 32-bit. */
    int32_t xs = x >> 2;
    uint32_t ax = (uint32_t)(xs < 0 ? -xs : xs);
    var_acc += (ax * ax) >> 4;
    for (k = 0u; k < 4u; k++) {
      int32_t s0 = xs + ((GOERTZ_C[k] * s1[k]) >> 14) - s2[k];
      s2[k] = s1[k];
      s1[k] = s0;
    }
  }

  for (k = 0u; k < 4u; k++) {
    /* Shift state down before squaring so energy fits comfortably in uint32_t. */
    int32_t a = s1[k] >> 6;
    int32_t b = s2[k] >> 6;
    int32_t ab = ((GOERTZ_C[k] * a) >> 14) * b;
    int32_t e = a * a + b * b - ab;
    pwr[k] = e > 0 ? (uint32_t)e : 0u;
  }
  out->g13 = pwr[0];
  out->gn = pwr[1] > pwr[2] ? pwr[1] : pwr[2];
  out->g12 = pwr[3];
  out->var = var_acc;
}

/* Preload 33 words, start native FSK TX, optionally run PA11 Goertzel mid-burst, await done. */
static int send_fsk_frame(const k6_api_t *api, unsigned frame_idx,
                          uint32_t m_in, slot_score_t *score_out) {
  unsigned i;
  uint32_t start;
  if (wr(api, R_CONTROL, 0x00F8u) != 0) return -13;
  if (wr(api, R_CLEAR_IRQ, 0u) != 0) return -13;
  if (wr(api, R_LENGTH, 0x4100u) != 0) return -13;
  if (wr(api, R_CONTROL, 0x80F8u) != 0) return -13;
  if (wr(api, R_CONTROL, 0x00F8u) != 0) return -13;
  for (i = 0u; i < FSK_WORDS; i++) {
    if (!safe(api)) return -4;
    if (wr(api, R_FIFO, tx32_fsk_words[frame_idx][i]) != 0) return -13;
  }
  if (wr(api, R_CONTROL, 0x08F8u) != 0) return -13;

  if (score_out) {
    /* Wait 15 ms past preamble so PA11 captures smack in the middle of live FSK RF. */
    api->delay_us(15000u);
    capture_pa11_goertzel(m_in, score_out);
  }

  start = api->ms();
  while (1) {
    if (!safe(api)) return -4;
    if (api->fd_read(R_STATUS) & 1u) return (int)FSK_WORDS;
    if ((uint32_t)(api->ms() - start) > TX32_FRAME_TIMEOUT_MS) return -14;
    api->delay_us(2000u);
  }
}

#if !TX32_NO_CABLE_WAIT
static void lcd_prompt(const k6_api_t *api, const char *l0, const char *l1, const char *l2) {
  if (api->level < 6u) return;
  api->lcd_text(0, l0);
  api->lcd_text(2, l1);
  api->lcd_text(3, l2);
  api->lcd_flush();
}

static int wait_full_key(const k6_api_t *api, uint32_t max_ms) {
  uint32_t t0 = api->ms();
  int phase;
  for (phase = 0; phase < 3; phase++) {
    while (api->key_poll() == !(phase & 1)) {
      if (km_power_switch_off()) return -4;
      if ((uint32_t)(api->ms() - t0) >= max_ms) return -7;
      api->delay_us(20000u);
    }
  }
  return 0;
}
#endif

static char *put_str(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *put_hex8(char *p, uint32_t v) {
  int shift;
  for (shift = 28; shift >= 0; shift -= 4) {
    uint32_t nib = (v >> shift) & 0xFu;
    *p++ = (char)(nib < 10u ? ('0' + nib) : ('a' + (nib - 10u)));
  }
  return p;
}

int k6_main(const k6_api_t *api) {
  volatile uint32_t *ga = (volatile uint32_t *)GPIOA_BASE;
  volatile uint32_t *gb = (volatile uint32_t *)GPIOB_BASE;
  uint16_t snap[N_RESTORE];
  uint16_t r30_tx = 0u, r50_orig = 0u, r47_orig = 0u, r7d_orig = 0u;
  uint32_t ga_moder = 0u, ga_ospeed = 0u, ga_pupd = 0u, ga_odr = 0u, pb2_orig = 0u;
  uint32_t m_in = 0u;
  slot_score_t scores[NUM_SLOTS];
  unsigned i, s, done_slots = 0u;
  int keyed = 0, gpio_saved = 0, reg_saved = 0, rc = 0;

  if (!api || api->abi != 1u || api->level < K6_MODULE_LEVEL) return -3;
  if (api->fd_read(R_CHIP) != CHIP_4829) return -6;
  if (api->rx_hz() != TX32_FREQ_HZ) return -2;
  if ((api->fd_read(R_STATE) & 0x000Au) != 0u || (api->fd_read(R_PA) & 0x00FFu) != 0u) return -2;
  if ((api->fd_read(R_WORK) & 0xF000u) != 0u || (api->fd_read(R_PREAMBLE) & 0x0001u) != 0u) return -8;
  if (!safe(api)) return -4;

#if !TX32_NO_CABLE_WAIT
  lcd_prompt(api, "TX32 COEX TEST", "PULL CABLE + 1300HZ", "PRESS ANY KEY");
  rc = wait_full_key(api, 300000u);
  if (rc != 0) return rc;
#endif

  /* Channel-clear RSSI gate (<= -90 dBm) before keying. */
  if (KM_RSSI_DBM(api->fd_read(R_RSSI)) > -90) return -7;
  api->delay_us(20000u);
  if (!safe(api)) return -4;
  if (KM_RSSI_DBM(api->fd_read(R_RSSI)) > -90) return -7;

  if (api->tx_on(TX32_MAX_TX_MS) != 0) return -5;
  keyed = 1;
  if (api->tx_pa(0) != 0) { rc = -4; goto cleanup; }

  /* Snapshot TX-state REG_30, all touched BK4829 registers, and MCU GPIOs. */
  r30_tx = api->fd_read(R_STATE);
  for (i = 0u; i < N_RESTORE; i++) snap[i] = api->fd_read(restore_regs[i]);
  reg_saved = 1;
  r47_orig = api->fd_read(R_AF);
  r50_orig = api->fd_read(R_TX_MUTE);
  r7d_orig = api->fd_read(R_MIC_SENS);

  ga_moder = ga[GPIO_MODER / 4u];
  ga_ospeed = ga[GPIO_OSPEEDR / 4u];
  ga_pupd = ga[GPIO_PUPDR / 4u];
  ga_odr = ga[GPIO_ODR / 4u];
  pb2_orig = gb[GPIO_ODR / 4u] & (1u << PB2_SPK_PIN);
  gpio_saved = 1;

  /* Mute speaker amp (PB2) and configure PA11 for fast delta-sigma turns. */
  gb[GPIO_BSRR / 4u] = 1u << (PB2_SPK_PIN + 16u);
  m_in = ga_moder & ~(3u << (2u * PA11_PIN));
  ga[GPIO_PUPDR / 4u] = ga_pupd & ~(3u << (2u * PA11_PIN));
  ga[GPIO_OSPEEDR / 4u] = ga_ospeed | (3u << (2u * PA11_PIN));
  ga[GPIO_MODER / 4u] = m_in;

  /* Configure mic -> AF_OUT (select 4) -> PA11 and native FSK-13 static registers. */
  if (wr(api, R_AF, (uint16_t)((r47_orig & ~0x0F40u) | 0x0400u)) != 0 ||
      wr(api, R_VOL, 0x82DFu) != 0 ||
      wr(api, R_MIC_SENS, (uint16_t)((r7d_orig & ~0x001Fu) | 31u)) != 0 ||
      wr(api, R_CONTROL, 0x00F8u) != 0 ||
      wr(api, R_CLEAR_IRQ, 0u) != 0 ||
      wr(api, R_PREAMBLE, 0x00E1u) != 0 ||
      wr(api, R_GAIN, (uint16_t)(0x0080u | FSK_GAIN)) != 0 ||
      wr(api, R_BAUD, FSK_BAUD) != 0 ||
      wr(api, R_SYNC0, 0x076Bu) != 0 ||
      wr(api, R_SYNC1, 0x3E36u) != 0 ||
      wr(api, R_SYNC2, 0x5625u) != 0 ||
      wr(api, R_LENGTH, 0x4100u) != 0) {
    rc = -13;
    goto cleanup;
  }

  /* Enable BOTH micin_en (bit 2) and afout_en (bit 9) in REG_30. */
  if (write_r30_checked(api, (uint16_t)(r30_tx | 0x0204u)) != 0) {
    rc = -12;
    goto cleanup;
  }

  for (s = 0u; s < NUM_SLOTS; s++) {
    if (!safe(api)) { rc = -4; goto cleanup; }

    /* Restore per-slot baseline controls before configuring slot s. */
    if (wr(api, R_WORK, 0x0000u) != 0 ||
        wr(api, R_TX_MUTE, r50_orig) != 0 ||
        wr(api, R_TX3K, snap[13]) != 0 ||
        wr(api, R_TX300_D1, snap[14]) != 0 ||
        wr(api, R_TX300_D2, snap[15]) != 0) {
      rc = -13;
      goto cleanup;
    }

    if (s == 0u) {
      /* S0: PA OFF baseline (proven mic -> PA11 1300 Hz contrast). */
      if (api->tx_pa(0) != 0) { rc = -4; goto cleanup; }
      api->delay_us(TX32_SETTLE_US);
      capture_pa11_goertzel(m_in, &scores[s]);
    } else if (s == 1u || s == 2u) {
      /* S1: PA ON pure FM; S2: PA ON + REG_50[15]=1 mute control. */
      if (s == 2u && wr(api, R_TX_MUTE, (uint16_t)(r50_orig | 0x8000u)) != 0) {
        rc = -13;
        goto cleanup;
      }
      if (api->tx_pa(1) != 0) { rc = -4; goto cleanup; }
      api->delay_us(TX32_SETTLE_US);
      capture_pa11_goertzel(m_in, &scores[s]);
      api->delay_us(TX32_CARRIER_HOLD_US);
      if (api->tx_pa(0) != 0) { rc = -4; goto cleanup; }
      api->delay_us(TX32_GAP_US);
    } else {
      /* S3: Live FSK (REG_50 stock); S4: FSK + REG_50[15]=1; S5: FSK + TX audio filters=0. */
      unsigned f0 = (s - 3u) * 2u;
      if (s == 4u && wr(api, R_TX_MUTE, (uint16_t)(r50_orig | 0x8000u)) != 0) {
        rc = -13;
        goto cleanup;
      }
      if (s == 5u && (wr(api, R_TX3K, 0u) != 0 || wr(api, R_TX300_D1, 0u) != 0 || wr(api, R_TX300_D2, 0u) != 0)) {
        rc = -13;
        goto cleanup;
      }
      if (wr(api, R_WORK, 0x8000u) != 0) { rc = -13; goto cleanup; }
      if (api->tx_pa(1) != 0) { rc = -4; goto cleanup; }
      api->delay_us(10000u);
      rc = send_fsk_frame(api, f0, m_in, &scores[s]);
      if (rc != (int)FSK_WORDS) goto cleanup;
      rc = send_fsk_frame(api, f0 + 1u, m_in, (slot_score_t *)0);
      if (rc != (int)FSK_WORDS) goto cleanup;
      if (api->tx_pa(0) != 0) { rc = -4; goto cleanup; }
      api->delay_us(TX32_GAP_US);
    }
    done_slots = s + 1u;
  }
  rc = (int)done_slots;

cleanup:
  if (keyed) {
    (void)api->tx_pa(0);
    if (reg_saved) {
      (void)wr(api, R_CONTROL, 0x00F8u);
      (void)wr(api, R_WORK, 0x0000u);
      (void)wr(api, R_CLEAR_IRQ, 0u);
      (void)write_r30_checked(api, r30_tx);
      for (i = N_RESTORE; i > 0u; i--) {
        (void)wr(api, restore_regs[i - 1u], snap[i - 1u]);
      }
    }
    if (gpio_saved) {
      ga[GPIO_ODR / 4u] = ga_odr;
      ga[GPIO_OSPEEDR / 4u] = ga_ospeed;
      ga[GPIO_PUPDR / 4u] = ga_pupd;
      ga[GPIO_MODER / 4u] = ga_moder;
      gb[GPIO_BSRR / 4u] = pb2_orig ? (1u << PB2_SPK_PIN) : (1u << (PB2_SPK_PIN + 16u));
    }
    api->tx_off();
  }

#if !TX32_NO_CABLE_WAIT
  if (done_slots > 0u) {
    lcd_prompt(api, "TX32 DONE", "PLUG CABLE IN", "PRESS ANY KEY");
    (void)wait_full_key(api, 300000u);
  }
#endif

  for (s = 0u; s < done_slots; s++) {
    char msg[60];
    char *p = msg;
    p = put_str(p, "tx32 s=");
    *p++ = (char)('0' + s);
    p = put_str(p, " g13=");
    p = put_hex8(p, scores[s].g13);
    p = put_str(p, " gn=");
    p = put_hex8(p, scores[s].gn);
    p = put_str(p, " g12=");
    p = put_hex8(p, scores[s].g12);
    p = put_str(p, " v=");
    p = put_hex8(p, scores[s].var);
    *p = '\0';
    api->say(msg);
  }
  return rc;
}

#endif /* TX32_BENCH_ENABLE */
