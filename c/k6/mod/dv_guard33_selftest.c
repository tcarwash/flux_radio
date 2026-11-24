/* Flux DV: receive-idle guarded Sotto packer self-test.
 * Checks Cortex-M0-compatible module can pack full and short messages
 * byte-for-byte identical to Python modem/pager.py golden arrays.
 * SYNTHETIC bytes only. No PTT, chip, GPIO, ADC, RF or SPI access;
 * installing via serial still requires an exclusive shared queue lease.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "dv_guard33_stream.h"

static const uint16_t a_golden[33] = {
    0x9156u, 0x9addu, 0x8dedu, 0x3030u, 0x4ccfu, 0x6195u,
    0xed19u, 0x96dcu, 0xc3f9u, 0x38e2u, 0x0f9cu, 0xaba9u,
    0x1f52u, 0xca90u, 0xbec6u, 0xb451u, 0x9d8du, 0x2f4cu,
    0xfe93u, 0x2184u, 0x8acfu, 0xfe93u, 0xd4dcu, 0x9db3u,
    0x1a8du, 0xd52bu, 0xb3d7u, 0xcd8au, 0xd1e7u, 0x094eu,
    0x1732u, 0x2631u, 0xa55au,
};
static const uint16_t b_golden[33] = {
    0x9056u, 0x9addu, 0x87edu, 0x3e34u, 0x3ac7u, 0x5f81u,
    0x1b09u, 0xb8c8u, 0xa591u, 0xd82du, 0x0d6du, 0x8fbau,
    0x5967u, 0xa2c7u, 0x34bfu, 0x18cau, 0x5330u,
    0xdf93u, 0xec92u, 0x15a7u, 0xdc8au, 0x86f4u, 0x4e55u,
    0x2118u, 0xc440u, 0xd5c4u, 0x91c6u, 0xcd8au, 0xd1e7u,
    0x094eu, 0x1732u, 0xbdb2u, 0xa55au,
};

int k6_main(const k6_api_t *api) {
    dv_guard33_stream_t q;
    uint8_t six[6];
    const uint16_t *words;
    unsigned i,j;
    if (!api || api->abi != KM_ABI || api->level < K6_MODULE_LEVEL)
        return -1;
    if (dv_stream_begin(&q,112u) != DV_STREAM_OK) return -2;
    for (i = 0; i < 8; ++i) {
        for (j = 0; j < 6; ++j)
            six[j] = (uint8_t)(((i*6u+j)*17u+3u)&255u);
        if (dv_stream_push(&q,six) != (i == 7u ? DV_STREAM_COMMITTED : DV_STREAM_OK))
            return -3;
    }
    words=dv_stream_peek(&q);
    if (!words) return -4;
    for (i=0;i<33u;++i) if (words[i]!=a_golden[i]) return -5;
    if (dv_stream_ack(&q)!=DV_STREAM_OK) return -6;
    for (i = 0; i < 2; ++i) {
        for (j = 0; j < 6; ++j)
            six[j] = (uint8_t)(((i*6u+j)*19u+7u)&255u);
        if (dv_stream_push(&q,six)!=DV_STREAM_OK) return -7;
    }
    if (dv_stream_finish(&q)!=DV_STREAM_OK) return -8;
    words=dv_stream_peek(&q);
    if (!words) return -9;
    for (i=0;i<33u;++i) if (words[i]!=b_golden[i]) return -10;
    if (dv_stream_ack(&q)!=DV_STREAM_OK) return -11;
    if (dv_stream_peek(&q) || q.segments!=2u ||
        q.next_msg_id!=114u || q.filled!=0u || !q.ended) return -12;
    api->say("dv guard33 packet selftest PASS");
    return 33;
}
