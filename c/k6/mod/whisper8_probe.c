/* Flux Whisper8 narrow 8-tone FM-audio fixture: NOT Olivia-compatible.
 * OFFLINE and inert by default. Never install or run without an independent
 * radio/SDR lease, dummy load, firmware/slot backup and restoration harness.
 * The 32-symbol, 1.024-second stimulus ONLY qualifies symbol tones.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "whisper8_fixture.h"

#ifndef W8_BENCH_ENABLE
#define W8_BENCH_ENABLE 0
#endif
#ifndef W8_RF_ENABLE
#define W8_RF_ENABLE 0
#endif
#ifndef W8_DUMMY_LOAD_ACK
#define W8_DUMMY_LOAD_ACK 0
#endif
#ifndef W8_WATCHDOG_ACK
#define W8_WATCHDOG_ACK 0
#endif
#if W8_RF_ENABLE && (!W8_BENCH_ENABLE || !W8_DUMMY_LOAD_ACK || !W8_WATCHDOG_ACK)
#error "RF output requires explicit bench and watchdog acknowledgments"
#endif

#define W8_EXPECTED_HZ 433500000u
#define W8_LEASE_MS 1800u
#define W8_TONE_GAIN 0x9800u  /* 0x8000 | (24 << 8) */
#define W8_SYMBOL_MS 32u

#if W8_BENCH_ENABLE
static int wr(const k6_api_t *api, unsigned reg, unsigned value)
{
    return api->fd_write((uint8_t)reg, (uint16_t)value) == 0 &&
           api->fd_read((uint8_t)reg) == (uint16_t)value ? 0 : -1;
}
static int wait_until(const k6_api_t *api, uint32_t deadline)
{
    while ((int32_t)(api->ms() - deadline) < 0) {
        if (api->key_down()) return -4;
        api->delay_us(200);
    }
    return (int32_t)(api->ms() - deadline) <= 2 ? 0 : -8;
}
#endif

int k6_main(const k6_api_t *api)
{
    if (!api || api->abi != 1 || api->level < 3 ||
        api->fd_read(0) != 0x4829u) return -3;
#if !W8_BENCH_ENABLE
    api->say("whisper8 inert; bench build required");
    return -9;
#else
    uint16_t old70 = 0, old71 = 0, old51 = 0, old40 = 0;
    uint32_t t0 = 0, i;
    int ret = 0, keyed = 0, snapshot = 0, restored = 1;

    if (api->rx_hz() != W8_EXPECTED_HZ || api->key_down()) return -2;
    if (KM_RSSI_DBM(api->fd_read(0x67)) > -90) return -7;
    api->delay_us(50000);
    if (KM_RSSI_DBM(api->fd_read(0x67)) > -90) return -7;

    if (api->tx_on(W8_LEASE_MS) != 0) return -5;
    keyed = 1;
    if (api->tx_pa(0) != 0) { ret = -12; goto done; }
    old70 = api->fd_read(0x70);
    old71 = api->fd_read(0x71);
    old51 = api->fd_read(0x51);
    old40 = api->fd_read(0x40);
    snapshot = 1;
    if (old51 != 0u || old40 != 0x3516u) { ret = -13; goto done; }
    if (wr(api, 0x70u, 0u) != 0 ||
        wr(api, 0x71u, w8_fixture_words[0]) != 0 ||
        wr(api, 0x70u, W8_TONE_GAIN) != 0) {
        ret = -12;
        goto done;
    }
    api->delay_us(50000u);
#if W8_RF_ENABLE
    if (api->tx_pa(1) != 0) { ret = -12; goto done; }
#endif
    t0 = api->ms() + 30u;
    for (i = 0; i < W8_FIXTURE_COUNT; ++i) {
        ret = wait_until(api, t0 + i * W8_SYMBOL_MS);
        if (ret != 0) goto done;
        if (api->key_down() ||
            wr(api, 0x71u, w8_fixture_words[i]) != 0) {
            ret = -4;
            goto done;
        }
    }
    ret = wait_until(api, t0 + W8_FIXTURE_COUNT * W8_SYMBOL_MS);
    if (ret == 0) ret = W8_FIXTURE_COUNT;
done:
    if (keyed) {
        (void)api->tx_pa(0);
        if (snapshot) {
            if (wr(api, 0x70, 0) != 0) restored = 0;
            if (wr(api, 0x71, old71) != 0) restored = 0;
            if (wr(api, 0x70, old70) != 0) restored = 0;
            if (api->fd_read(0x51) != old51 ||
                api->fd_read(0x40) != old40) restored = 0;
        }
        api->tx_off();
    }
    if (!restored) return -16;
    api->say(ret == W8_FIXTURE_COUNT ? "whisper8 fixture complete" :
                                       "whisper8 aborted; TX off");
    return ret;
#endif
}
