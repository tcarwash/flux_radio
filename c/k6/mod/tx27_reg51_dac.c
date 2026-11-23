/* TX-27 FD6818B/BK4829 REG_51 single-write 8-bit signed DC frequency DAC probe.
 *
 * OFFLINE / RADIO-INERT BY DEFAULT (TX27_BENCH_ENABLE=0 -> returns -9).
 * Combines TX16M/N (REG_08=0x7FFFFF all-ones -> constant DC DCS polarity on
 * REG_51[13]) with TX16B (monotonic 7-bit subaudio deviation gain REG_51[6:0])
 * into a single-write signed DC frequency DAC:
 *   REG_51 = 0x8000 | bw_bit | (neg ? 0x2000 : 0) | (mag & 0x7F)
 *
 * Also exposes compile-time variants for REG_07 (DCS clock 0x0AD7 vs 0x1FFF)
 * and REG_51[9] (auto CDCSS bandwidth disable) to probe the subaudio LPF corner.
 *
 * Sequence (~1.07 s total << 1800 ms watchdog lease, 433.500 MHz dummy load):
 *   1. 60 ms carrier-only lead reference (REG_51 = 0)
 *   2. 9-step signed static DAC staircase (+60,+40,+20,0,-20,-40,-60,+60,-60),
 *      45 ms/step (405 ms) — tests linearity, sign symmetry, and zero-hold
 *   3. 40 ms carrier-only separator
 *   4. 24-symbol 4-FSK pilot (+3,-3,+1,-1 x6) + 64 held-out 4-FSK symbols
 *      (128 bits = 14-byte ASCII + CRC-16) at 200 sym/s (400 bps, 5 ms/sym)
 *      + 2 stop/guard symbols
 *   5. 20-step 2.5 ms alternating +60/-60 edge-rate probe (50 ms)
 *   6. 60 ms carrier-only shutdown guard
 *
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TX27_BENCH_ENABLE
#define TX27_BENCH_ENABLE 0
#endif
#ifndef TX27_RF_ENABLE
#define TX27_RF_ENABLE 0
#endif
#ifndef TX27_FIXTURE_ACK
#define TX27_FIXTURE_ACK 0
#endif
#ifndef TX27_WATCHDOG_ACK
#define TX27_WATCHDOG_ACK 0
#endif
#ifndef TX27_HZ
#define TX27_HZ 433500000u
#endif
#ifndef TX27_CLK_WORD
#define TX27_CLK_WORD 0x0AD7u /* stock 134.4 Hz DCS clock; try 0x1FFF for 396.6 Hz */
#endif
#ifndef TX27_BW_DISABLE
#define TX27_BW_DISABLE 0 /* 1 sets REG_51[9] (auto_cdcss_bw disable) */
#endif
#ifndef TX27_SYM_US
#define TX27_SYM_US 5000u /* 200 sym/s -> 400 bps 4-FSK */
#endif

#if TX27_RF_ENABLE && (!TX27_BENCH_ENABLE || !TX27_FIXTURE_ACK || !TX27_WATCHDOG_ACK)
#error "TX27 RF requires TX27_BENCH_ENABLE, TX27_FIXTURE_ACK, and TX27_WATCHDOG_ACK"
#endif
#if TX27_HZ != 433500000u
#error "TX27 probe is restricted to the 433.500-MHz dummy-load fixture"
#endif
#if TX27_CLK_WORD > 0x1FFFu
#error "TX27_CLK_WORD must fit REG_07[12:0] (bank 0)"
#endif
#if TX27_BW_DISABLE != 0 && TX27_BW_DISABLE != 1
#error "TX27_BW_DISABLE must be 0 or 1"
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT   0x40002024u
#define TIM17       0x40014800u
#define TIM17_CNT   (TIM17 + 0x24u)
#define TIM17EN     (1u << 18)

#define R_CHIP      0x00u
#define R_DCS_CLK   0x07u
#define R_DCS_WORD  0x08u
#define R_STATE     0x30u
#define R_DCS_CTRL  0x51u
#define R_SUB_TH    0x52u
#define R_RSSI      0x67u
#define R_TONE_G    0x70u
#define R_TONE_F    0x71u

