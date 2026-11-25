/* Cortex-M0 API31 Sotto-1200 receive + fixed-point decode + PCM/PWM module.
 * OFFLINE development/qualification fixture only.
 * Reuses existing c/fixed/sotto_fx_dec.c, c/fixed/sotto_fx_lr.c, fx_math.c,
 * fx_tables.c, c/k6/sotto_dv_wire.c and c/k6/sotto_pager.c conventions.
 * Never transmits, keys PA, or drives physical speaker DMA.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "sotto_m0rt.h"
#include "sotto_m0rt64.h"
#include "../sotto_dv_wire.c"

int memcmp(const void *a, const void *b, size_t n) {
    const unsigned char *p = (const unsigned char *)a;
    const unsigned char *q = (const unsigned char *)b;
    while (n--) {
        if (*p != *q) return (int)*p - (int)*q;
        p++;
        q++;
    }
    return 0;
}

typedef struct {
    uint32_t *mem;
    unsigned cap;
    unsigned used;
    unsigned peak;
    unsigned blocks;
    const k6_api_t *api;
    uint32_t cb_reads;
    unsigned cb_fault;
} sp_pool_t;

/* Position-independent RAM modules cannot contain writable globals.
 * R9 is reserved for this invocation and read with ordinary Thumb asm:
 * clang's global-register variable lowers to unsupported llvm.read_register. */
static inline sp_pool_t *rx_pool_get(void) {
    sp_pool_t *p;
    __asm volatile("mov %0, r9" : "=r"(p));
    return p;
}

void *sp_calloc(size_t n, size_t size) {
    size_t b = (n * size + 3u) & ~(size_t)3u;
    sp_pool_t *p = rx_pool_get();
    void *ptr;
    if (!p || !p->mem || b > p->cap - p->used) return 0;
    ptr = (uint8_t *)p->mem + p->used;
    memset(ptr, 0, b);
    p->used += (unsigned)b;
    if (p->used > p->peak) p->peak = p->used;
    p->blocks++;
    return ptr;
}

void sp_free(void *ptr) {
    sp_pool_t *p = rx_pool_get();
    if (p && ptr && p->blocks && --p->blocks == 0) p->used = 0;
}

#define SOTTO_CB_SIZE 30720u
#define SOTTO_CB_CRC 0xd5856ecdu

