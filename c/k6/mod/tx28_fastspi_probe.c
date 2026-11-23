/* TX-28 FD6818B/BK4829 RX-only fast-SPI bus timing & integrity probe.
 *
 * OFFLINE / INERT BY DEFAULT (TX28_BENCH_ENABLE=0 -> returns -9).
 * Strictly RECEIVE-ONLY (API level 2): zero TX/PA/PTT calls, refuses if
 * REG_30 TX bit is set, REG_36 PACTL is active, or native FSK is enabled.
 *
 * Tests how fast the KD32F328 MCU can bit-bang the FD6818B 3-wire SPI bus
 * (PB3=SCN, PB5=SCK, PB6=SDA) across 6 half-bit delay ladders
 * (nops = 32, 16, 8, 4, 2, 1) against benign idle register REG_5A (FSK sync0,
 * inert while REG_58[0]=0 and REG_3F[15:12]=0), cross-checking every write
 * via BOTH fast readback and stock api->fd_read().
 *
 * Measures exact 96 MHz cycles per 24-bit register write on TIM17 to establish
 * the safe fast-SPI ceiling for a future guarded firmware-side fd_write().
 * Never bypasses MOD_NO_WRITE: only REG_5A is ever addressed.
 *
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"

#ifndef TX28_BENCH_ENABLE
#define TX28_BENCH_ENABLE 0
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT   0x40002024u
#define TIM17       0x40014800u
#define TIM17_CNT   (TIM17 + 0x24u)
#define TIM17EN     (1u << 18)

#define GPIOB_BASE  0x48000400u
#define GPIOB_MODER (GPIOB_BASE + 0x00u)
#define GPIOB_IDR   (GPIOB_BASE + 0x10u)
#define GPIOB_BSRR  (GPIOB_BASE + 0x18u)
#define GPIOB_BRR   (GPIOB_BASE + 0x28u)

#define PIN_SCN     (1u << 3)
#define PIN_SCK     (1u << 5)
#define PIN_SDA     (1u << 6)
#define SDA_MODE_SHIFT 12u
#define SDA_MODE_MASK  (3u << SDA_MODE_SHIFT)
#define SDA_MODE_OUT   (1u << SDA_MODE_SHIFT)

#define R_CHIP      0x00u
#define R_STATE     0x30u
#define R_PA        0x36u
#define R_WORK      0x3Fu
#define R_FSK_CFG   0x58u
#define R_SCRATCH   0x5Au /* FSK sync0: benign while FSK disabled */

#if TX28_BENCH_ENABLE

#define N_LADDERS 6u
#define N_PATTERNS 8u
#define REPS_PER_LADDER 8u /* 8 * 8 = 64 writes+verifies per ladder */

static const uint8_t NOPS[N_LADDERS] = { 32u, 16u, 8u, 4u, 2u, 1u };
static const uint16_t PATTERNS[N_PATTERNS] = {
    0xA55Au, 0x5AA5u, 0x0F0Fu, 0xF0F0u,
    0xFFFFu, 0x0000u, 0x1234u, 0xCAFEu
};

static inline uint32_t mmio_rd(uint32_t a) {
    return *(volatile const uint32_t *)(uintptr_t)a;
}
static inline void mmio_wr(uint32_t a, uint32_t v) {
    *(volatile uint32_t *)(uintptr_t)a = v;
}

static inline void spin_nops(uint32_t n) {
    volatile uint32_t k = n;
    while (k) {
        __asm volatile("nop");
        k--;
    }
}

/* Direct 3-wire SPI 24-bit write (MSB-first, rising SCK edge), restricted to R_SCRATCH. */
static void fast_write_5a(uint16_t val, uint32_t half_nops) {
    uint32_t word = ((uint32_t)(R_SCRATCH & 0x7Fu) << 16) | (uint32_t)val;
    int i;
    mmio_wr(GPIOB_BRR, PIN_SCN);
    spin_nops(half_nops);
    for (i = 23; i >= 0; i--) {
        mmio_wr(GPIOB_BRR, PIN_SCK);
        if ((word >> i) & 1u) {
            mmio_wr(GPIOB_BSRR, PIN_SDA);
        } else {
            mmio_wr(GPIOB_BRR, PIN_SDA);
        }
        spin_nops(half_nops);
        mmio_wr(GPIOB_BSRR, PIN_SCK);
        spin_nops(half_nops);
    }
    mmio_wr(GPIOB_BSRR, PIN_SCN);
    spin_nops(half_nops);
    mmio_wr(GPIOB_BRR, PIN_SCK | PIN_SDA);
}