#define LEASE_MS    1800u
#define BW_BIT      ((uint16_t)(TX27_BW_DISABLE ? 0x0200u : 0x0000u))
#define DAC_OUTER   60u
#define DAC_INNER   20u

#if TX27_BENCH_ENABLE

static uint16_t reg51_signed(int8_t code) {
    if (code == 0) return 0u;
    if (code < 0) {
        uint16_t mag = (uint16_t)(-code) & 0x7Fu;
        return (uint16_t)(0xA000u | BW_BIT | mag);
    }
    return (uint16_t)(0x8000u | BW_BIT | ((uint16_t)code & 0x7Fu));
}

static const int8_t STAIRCASE[9] = { 60, 40, 20, 0, -20, -40, -60, 60, -60 };

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). */
static uint16_t crc16_ccitt(const uint8_t *p, unsigned n) {
    uint16_t c = 0xFFFFu;
    unsigned i, b;
    for (i = 0; i < n; i++) {
        c ^= (uint16_t)((uint16_t)p[i] << 8);
        for (b = 0; b < 8u; b++) {
            c = (c & 0x8000u) ? (uint16_t)((c << 1) ^ 0x1021u) : (uint16_t)(c << 1);
        }
    }
    return c;
}

/* Map 2-bit dibit (bit1=sign: 1=+,0=-; bit0=mag: 1=outer,0=inner) to signed DAC code. */
static int8_t dibit_to_code(unsigned dibit) {
    int8_t mag = (dibit & 1u) ? (int8_t)DAC_OUTER : (int8_t)DAC_INNER;
    return (dibit & 2u) ? mag : (int8_t)(-mag);
}

static void build_4fsk_symbols(int8_t out[90]) {
    static const uint8_t msg[14] = {
        'F','L','U','X',' ','T','X','2','7',' ','D','A','C','!'
    };
    uint8_t frame[16];
    uint16_t crc;
    unsigned i, k;

    /* 24-symbol pilot: (+3, -3, +1, -1) x 6 */
    for (i = 0; i < 6u; i++) {
        out[4u * i + 0u] = (int8_t)DAC_OUTER;
        out[4u * i + 1u] = (int8_t)(-DAC_OUTER);
        out[4u * i + 2u] = (int8_t)DAC_INNER;
        out[4u * i + 3u] = (int8_t)(-DAC_INNER);
    }

    for (i = 0; i < 14u; i++) frame[i] = msg[i];
    crc = crc16_ccitt(frame, 14u);
    frame[14] = (uint8_t)(crc >> 8);
    frame[15] = (uint8_t)(crc & 0xFFu);

    /* 16 bytes = 128 bits = 64 4-FSK symbols (MSB-first dibits) */
    for (i = 0; i < 16u; i++) {
        for (k = 0; k < 4u; k++) {
            unsigned dibit = (frame[i] >> (6u - 2u * k)) & 3u;
            out[24u + 4u * i + k] = dibit_to_code(dibit);
        }
    }

    /* 2 trailing guard symbols */
    out[88] = (int8_t)DAC_OUTER;
    out[89] = 0;
}

static inline uint32_t mmio_rd(uint32_t a) {
    return *(volatile const uint32_t *)(uintptr_t)a;
}
static inline void mmio_wr(uint32_t a, uint32_t v) {
    *(volatile uint32_t *)(uintptr_t)a = v;
}

typedef struct {
    uint32_t t, c17, c14, apb2;
} tx27_clk_t;

static void clk_start(tx27_clk_t *c) {
    c->apb2 = mmio_rd(RCC_APB2ENR);
    mmio_wr(RCC_APB2ENR, c->apb2 | TIM17EN);
    mmio_wr(TIM17 + 0x00u, 0u);
    mmio_wr(TIM17 + 0x28u, 0u);
    mmio_wr(TIM17 + 0x2Cu, 0xFFFFu);
    mmio_wr(TIM17 + 0x14u, 1u);
    mmio_wr(TIM17 + 0x00u, 1u);
    c->t = 0u;
    c->c17 = mmio_rd(TIM17_CNT) & 0xFFFFu;
    c->c14 = mmio_rd(TIM14_CNT) & 0xFFFFu;
}

