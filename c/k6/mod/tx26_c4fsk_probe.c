/* TX-26 FD6818B/BK4829 4800-baud 4-FSK source probe (REG_5F sign + REG_70 magnitude).
 *
 * OFFLINE / RADIO-INERT BY DEFAULT (TX26_BENCH_ENABLE=0 -> returns -9).
 * Ports the upstream 0x4819 C4FM/4-FSK technique (REG_5F FIFO for symbol sign,
 * symbol-rate REG_70[6:0] writes for outer/inner magnitude) to the guarded
 * FD6818B (0x4829) 433.500-MHz dummy-load bench contract.
 *
 * Four bounded 4800-baud packets (30 FIFO words = 480 symbols each, ~100 ms
 * payload + 33.3 ms lead; total TX ~900 ms << 1800 ms watchdog lease):
 *   P0: 480-symbol PN9 at fixed outer gain (72) — baseline +-df & symbol clock
 *   P1: 480-symbol PN9 with mid-packet REG_70[6:0] staircase (72,54,36,24,12,
 *       96 symbols each) — tests live mid-packet gain update, linearity & lag
 *   P2: 240 zeros then 240 ones at fixed gain 72 — TX high-pass droop check
 *   P3: 480-symbol 4-FSK frame (64-symbol +-3/+-1 pilot + 416 held-out symbols
 *       carrying CRC-16-protected payload) with symbol-timed REG_70 writes
 *
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TX26_BENCH_ENABLE
#define TX26_BENCH_ENABLE 0
#endif
#ifndef TX26_RF_ENABLE
#define TX26_RF_ENABLE 0
#endif
#ifndef TX26_FIXTURE_ACK
#define TX26_FIXTURE_ACK 0
#endif
#ifndef TX26_WATCHDOG_ACK
#define TX26_WATCHDOG_ACK 0
#endif
#ifndef TX26_HZ
#define TX26_HZ 433500000u
#endif
#ifndef TX26_GAIN_OUTER
#define TX26_GAIN_OUTER 72u
#endif
#ifndef TX26_GAIN_INNER
#define TX26_GAIN_INNER 24u
#endif
#ifndef TX26_LAG_Q
#define TX26_LAG_Q 7 /* quarter-symbols from ideal edge (+1.75 sym prior) */
#endif
#ifndef TX26_CTRL_BASE
#define TX26_CTRL_BASE 0x00F8u /* 16 preamble bytes + 4-byte sync */
#endif

#if TX26_RF_ENABLE && (!TX26_BENCH_ENABLE || !TX26_FIXTURE_ACK || !TX26_WATCHDOG_ACK)
#error "TX26 RF requires TX26_BENCH_ENABLE, TX26_FIXTURE_ACK, and TX26_WATCHDOG_ACK"
#endif
#if TX26_HZ != 433500000u
#error "TX26 probe is restricted to the 433.500-MHz dummy-load fixture"
#endif
#if TX26_GAIN_OUTER > 127u || TX26_GAIN_INNER > 127u || TX26_GAIN_INNER >= TX26_GAIN_OUTER
#error "TX26 gains must satisfy 0 <= TX26_GAIN_INNER < TX26_GAIN_OUTER <= 127"
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT   0x40002024u
#define TIM17       0x40014800u
#define TIM17_CNT   (TIM17 + 0x24u)
#define TIM17EN     (1u << 18)

#define R_CHIP      0x00u
#define R_IRQ       0x02u
#define R_STATUS    0x0Cu
#define R_STATE     0x30u
#define R_WORK      0x3Fu
#define R_PREAMBLE  0x58u
#define R_CTRL      0x59u
#define R_SYNC0     0x5Au
#define R_SYNC1     0x5Bu
#define R_SYNC2     0x5Cu
#define R_LENGTH    0x5Du
#define R_FIFO      0x5Fu
#define R_RSSI      0x67u
#define R_GAIN      0x70u
#define R_BAUD      0x72u