/* Direct 3-wire SPI 24-bit read (MSB-first), restricted to R_SCRATCH. */
static uint16_t fast_read_5a(uint32_t half_nops, uint32_t moder_out) {
    uint32_t cmd = (uint32_t)(R_SCRATCH | 0x80u);
    uint32_t moder_in = moder_out & ~SDA_MODE_MASK;
    uint16_t data = 0u;
    int i;

    mmio_wr(GPIOB_BRR, PIN_SCN);
    spin_nops(half_nops);
    for (i = 7; i >= 0; i--) {
        mmio_wr(GPIOB_BRR, PIN_SCK);
        if ((cmd >> i) & 1u) {
            mmio_wr(GPIOB_BSRR, PIN_SDA);
        } else {
            mmio_wr(GPIOB_BRR, PIN_SDA);
        }
        spin_nops(half_nops);
        mmio_wr(GPIOB_BSRR, PIN_SCK);
        spin_nops(half_nops);
    }
    mmio_wr(GPIOB_BRR, PIN_SCK);
    spin_nops(half_nops);
    mmio_wr(GPIOB_MODER, moder_in);
    spin_nops(half_nops);

    for (i = 0; i < 16; i++) {
        data = (uint16_t)(data << 1);
        mmio_wr(GPIOB_BSRR, PIN_SCK);
        if (mmio_rd(GPIOB_IDR) & PIN_SDA) data |= 1u;
        spin_nops(half_nops);
        mmio_wr(GPIOB_BRR, PIN_SCK);
        spin_nops(half_nops);
    }

    mmio_wr(GPIOB_MODER, moder_out);
    mmio_wr(GPIOB_BSRR, PIN_SCN);
    spin_nops(half_nops);
    mmio_wr(GPIOB_BRR, PIN_SCK | PIN_SDA);
    return data;
}

typedef struct {
    uint32_t t, c17, c14, apb2;
} tx28_clk_t;

static void clk_start(tx28_clk_t *c) {
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

static void clk_stop(tx28_clk_t *c) {
    mmio_wr(TIM17 + 0x00u, 0u);
    mmio_wr(RCC_APB2ENR, c->apb2);
}

static uint32_t clk_now(tx28_clk_t *c) {
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

#endif /* TX28_BENCH_ENABLE */

int k6_main(const k6_api_t *api) {
    if (!api || api->abi != 1u || api->level < K6_MODULE_LEVEL) return -3;
    if (api->fd_read(R_CHIP) != 0x4829u) return -6;
#if !TX28_BENCH_ENABLE
    api->say("tx28 inert; offline bench build required");
    return -9;
#else
    uint16_t saved_5a;
    uint32_t saved_moder;
    tx28_clk_t clk;
    unsigned li, r, pi;
    uint32_t total_err = 0u;
    int ret = 0;

    /* Strictly RX-only idle safety gates: never run if TX, PA, or FSK active. */
    if (api->key_down() || km_power_switch_off()) return -4;
    if ((api->fd_read(R_STATE) & 0x000Au) != 0u) return -2;
    if ((api->fd_read(R_PA) & 0x0080u) != 0u) return -2;
    if ((api->fd_read(R_FSK_CFG) & 0x0001u) != 0u) return -8;
    if ((api->fd_read(R_WORK) & 0xF000u) != 0u) return -8;

    saved_5a = api->fd_read(R_SCRATCH);
    saved_moder = mmio_rd(GPIOB_MODER);
    if ((saved_moder & SDA_MODE_MASK) != SDA_MODE_OUT) return -11;

    clk_start(&clk);

    for (li = 0; li < N_LADDERS; li++) {
        uint32_t half_nops = NOPS[li];
        uint32_t ladder_err = 0u;
        uint32_t wr_cyc_sum = 0u;
        uint32_t stock_cyc;

        /* Baseline 1 stock write timing on first ladder for direct ratio */
        {
            uint32_t t0 = clk_now(&clk);
            if (api->fd_write(R_SCRATCH, saved_5a) != 0) { ret = -13; goto cleanup; }
            stock_cyc = clk_now(&clk) - t0;
        }

        for (r = 0; r < REPS_PER_LADDER; r++) {
            if (api->key_down() || km_power_switch_off()) { ret = -4; goto cleanup; }
            for (pi = 0; pi < N_PATTERNS; pi++) {
                uint16_t pat = PATTERNS[pi];
                uint32_t t0 = clk_now(&clk);
                fast_write_5a(pat, half_nops);
                wr_cyc_sum += clk_now(&clk) - t0;

                if (fast_read_5a(half_nops, saved_moder) != pat) ladder_err++;
                if (api->fd_read(R_SCRATCH) != pat) ladder_err++;
            }
        }
        total_err += ladder_err;

        {
            char line[60], *p = line;
            uint32_t avg_cyc = wr_cyc_sum >> 6; /* divide by 64 */
            uint32_t avg_us_x10 = (avg_cyc * 6827u) >> 16; /* avg_cyc / 9.6 */
            uint32_t stock_us = (stock_cyc * 683u) >> 16;
            p = put_s(p, "tx28 nop=");
            p = put_u(p, half_nops);
            p = put_s(p, " cyc=");
            p = put_u(p, avg_cyc);
            p = put_s(p, " us10=");
            p = put_u(p, avg_us_x10);
            p = put_s(p, " err=");
            p = put_u(p, ladder_err);
            p = put_s(p, " stk=");
            p = put_u(p, stock_us);
            *p = '\0';
            api->say(line);
        }
    }

    ret = (total_err == 0u) ? (int)(N_LADDERS * REPS_PER_LADDER * N_PATTERNS) : -14;

cleanup:
    clk_stop(&clk);
    mmio_wr(GPIOB_MODER, saved_moder);
    if (api->fd_write(R_SCRATCH, saved_5a) != 0 || api->fd_read(R_SCRATCH) != saved_5a) {
        ret = -15;
    }
    return ret;
#endif
}