static void clk_stop(tx27_clk_t *c) {
    mmio_wr(TIM17 + 0x00u, 0u);
    mmio_wr(RCC_APB2ENR, c->apb2);
}

static uint32_t clk_now(tx27_clk_t *c) {
    uint32_t c17 = mmio_rd(TIM17_CNT) & 0xFFFFu;
    uint32_t c14 = mmio_rd(TIM14_CNT) & 0xFFFFu;
    uint32_t coarse = ((c14 - c->c14) & 0xFFFFu) * 9600u;
    uint32_t fine = (c17 - c->c17) & 0xFFFFu;
    int32_t diff = (int32_t)(int16_t)(uint16_t)(fine - coarse);
    c->c17 = c17;
    c->c14 = c14;
    c->t += coarse + (uint32_t)diff;
    return c->t;
}

static int safe(const k6_api_t *a) {
    return !a->key_down() && !km_power_switch_off();
}

static int free_channel(const k6_api_t *a) {
    return KM_RSSI_DBM(a->fd_read(R_RSSI)) <= -90;
}

static int put_verify(const k6_api_t *a, uint8_t r, uint16_t v) {
    return a->fd_write(r, v) == 0 && a->fd_read(r) == v ? 0 : -14;
}

static int wait_to(const k6_api_t *a, tx27_clk_t *c, uint32_t target) {
    while ((int32_t)(target - clk_now(c)) > 0) {
        if (!safe(a)) return -4;
    }
    return 0;
}

static char *put_s(char *p, const char *s) {
    while (*s) *p++ = *s++;
    return p;
}

static char *put_u(char *p, uint32_t n) {
    static const uint32_t pw[] = {
        1000000000u, 100000000u, 10000000u, 1000000u,
        100000u, 10000u, 1000u, 100u, 10u, 1u
    };
    unsigned i, started = 0;
    for (i = 0; i < 10u; i++) {
        uint32_t d = 0;
        while (n >= pw[i]) { n -= pw[i]; d++; }
        if (d || started || i == 9u) { *p++ = (char)('0' + d); started = 1; }
    }
    return p;
}

#endif /* TX27_BENCH_ENABLE */