#define FSK_BAUD_4800  0xC194u
#define FSK_MODE_4800  0x00E1u
#define FSK_SYNC0      0x076Bu
#define FSK_SYNC1      0x3E36u
#define FSK_SYNC2      0x5625u
#define FSK_IRQ_DONE   0x8000u
#define CTRL_CLR_TX    0x8000u
#define CTRL_TX_EN     0x0800u

#define SYM_CYCLES     20000u       /* 96 MHz / 4800 baud */
#define NSYM           480u
#define NWORDS         30u          /* 30 words = 60 bytes = 480 bits */
#define LEN_WORD       0x3B00u      /* (60 - 1) << 8 */
#define STEP_SYMS      96u          /* 5 steps * 96 = 480 */
#define WR_MAX_CYCLES  16000u       /* 166.7 us ceiling (< 208.3 us symbol) */
#define LEASE_MS       1800u
#define PKT_TIMEOUT_MS 350u
#define GAP_MS         20u

#define G70(g) ((uint16_t)(0x0080u | ((g) & 0x7Fu)))

#if TX26_BENCH_ENABLE

static const uint8_t SAVE_REGS[] = {
    R_WORK, R_CTRL, R_PREAMBLE, R_GAIN, R_BAUD,
    R_SYNC0, R_SYNC1, R_SYNC2, R_LENGTH
};
#define N_SAVE (sizeof(SAVE_REGS) / sizeof(SAVE_REGS[0]))

static const uint8_t P1_STEPS[5] = { 72u, 54u, 36u, 24u, 12u };

/* Deterministic 60-byte PN9 sign stream (LFSR x^9 + x^5 + 1, seed 0x1FF,
 * MSB-first per byte) shared identically with tools/k6_tx26_c4fsk_analyze.py. */
static void gen_pn9(uint8_t out[60]) {
    uint16_t s = 0x1FFu;
    unsigned i, b;
    for (i = 0; i < 60u; i++) {
        uint8_t v = 0;
        for (b = 0; b < 8u; b++) {
            uint16_t bit = (uint16_t)(s & 1u);
            v = (uint8_t)((v << 1) | bit);
            s = (uint16_t)((s >> 1) | ((((s ^ (s >> 5)) & 1u) << 8)));
        }
        out[i] = v;
    }
}

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over bytes[0..n-1]. */
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

/* P3: 480 4-FSK symbols encoded as sign_bytes[60] (1=+, 0=-) and
 * mag_bytes[60] (1=outer +-3, 0=inner +-1).
 *   Symbols 0..63  (bytes 0..7):  16x (+3, -3, +1, -1) pilot
 *     sign nibble: 1,0,1,0 = 0xAA; mag nibble: 1,1,0,0 = 0xCC.
 *   Symbols 64..479 (bytes 8..59): 416 held-out symbols = 832 bits = 104 bytes:
 *     102 payload bytes + 2-byte big-endian CRC-16 -> dibit (sign, mag). */
static void gen_p3_4fsk(uint8_t sign_b[60], uint8_t mag_b[60]) {
    static const uint8_t hdr[16] = {
        'F','L','U','X',' ','T','X','2','6',' ','4','F','S','K','!','\n'
    };
    uint8_t payload[104];
    uint16_t lfsr = 0x1A7u;
    uint16_t crc;
    unsigned i, b;

    for (i = 0; i < 8u; i++) {
        sign_b[i] = 0xAAu;
        mag_b[i] = 0xCCu;
    }
    for (i = 0; i < 16u; i++) payload[i] = hdr[i];
    for (i = 16u; i < 102u; i++) {
        uint8_t v = 0;
        for (b = 0; b < 8u; b++) {
            v = (uint8_t)((v << 1) | (lfsr & 1u));
            lfsr = (uint16_t)((lfsr >> 1) | ((((lfsr ^ (lfsr >> 5)) & 1u) << 8)));
        }
        payload[i] = v;
    }
    crc = crc16_ccitt(payload, 102u);
    payload[102] = (uint8_t)(crc >> 8);
    payload[103] = (uint8_t)(crc & 0xFFu);

    /* Pack 104 bytes (832 bits = 416 dibits) into sign_b[8..59] & mag_b[8..59].
     * Each dibit takes 2 bits MSB-first from payload: bit0=sign, bit1=mag. */
    for (i = 0; i < 52u; i++) {
        uint8_t b0 = payload[2u * i];
        uint8_t b1 = payload[2u * i + 1u];
        uint8_t sb = 0, mb = 0;
        for (b = 0; b < 4u; b++) {
            sb = (uint8_t)((sb << 1) | ((b0 >> (7u - 2u * b)) & 1u));
            mb = (uint8_t)((mb << 1) | ((b0 >> (6u - 2u * b)) & 1u));
        }
        for (b = 0; b < 4u; b++) {
            sb = (uint8_t)((sb << 1) | ((b1 >> (7u - 2u * b)) & 1u));
            mb = (uint8_t)((mb << 1) | ((b1 >> (6u - 2u * b)) & 1u));
        }
        sign_b[8u + i] = sb;
        mag_b[8u + i] = mb;
    }
}

