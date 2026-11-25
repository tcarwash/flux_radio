/* TX-27B: strictly RX-idle, read-only register baseline after failed TX-27.
 * No RF; no fd_write(), tx_on(), tx_pa(), raw GPIO, PTT or host mutation.
 * This is the *module source*; installation still needs approved bench gate.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#define R_CHIP     0x00u
#define R_DCSWORD  0x08u
#define R_STATE    0x30u
#define R_DCSCTRL  0x51u
#define R_SUBTH    0x52u
#define R_TONEG    0x70u
#define R_TONEF    0x71u
#define TX27B_DELAY_US 5000u

typedef struct {
    uint16_t r51, r08, r52, r70, r71, r30;
} diag_snapshot_t;

static void snapshot(const k6_api_t *a, diag_snapshot_t *s) {
    s->r51 = a->fd_read(R_DCSCTRL);
    s->r08 = a->fd_read(R_DCSWORD);
    s->r52 = a->fd_read(R_SUBTH);
    s->r70 = a->fd_read(R_TONEG);
    s->r71 = a->fd_read(R_TONEF);
    s->r30 = a->fd_read(R_STATE);
}

static char *hex4(char *p, uint16_t n) {
    unsigned shift;
    for (shift = 0; shift < 16; shift += 4)
        *p++ = "0123456789abcdef"[(n >> (12u - shift)) & 15u];
    return p;
}

static char *put(char *p, const char *s) {
    while (*s) *p++ = *s++;
    return p;
}

static void report(const k6_api_t *a, const char *tag, uint16_t v1,
                   const char *tag2, uint16_t v2) {
    char line[48];
    char *p = hex4(put(line, tag), v1);
    p = hex4(put(p, tag2), v2);
    *p = '\0';
    a->say(line);
}

int k6_main(const k6_api_t *api) {
    diag_snapshot_t a, b;
    if (!api || api->abi != 1u || api->level < K6_MODULE_LEVEL) return -3;
    if (api->fd_read(R_CHIP) != 0x4829u) return -6;
    if ((api->fd_read(R_STATE) & 2u) || api->key_down() || km_power_switch_off())
        return -2;
    snapshot(api, &a);
    api->delay_us(TX27B_DELAY_US);
    if ((api->fd_read(R_STATE) & 2u) || api->key_down() || km_power_switch_off())
        return -2;
    snapshot(api, &b);
    report(api, "tx27b 51a=", a.r51, " 51b=", b.r51);
    report(api, "tx27b 08=", a.r08, " 52=", a.r52);
    report(api, "tx27b 70=", a.r70, " 71=", a.r71);
    report(api, "tx27b 30a=", a.r30, " 30b=", b.r30);
    if (a.r51 != b.r51 || a.r08 != b.r08 || a.r52 != b.r52 ||
        a.r70 != b.r70 || a.r71 != b.r71 || a.r30 != b.r30) return -11;
    if ((a.r30 & 2u) || (b.r30 & 2u)) return -2;
    /* Successful observation of a nonzero REG51 is a diagnostic SUCCESS. */
    return 27;
}