int k6_main(const k6_api_t *api) {
    if (!api || api->abi != 1u || api->level < K6_MODULE_LEVEL) return -3;
    if (api->fd_read(R_CHIP) != 0x4829u) return -6;
#if !TX27_BENCH_ENABLE
    api->say("tx27 inert; offline bench build required");
    return -9;
#else
    uint16_t old51 = 0u, old52 = 0u, old70 = 0u, old71 = 0u;
    int8_t syms[90];
    tx27_clk_t clk;
    uint32_t t_next, wr_cyc = 0u, writes = 0u, late = 0u;
    unsigned i;
    int keyed = 0, snap = 0, clk_active = 0, ret = 90;

    if (api->rx_hz() != TX27_HZ || (api->fd_read(R_STATE) & 2u) || !safe(api)) {
        return -2;
    }
    if (!free_channel(api)) return -7;
    api->delay_us(50000u);
    if (!safe(api) || !free_channel(api)) return -7;

    build_4fsk_symbols(syms);

    if (api->tx_on(LEASE_MS) != 0) return -5;
    keyed = 1;
    if (api->tx_pa(0) != 0) { ret = -12; goto done; }

    old51 = api->fd_read(R_DCS_CTRL);
    old52 = api->fd_read(R_SUB_TH);
    old70 = api->fd_read(R_TONE_G);
    old71 = api->fd_read(R_TONE_F);
    snap = 1;
    if (old51 != 0u) { ret = -13; goto done; }

    /* Silence audio tones and configure constant all-ones raw 23-bit DCS word. */
    if (put_verify(api, R_TONE_G, 0u) != 0 ||
        put_verify(api, R_DCS_CTRL, 0u) != 0 ||
        api->fd_write(R_DCS_CLK, (uint16_t)TX27_CLK_WORD) != 0 ||
        api->fd_write(R_DCS_WORD, 0x0FFFu) != 0 ||
        api->fd_write(R_DCS_WORD, 0x87FFu) != 0) {
        ret = -14;
        goto done;
    }

    clk_start(&clk);
    clk_active = 1;

    /* Measure one REG_51 write duration for accurate landing compensation. */
    {
        uint32_t t0 = clk_now(&clk);
        if (api->fd_write(R_DCS_CTRL, 0u) != 0) { ret = -14; goto done; }
        wr_cyc = clk_now(&clk) - t0;
    }

#if TX27_RF_ENABLE
    if (api->tx_pa(1) != 0) { ret = -12; goto done; }
#endif

    /* 1. 60 ms carrier-only lead reference */
    t_next = clk_now(&clk) + 60000u * 96u;

    /* 2. 9-step static signed DAC staircase (45 ms/step) */
    for (i = 0; i < 9u; i++) {
        uint32_t start = t_next - wr_cyc;
        if ((int32_t)(start - clk_now(&clk)) < 0) late++;
        if ((ret = wait_to(api, &clk, start)) != 0) goto done;
        if (api->fd_write(R_DCS_CTRL, reg51_signed(STAIRCASE[i])) != 0) { ret = -14; goto done; }
        writes++;
        t_next += 45000u * 96u;
    }

    /* 3. 40 ms carrier-only separator */
    {
        uint32_t start = t_next - wr_cyc;
        if ((ret = wait_to(api, &clk, start)) != 0) goto done;
        if (api->fd_write(R_DCS_CTRL, 0u) != 0) { ret = -14; goto done; }
        writes++;
        t_next += 40000u * 96u;
    }

    /* 4. 24-symbol 4-FSK pilot + 64 held-out 4-FSK symbols + 2 guard symbols */
    for (i = 0; i < 90u; i++) {
        uint32_t start = t_next - wr_cyc;
        if ((int32_t)(start - clk_now(&clk)) < 0) late++;
        if ((ret = wait_to(api, &clk, start)) != 0) goto done;
        if (api->fd_write(R_DCS_CTRL, reg51_signed(syms[i])) != 0) { ret = -14; goto done; }
        writes++;
        t_next += TX27_SYM_US * 96u;
    }

    /* 5. 20-step 2.5 ms alternating +60/-60 edge-rate probe */
    for (i = 0; i < 20u; i++) {
        int8_t c = (i & 1u) ? (int8_t)(-DAC_OUTER) : (int8_t)DAC_OUTER;
        uint32_t start = t_next - wr_cyc;
        if ((int32_t)(start - clk_now(&clk)) < 0) late++;
        if ((ret = wait_to(api, &clk, start)) != 0) goto done;
        if (api->fd_write(R_DCS_CTRL, reg51_signed(c)) != 0) { ret = -14; goto done; }
        writes++;
        t_next += 2500u * 96u;
    }

    /* 6. 60 ms shutdown carrier guard */
    {
        uint32_t start = t_next - wr_cyc;
        if ((ret = wait_to(api, &clk, start)) != 0) goto done;
        if (api->fd_write(R_DCS_CTRL, 0u) != 0) { ret = -14; goto done; }
        writes++;
        t_next += 60000u * 96u;
        if ((ret = wait_to(api, &clk, t_next)) != 0) goto done;
    }

    ret = 90;

done:
    if (clk_active) clk_stop(&clk);
    if (keyed) {
        (void)api->tx_pa(0);
        if (snap) {
            if (put_verify(api, R_DCS_CTRL, 0u) != 0) ret = -15;
            if (put_verify(api, R_SUB_TH, old52) != 0) ret = -15;
            if (put_verify(api, R_TONE_G, old70) != 0) ret = -15;
            if (put_verify(api, R_TONE_F, old71) != 0) ret = -15;
            if (put_verify(api, R_DCS_CTRL, old51) != 0) ret = -15;
        }
        api->tx_off();
    }
    if (ret == 90) {
        char msg[60], *p = msg;
        p = put_s(p, "tx27 ok w=");
        p = put_u(p, writes);
        p = put_s(p, " late=");
        p = put_u(p, late);
        p = put_s(p, " bw=");
        p = put_u(p, (uint32_t)TX27_BW_DISABLE);
        p = put_s(p, " pa=");
        p = put_u(p, (uint32_t)TX27_RF_ENABLE);
        *p = '\0';
        api->say(msg);
    } else {
        api->say("tx27 abort; RX restored");
    }
    return ret;
#endif
}