static uint32_t rx_crc32(uint32_t crc, const uint8_t *p, unsigned n) {
    unsigned i, j;
    for (i = 0; i < n; i++) {
        crc ^= p[i];
        for (j = 0; j < 8u; j++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}

static uint32_t rx_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int rx_cb_verify(const k6_api_t *a) {
    uint8_t h[32], buf[128];
    uint32_t off, crc = ~0u;
    if (!a || !a->resource_read) return -1;
    if (a->resource_read(0, 0, h, 32) != 0) return -1;
    if (h[0] != 'K' || h[1] != '6' || h[2] != 'C' || h[3] != 'B' ||
        h[4] != 1 || h[5] != 0 || h[6] != 20 || h[7] != 0 ||
        rx_le32(h + 8) != SOTTO_CB_SIZE ||
        h[12] != 4 || h[13] != 9 || h[14] != 9 || h[15] != 8 || h[16] != 8 ||
        rx_le32(h + 24) != SOTTO_CB_CRC ||
        ~rx_crc32(~0u, h, 28) != rx_le32(h + 28))
        return -1;
    for (off = 0; off < SOTTO_CB_SIZE; off += sizeof(buf)) {
        unsigned k = SOTTO_CB_SIZE - off;
        if (k > sizeof(buf)) k = sizeof(buf);
        if (a->resource_read(0, 32u + off, buf, k) != 0) return -1;
        crc = rx_crc32(crc, buf, k);
    }
    return (~crc == SOTTO_CB_CRC) ? 0 : -1;
}

const int16_t *fx_cb_fetch(int s, int i, int16_t *buf) {
    static const uint16_t base[4] = {0, 512, 1024, 1280};
    uint8_t b[20];
    sp_pool_t *p = rx_pool_get();
    int k;
    if (!p || !p->api || p->cb_fault || s < 0 || s > 3 || i < 0 || i >= (s < 2 ? 512 : 256)) {
        if (p) p->cb_fault = 1;
        for (k = 0; k < 10; k++) buf[k] = 0;
        return buf;
    }
    if (p->api->resource_read(0, 32u + (base[s] + (uint32_t)i) * 20u, b, 20u) != 0) {
        p->cb_fault = 1;
        for (k = 0; k < 10; k++) buf[k] = 0;
        return buf;
    }
    for (k = 0; k < 10; k++)
        buf[k] = (int16_t)((uint16_t)b[2 * k] | ((uint16_t)b[2 * k + 1] << 8));
    p->cb_reads++;
    return buf;
}

#include "sotto.h"
#include "fx.h"
#include "fx_tables.c"
#include "fx_math.c"
#include "sotto_fx_dec.c"
/* Both upstream translation units use a file-local unpack() helper. */
#define unpack sotto_lr_unpack
#include "sotto_fx_lr.c"
#undef unpack

#define RX_HDR_MAGIC 0x5258504Cu /* 'RXPL' */
#define RX_POOL_BYTES 3200u      /* SP_DEC_CORE (2872) + sotto_lr_dec (300) = 3172 */
#define RX_PWM_TOP 2000u
#define RX_PWM_GAIN_Q8 256
#define RX_PWM_EMPHASIS 1
#define RX_LR_SKIP (2 * SOTTO_LR_DEC_DELAY)

typedef struct {
    uint32_t magic;
    uint32_t emit_pc;
    uint32_t seq;
    uint32_t pcm_hash;
    uint32_t pcm_samples;
    uint32_t pwm_hash;
    uint32_t frames_decoded;
    uint32_t scratch_room;
    uint32_t scratch_used;
    uint32_t pool_peak;
    uint32_t pool_blocks_end;
    uint32_t stack_reserve;
    uint32_t stack_used;
    uint32_t cb_reads;
    uint16_t fifo[34];
    int16_t pcm[160];
    uint16_t pwm[160];
} rx_hdr_t;

__attribute__((noinline))
static void rx_pcm_emit(rx_hdr_t *h) {
    __asm volatile("" :: "r"(h) : "memory");
}

static uint32_t rx_hash16(uint32_t h, const uint16_t *v, int n) {
    int i;
    for (i = 0; i < n; i++) {
        unsigned x = v[i];
        h = (h ^ (x & 0xFFu)) * 16777619u;
        h = (h ^ (x >> 8)) * 16777619u;
    }
    return h;
}

/* Same filter and scaling as c/k6/sotto_pager.c sp_pcm_to_pwm(), with caller-owned history. */
static void rx_pcm_to_pwm(const int16_t *pcm, uint16_t *out, int n,
                          unsigned top, int gain_q8, int emphasis,
                          int32_t *emph1, int32_t *emph2) {
    int i;
    int32_t half = (int32_t)(top / 2u);
    for (i = 0; i < n; i++) {
        int32_t x = pcm[i], y = x, t;
        if (emphasis) {
            y = (33 * x - 21 * *emph1 + 4 * *emph2) >> 4;
            *emph2 = *emph1;
            *emph1 = x;
        }
        t = (y * gain_q8) >> 8;
        if (t > 32767) t = 32767;
        if (t < -32767) t = -32767;
        t = half + ((t * half) >> 15);
        out[i] = (uint16_t)(t < 0 ? 0 : t > (int32_t)top ? (int32_t)top : t);
    }
}

static void rx_emit_block(rx_hdr_t *h, int32_t *emph1, int32_t *emph2) {
    h->pcm_hash = rx_hash16(h->pcm_hash, (const uint16_t *)h->pcm, 160);
    rx_pcm_to_pwm(h->pcm, h->pwm, 160, RX_PWM_TOP, RX_PWM_GAIN_Q8,
                  RX_PWM_EMPHASIS, emph1, emph2);
    h->pwm_hash = rx_hash16(h->pwm_hash, h->pwm, 160);
    h->pcm_samples += 160u;
    h->seq++;
    rx_pcm_emit(h);
}

__attribute__((used, noinline))
static int rx_main(const k6_api_t *a, rx_hdr_t *h) {
    uint32_t timeout, count = 0, segments = 0, frames = 0, tag_h = 0, tag_l = 0;
    uint8_t next = 0, first = 0, state = 0, payload[48], id = 0;
    unsigned n = 0, off = 0, co = 0, i;
    int32_t emph1 = 0, emph2 = 0;
    int skip = RX_LR_SKIP;
    int r, result = -8;
    dv_session_ctrl_t c;
    struct sotto_lr_dec *dec = 0;

    if (rx_cb_verify(a) != 0) return -13;
    dec = sotto_lr_dec_create();
    if (!dec) return -14;
    if (a->fsk4800_rx(0, 0, 4) != 0) {
        sotto_lr_dec_destroy(dec);
        return -5;
    }

    timeout = a->ms() + 4500u;
    while ((int32_t)(timeout - a->ms()) > 0 && count < 144u + 2u) {
        if (a->key_down()) { result = -6; break; }
        r = a->fsk4800_rx(1, h->fifo, 0);
        if (r < 0) { result = -7; break; }
        if (r == 0) { a->delay_us(500); continue; }
        if (r != 33) { result = -9; break; }
        count++;
        if (flux_dv_wire_session_rx66((const uint8_t *)h->fifo, &c, &co)) {
            if (c.kind == DV_SESSION_BEGIN && !state && (c.tag_hi | c.tag_lo)) {
                tag_h = c.tag_hi;
                tag_l = c.tag_lo;
                first = c.first_id;
                next = first;
                state = 1;
            } else if (c.kind == DV_SESSION_END && state == 1 &&
                       c.tag_hi == tag_h && c.tag_lo == tag_l &&
                       segments == c.segments &&
                       c.first_id == first &&
                       c.last_id == (uint8_t)(next - 1u) &&
                       c.last_frames == frames &&
                       segments > 0) {
                int half = 0;
                while (sotto_lr_dec_flush_half(dec, half, h->pcm)) {
                    if (rx_pool_get()->cb_fault) { result = -15; break; }
                    if (skip > 0) skip--;
                    else rx_emit_block(h, &emph1, &emph2);
                    half = !half;
                }
                if (result == -15) break;
                state = 2;
                result = (int)count;
                break;
            } else {
                result = -10;
                break;
            }
        } else if (flux_dv_wire_rx66((const uint8_t *)h->fifo, payload, &id, &n, &off) &&
                   state == 1 && id == next && n >= 1u && n <= 8u) {
            next++;
            segments++;
            frames = n;
            for (i = 0; i < n; i++) {
                int half;
                for (half = 0; half < 2; half++) {
                    sotto_lr_decode_half(dec, payload + 6u * i, 1, half, h->pcm);
                    if (rx_pool_get()->cb_fault) { result = -15; break; }
                    if (skip > 0) skip--;
                    else rx_emit_block(h, &emph1, &emph2);
                }
                if (result == -15) break;
                h->frames_decoded++;
            }
            if (result == -15) break;
        } else {
            result = -11;
            break;
        }
        if (a->fsk4800_rx(0, 0, 4) != 0) { result = -12; break; }
    }

    a->fsk4800_rx(2, 0, 0);
    sotto_lr_dec_destroy(dec);
    memset(h->pcm, 0, sizeof(h->pcm));
    memset(h->pwm, 0, sizeof(h->pwm));
    h->pool_peak = rx_pool_get()->peak;
    h->pool_blocks_end = rx_pool_get()->blocks;
    h->cb_reads = rx_pool_get()->cb_reads;
    return result;
}

__attribute__((naked, noinline))
static int rx_call_on_stack(void *top, const k6_api_t *a, rx_hdr_t *h) {
    __asm volatile(
        "push {r4, lr}\n"
        "mov r4, sp\n"
        "mov sp, r0\n"
        "mov r0, r1\n"
        "mov r1, r2\n"
        "bl rx_main\n"
        "mov sp, r4\n"
        "pop {r4, pc}\n"
    );
}

int k6_main(const k6_api_t *a) {
    uint32_t room = 0, hdr_bytes, pool_off, stack_off, stack_bytes = 0, i;
    uint8_t *scratch;
    rx_hdr_t *h;
    sp_pool_t pool;
    void *saved_r9;
    int rc;

    if (!a || a->level < 31 || a->abi != 1 || !a->fsk4800_rx ||
        !a->scratch || !a->resource_read)
        return -3;
    scratch = (uint8_t *)a->scratch(&room);
    hdr_bytes = (uint32_t)((sizeof(rx_hdr_t) + 3u) & ~3u);
    if (!scratch || ((uintptr_t)scratch & 3u) || room < hdr_bytes + RX_POOL_BYTES)
        return -4;

    h = (rx_hdr_t *)scratch;
    memset(h, 0, sizeof(*h));
    h->magic = RX_HDR_MAGIC;
    h->emit_pc = ((uint32_t)(uintptr_t)rx_pcm_emit) & ~1u;
    h->pcm_hash = 2166136261u;
    h->pwm_hash = 2166136261u;
    h->scratch_room = room;

    pool_off = hdr_bytes;
    stack_off = pool_off + RX_POOL_BYTES;
    if (room >= stack_off + 1024u) {
        stack_bytes = (room - stack_off) & ~7u;
        if (stack_bytes > 2048u) stack_bytes = 2048u;
    }
    h->scratch_used = stack_off + stack_bytes;
    h->stack_reserve = stack_bytes;

    memset(&pool, 0, sizeof(pool));
    pool.mem = (uint32_t *)(scratch + pool_off);
    pool.cap = RX_POOL_BYTES;
    pool.api = a;

    __asm volatile("mov %0, r9" : "=r"(saved_r9));
    __asm volatile("mov r9, %0" :: "r"(&pool) : "memory");

    if (stack_bytes) {
        uint32_t *stk = (uint32_t *)(scratch + stack_off);
        uint32_t words = stack_bytes / 4u;
        for (i = 0; i < words; i++) stk[i] = 0xA5A5A5A5u;
        rc = rx_call_on_stack(scratch + stack_off + stack_bytes, a, h);
        for (i = 0; i < words; i++) {
            if (stk[i] != 0xA5A5A5A5u) break;
        }
        h->stack_used = (words - i) * 4u;
    } else {
        rc = rx_main(a, h);
    }

    __asm volatile("mov r9, %0" :: "r"(saved_r9) : "memory");
    return rc;
}