static inline uint32_t mmio_rd(uint32_t a) {
    return *(volatile const uint32_t *)(uintptr_t)a;
}
static inline void mmio_wr(uint32_t a, uint32_t v) {
    *(volatile uint32_t *)(uintptr_t)a = v;
}

typedef struct {
    uint32_t t, c17, c14, apb2;
} tx26_clk_t;

typedef struct {
    const k6_api_t *api;
    tx26_clk_t clk;
    uint32_t wr_cycles;
    uint32_t writes;
    uint32_t late;
} tx26_ctx_t;

static void clk_start(tx26_clk_t *c) {
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

static void clk_stop(tx26_clk_t *c) {
    mmio_wr(TIM17 + 0x00u, 0u);
    mmio_wr(RCC_APB2ENR, c->apb2);
}

static uint32_t clk_now(tx26_clk_t *c) {
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

static int fd_w(const k6_api_t *a, uint8_t r, uint16_t v) {
    return a->fd_write(r, v) == 0 ? 0 : -13;
}

static int wait_cycles(tx26_ctx_t *ctx, uint32_t target) {
    while ((int32_t)(target - clk_now(&ctx->clk)) > 0) {
        if (km_power_switch_off()) return -4;
    }
    return 0;
}

static int timed_write(tx26_ctx_t *ctx, uint32_t land_at, uint8_t r, uint16_t v) {
    uint32_t start_at = land_at - ctx->wr_cycles;
    uint32_t t0, t1;
    if ((int32_t)(start_at - clk_now(&ctx->clk)) < 0) ctx->late++;
    if (wait_cycles(ctx, start_at) != 0) return -4;
    t0 = clk_now(&ctx->clk);
    if (fd_w(ctx->api, r, v) != 0) return -13;
    t1 = clk_now(&ctx->clk);
    ctx->wr_cycles = t1 - t0;
    ctx->writes++;
    return 0;
}

static unsigned lead_syms(uint16_t ctrl) {
    return 8u * (((ctrl >> 4) & 15u) + 1u) + ((ctrl & 8u) ? 32u : 16u);
}

static unsigned get_bit(const uint8_t *b, unsigned k) {
    return (b[k >> 3] >> (7u - (k & 7u))) & 1u;
}

/* Arm one 30-word packet: clear TX FIFO, write REG_5D AFTER REG_59=CTRL_BASE
 * (so REG_59 is not the immediately preceding bus write before REG_5F), preload
 * 30 words, wait 15 ms settle, start TX_EN. */
static int send_pkt(tx26_ctx_t *ctx, const uint8_t sign_b[60],
                    const uint8_t *mag_b, int is_p1) {
    const k6_api_t *a = ctx->api;
    uint32_t t_en, t_pay, start_ms;
    unsigned i;
    int rc;

    if ((rc = fd_w(a, R_GAIN, G70(TX26_GAIN_OUTER))) != 0) return rc;
    if ((rc = fd_w(a, R_IRQ, 0u)) != 0) return rc;
    if ((rc = fd_w(a, R_CTRL, (uint16_t)(TX26_CTRL_BASE | CTRL_CLR_TX))) != 0) return rc;
    if ((rc = fd_w(a, R_CTRL, (uint16_t)TX26_CTRL_BASE)) != 0) return rc;
    if ((rc = fd_w(a, R_LENGTH, LEN_WORD)) != 0) return rc;
    a->delay_us(2000u);

    for (i = 0; i < NWORDS; i++) {
        uint16_t w = (uint16_t)((uint16_t)sign_b[2u * i] |
                                ((uint16_t)sign_b[2u * i + 1u] << 8));
        if (!safe(a)) return -4;
        if ((rc = fd_w(a, R_FIFO, w)) != 0) return rc;
    }
    a->delay_us(15000u);
    if (!safe(a)) return -4;

    t_en = clk_now(&ctx->clk) + ctx->wr_cycles + 4800u; /* +50 us margin */
    if ((rc = timed_write(ctx, t_en, R_CTRL,
                          (uint16_t)(TX26_CTRL_BASE | CTRL_TX_EN))) != 0) {
        return rc;
    }
    t_pay = t_en + lead_syms((uint16_t)TX26_CTRL_BASE) * SYM_CYCLES +
            (uint32_t)((int32_t)TX26_LAG_Q * (int32_t)(SYM_CYCLES / 4u));

    if (is_p1) {
        for (i = 1u; i < 5u; i++) {
            uint32_t t_step = t_pay + i * STEP_SYMS * SYM_CYCLES;
            if ((rc = timed_write(ctx, t_step, R_GAIN, G70(P1_STEPS[i]))) != 0) {
                return rc;
            }
        }
    } else if (mag_b != 0) {
        unsigned cur_outer = 1u;
        for (i = 0; i < NSYM; i++) {
            unsigned want_outer = get_bit(mag_b, i);
            if (want_outer != cur_outer) {
                uint16_t g = want_outer ? G70(TX26_GAIN_OUTER) : G70(TX26_GAIN_INNER);
                if ((rc = timed_write(ctx, t_pay + i * SYM_CYCLES, R_GAIN, g)) != 0) {
                    return rc;
                }
                cur_outer = want_outer;
            }
        }
    }

    start_ms = a->ms();
    for (;;) {
        if (!safe(a)) return -4;
        if (a->fd_read(R_STATUS) & 1u) break;
        if ((uint32_t)(a->ms() - start_ms) > PKT_TIMEOUT_MS) return -14;
        a->delay_us(1000u);
    }
    if ((rc = fd_w(a, R_IRQ, 0u)) != 0) return rc;
    if ((rc = fd_w(a, R_CTRL, (uint16_t)TX26_CTRL_BASE)) != 0) return rc;
    a->delay_us(GAP_MS * 1000u);
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

#endif /* TX26_BENCH_ENABLE */

int k6_main(const k6_api_t *api) {
    if (!api || api->abi != 1u || api->level < K6_MODULE_LEVEL) return -3;
    if (api->fd_read(R_CHIP) != 0x4829u) return -6;
#if !TX26_BENCH_ENABLE
    api->say("tx26 inert; offline bench build required");
    return -9;
#else
    uint16_t saved[N_SAVE];
    uint8_t pn9[60], p2[60], p3_sign[60], p3_mag[60];
    tx26_ctx_t ctx;
    unsigned i;
    int keyed = 0, snap = 0, clk_active = 0, ret = 4;

    if (api->rx_hz() != TX26_HZ || (api->fd_read(R_STATE) & 2u) || !safe(api)) {
        return -2;
    }
    if (!free_channel(api)) return -7;
    api->delay_us(50000u);
    if (!safe(api) || !free_channel(api)) return -7;
    if (api->fd_read(R_WORK) & 0xA000u) return -8;

    gen_pn9(pn9);
    for (i = 0; i < 30u; i++) p2[i] = 0x00u;
    for (i = 30u; i < 60u; i++) p2[i] = 0xFFu;
    gen_p3_4fsk(p3_sign, p3_mag);

    if (api->tx_on(LEASE_MS) != 0) return -5;
    keyed = 1;
    if (api->tx_pa(0) != 0) { ret = -4; goto cleanup; }

    for (i = 0; i < N_SAVE; i++) saved[i] = api->fd_read(SAVE_REGS[i]);
    if ((saved[0] & 0xA000u) || (saved[1] & 0x0800u)) { ret = -8; goto cleanup; }
    snap = 1;

    ctx.api = api;
    ctx.writes = 0u;
    ctx.late = 0u;
    clk_start(&ctx.clk);
    clk_active = 1;

    /* Measure actual fd_write latency in 96 MHz cycles; refuse if too slow. */
    {
        uint32_t t0 = clk_now(&ctx.clk);
        if (fd_w(api, R_CTRL, (uint16_t)TX26_CTRL_BASE) != 0) { ret = -13; goto cleanup; }
        ctx.wr_cycles = clk_now(&ctx.clk) - t0;
        if (ctx.wr_cycles > WR_MAX_CYCLES) { ret = -16; goto cleanup; }
    }

    if (fd_w(api, R_IRQ, 0u) != 0 ||
        fd_w(api, R_WORK, FSK_IRQ_DONE) != 0 ||
        fd_w(api, R_PREAMBLE, FSK_MODE_4800) != 0 ||
        fd_w(api, R_BAUD, FSK_BAUD_4800) != 0 ||
        fd_w(api, R_SYNC0, FSK_SYNC0) != 0 ||
        fd_w(api, R_SYNC1, FSK_SYNC1) != 0 ||
        fd_w(api, R_SYNC2, FSK_SYNC2) != 0 ||
        fd_w(api, R_LENGTH, LEN_WORD) != 0 ||
        fd_w(api, R_GAIN, G70(TX26_GAIN_OUTER)) != 0) {
        ret = -13;
        goto cleanup;
    }

#if TX26_RF_ENABLE
    if (api->tx_pa(1) != 0) { ret = -4; goto cleanup; }
#endif

    if ((ret = send_pkt(&ctx, pn9, 0, 0)) != 0) goto cleanup;       /* P0 */
    if ((ret = send_pkt(&ctx, pn9, 0, 1)) != 0) goto cleanup;       /* P1 */
    if ((ret = send_pkt(&ctx, p2, 0, 0)) != 0) goto cleanup;        /* P2 */
    if ((ret = send_pkt(&ctx, p3_sign, p3_mag, 0)) != 0) goto cleanup; /* P3 */
    ret = 4;

cleanup:
    if (clk_active) clk_stop(&ctx.clk);
    if (keyed) {
        (void)api->tx_pa(0);
        if (snap) {
            if (fd_w(api, R_CTRL, (uint16_t)TX26_CTRL_BASE) != 0 ||
                fd_w(api, R_WORK, 0u) != 0 ||
                fd_w(api, R_IRQ, 0u) != 0) {
                ret = -15;
            }
            for (i = N_SAVE; i > 0u; i--) {
                if (fd_w(api, SAVE_REGS[i - 1u], saved[i - 1u]) != 0) ret = -15;
            }
        }
        api->tx_off();
    }
    if (ret == 4) {
        char msg[60], *p = msg;
        uint32_t w_us = ((ctx.wr_cycles + 48u) * 683u) >> 16;
        p = put_s(p, "tx26 ok pkts=4 w=");
        p = put_u(p, ctx.writes);
        p = put_s(p, " late=");
        p = put_u(p, ctx.late);
        p = put_s(p, " wus=");
        p = put_u(p, w_us);
        p = put_s(p, " pa=");
        p = put_u(p, (uint32_t)TX26_RF_ENABLE);
        *p = '\0';
        api->say(msg);
    } else {
        api->say("tx26 abort; RX restored");
    }
    return ret;
#endif
}
