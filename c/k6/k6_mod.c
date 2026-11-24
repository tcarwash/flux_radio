/* k6_mod: run a small native module from the K6's RAM, sent over the air (see k6_mod.h).
   SPDX-License-Identifier: MIT */
#include "k6_mod.h"
#include "k6_lib.h"
#include "sotto_pager.h"
#include "cwid.h"
#include "xdmr.h"
#include <stddef.h>

/* API level 17: keep shared signal algorithms in the base image. Module state remains
   caller-owned, while the decoder implementation and the standard tone tables are shared. */
void km_cw_init(void *state) { if (state) cw_init((cw_t *)state); }
int km_cw_feed(void *state, uint32_t ms, int keyed) {
  return state ? cw_feed((cw_t *)state, ms, keyed) : -1;
}
int km_cw_flush(void *state, uint32_t ms) {
  return state ? cw_flush((cw_t *)state, ms) : -1;
}
int km_cw_call(const char *text, char *call, uint8_t *flags) {
  return text && call && flags ? cw_call(text, call, flags) : 0;
}
uint16_t km_tone_decode(uint16_t r68, uint16_t r69, uint16_t r6a) {
  return xd_tone_decode(r68, r69, r6a);
}

/* The key: -DKM_KEY=0x12,0x34,... (16 bytes, tools/k6_build.sh), or else a placeholder that
   `python -m modem.k6 keyimage` overwrites in a built .bin (so a CI image can be keyed without
   a toolchain). While the placeholder is there, every module is refused. Read through a
   volatile pointer so the compiler can't fold it into the code, where keyimage couldn't
   find it. */
#ifndef KM_CABLE_ONLY
#define KM_PLACEHOLDER 's', 'o', 't', 't', 'o', '-', 'K', '6', 'M', 'O', 'D', '-', 'k', 'e', 'y', '!'
#define X(c) (uint8_t)((c) ^ 0xFF)  /* stored inverted, so the placeholder is in the image once */
static const uint8_t placeholder_inv[16] = {X('s'), X('o'), X('t'), X('t'), X('o'), X('-'), X('K'), X('6'),
                                            X('M'), X('O'), X('D'), X('-'), X('k'), X('e'), X('y'), X('!')};
#ifdef KM_KEY
static const uint8_t key_store[16] = {KM_KEY};
#else
__attribute__((used)) static const uint8_t key_store[16] = {KM_PLACEHOLDER};
#endif

#endif

static km_status_t K;
static uint8_t seen[32];        /* a bit per chunk: up to 255 */
static uint8_t ready;
static uint32_t crc_diag;        /* last packet: calculated CRC in high half, received in low */

uint32_t km_crc_diag(void) { return crc_diag; }
uint32_t km_serial_highwater(void) { return K.serial; }
void km_init(void) {
  K.xfer = K.have = K.count = K.active = 0;
  K.last = K.chunk = 0;
  K.code_len = 0;
  K.serial = 0;
  K.reply = K.tlm = K.missing = K.pad = 0;
  for (unsigned i = 0; i < sizeof seen; i++) seen[i] = 0;
  ready = 0;
  crc_diag = 0;
}

#ifndef KM_CABLE_ONLY
static uint64_t le64(const uint8_t *p) {
  uint64_t v = 0;
  int i;
  for (i = 7; i >= 0; i--) v = v << 8 | p[i];
  return v;
}

#define ROTL(x, b) (((x) << (b)) | ((x) >> (64 - (b))))
static void rounds(uint64_t *v, int n) {
  while (n--) {
    v[0] += v[1], v[1] = ROTL(v[1], 13), v[1] ^= v[0], v[0] = ROTL(v[0], 32);
    v[2] += v[3], v[3] = ROTL(v[3], 16), v[3] ^= v[2];
    v[0] += v[3], v[3] = ROTL(v[3], 21), v[3] ^= v[0];
    v[2] += v[1], v[1] = ROTL(v[1], 17), v[1] ^= v[2], v[2] = ROTL(v[2], 32);
  }
}

uint64_t km_siphash(const uint8_t k[16], const uint8_t *m, unsigned n) {
  uint64_t k0 = le64(k), k1 = le64(k + 8), b = (uint64_t)n << 56, mi;
  uint64_t v[4];
  unsigned i, j;
  v[0] = k0 ^ 0x736f6d6570736575ULL, v[1] = k1 ^ 0x646f72616e646f6dULL;
  v[2] = k0 ^ 0x6c7967656e657261ULL, v[3] = k1 ^ 0x7465646279746573ULL;
  for (i = 0; i + 8 <= n; i += 8) {
    mi = le64(m + i);
    v[3] ^= mi, rounds(v, 2), v[0] ^= mi;
  }
  for (j = 0; i + j < n; j++) b |= (uint64_t)m[i + j] << (8 * j);
  v[3] ^= b, rounds(v, 2), v[0] ^= b;
  v[2] ^= 0xff, rounds(v, 4);
  return v[0] ^ v[1] ^ v[2] ^ v[3];
}

/* Fixed-domain cable challenge, never a general-purpose module signing oracle.
   Input is exactly K6CP v2's 16-byte record plus a 16-byte host nonce. */
int km_capability_proof(const uint8_t record[16], const uint8_t nonce[16], uint8_t tag[8]) {
  uint8_t msg[40] = {'K','6','C','A','P','A','U','1'};
  uint8_t key[16];
  const volatile uint8_t *kp = key_store;
  unsigned i, same = 1;
  uint64_t mac;
  for (i = 0; i < 16; i++) {
    key[i] = kp[i];
    same &= key[i] == (uint8_t)~placeholder_inv[i];
    msg[8+i] = record[i];
    msg[24+i] = nonce[i];
  }
  if (same) return KM_NO_KEY;
  mac = km_siphash(key, msg, sizeof msg);
  for (i = 0; i < 8; i++) tag[i] = (uint8_t)(mac >> (8*i));
  return 0;
}

#else
static uint32_t image_crc(const uint8_t *p,unsigned n) {
  uint32_t c=0xffffffffu;
  while(n--){unsigned i;c^=*p++;for(i=0;i<8;i++)c=(c>>1)^(0xedb88320u & (0u-(c&1)));}
  return ~c;
}
int km_capability_proof(const uint8_t record[16],const uint8_t nonce[16],uint8_t tag[8]){
  (void)record;(void)nonce;(void)tag;return KM_NO_KEY;
}
#endif

/* the whole image is in: is it a module of ours? */
static int check(const uint8_t *a, unsigned have_bytes, unsigned dl) {
  unsigned code = a[4] | a[5] << 8, entry = a[6] | a[7] << 8, total = KM_HDR + code + KM_TAG;
  uint32_t serial = (uint32_t)a[8] | (uint32_t)a[9] << 8 | (uint32_t)a[10] << 16 | (uint32_t)a[11] << 24;
  if (a[0] != 'K' || a[1] != '6' || a[2] != 'M' || a[3] != KM_ABI || a[12] > KM_LEVEL ||
      total > have_bytes || total + dl <= have_bytes || entry >= code || (entry & 1))
    return KM_BAD_HDR;
  {
    int t = km_tag_check(a, code);
    if (t) return t;
  }
  if (serial <= K.serial) return KM_OLD;
  km_arm(a, 1);
  /* an image for the SPI library (k6_lib.h): the caller writes it to its slot; it never runs
     from here, even on a caller that doesn't know the flag */
  if (a[13] & KM_F_LIB) ready = 0;
  return KM_READY;
}

/* Live RAM upload: an exact complete standalone image, never a library
   mutation or resident callback. Keep the boot-local replay watermark. */
int km_live_verify(const uint8_t *a, unsigned n) {
  int r;
  km_reset();
  if (n < KM_HDR + KM_TAG + 1 || a[12] < 1 || a[12] > 2 ||
      a[13] || a[14] || a[15]) return KM_BAD_HDR;
  r = check(a, n, 1);
  if (r != KM_READY) return r;
  km_arm(a, 0); /* no RF/ARQ report, clear reply and partial transfer state */
  return K.code_len;
}

/* Exact typed foreground library mutation. Reuse tag/replay checks;
   never arm this envelope to execute. Packed storage performs the write. */
int km_live_lib_verify(const uint8_t *a, unsigned n) {
  int r;
  unsigned slot, code;
  km_reset();
  if (n < KM_HDR + KM_TAG + 1 || n > 10240 || a[12] < 1 || a[14])
    return KM_BAD_HDR;
  slot = a[15];
  if (!(slot <= 8 || (slot >= 16 && slot < KL_SLOTS))) return KM_BAD_HDR;
  code = a[4] | a[5] << 8;
  if (a[13] == (KM_F_LIB | KM_F_REMOVE)) {
    static const uint8_t del[7] = {'K','6','E','R','A','S','E'};
    if (code != 7 || a[6] || a[7] || n != KM_HDR + 7 + KM_TAG) return KM_BAD_HDR;
    for (unsigned i=0;i<7;i++) if(a[KM_HDR+i]!=del[i]) return KM_BAD_HDR;
  } else if (a[13] != KM_F_LIB) return KM_BAD_HDR;
  r=check(a,n,1);
  if (r==KM_READY) km_arm(a,0); /* clear any stale RF report state */
  km_reset();
  return r==KM_READY ? (int)code : r;
}

int km_tag_check(const uint8_t *a, unsigned code) {
#ifdef KM_CABLE_ONLY
  const uint8_t *t=a+KM_HDR+code;
  uint32_t c=image_crc(a,KM_HDR+code);
  unsigned i;
  for(i=0;i<4;i++)if(t[i]!=(uint8_t)(c>>(8*i))||t[4+i]!=(uint8_t)(~c>>(8*i)))return KM_BAD_TAG;
  return 0;
#else
  const volatile uint8_t *kp = key_store;
  uint8_t key[16];
  int i, same = 1;
  for (i = 0; i < 16; i++) key[i] = kp[i], same &= key[i] == (uint8_t)~placeholder_inv[i];
  if (same) return KM_NO_KEY;
  if (km_siphash(key, a, KM_HDR + code) != le64(a + KM_HDR + code)) return KM_BAD_TAG;
  return 0;
#endif
}

void km_arm(const uint8_t *a, int air) {
  uint32_t serial = (uint32_t)a[8] | (uint32_t)a[9] << 8 | (uint32_t)a[10] << 16 | (uint32_t)a[11] << 24;
  if (serial > K.serial) K.serial = serial;   /* a slot's older serial never reopens a replay */
  K.code_len = (uint16_t)(a[4] | a[5] << 8);
  K.tlm = a[14] > KM_TLM_MAX ? KM_TLM_MAX : a[14];
  if (!air) K.reply = 0, K.tlm = 0, K.active = 0;   /* from the library: no upload to answer */
  ready = 1;
}

int km_again(const uint8_t *a, unsigned cap) {
  unsigned code = a[4] | a[5] << 8;
  int t;
  /* the tag covers the header, so it checked on arrival; a library image or a code page never
     runs from here */
  if (KM_HDR + code + KM_TAG > cap || a[13]) return KM_BAD_HDR;
  t = km_tag_check(a, code);
  if (t) return t;
  km_arm(a, 0);
  return (int)code;
}

static int packet(const uint8_t *payload, int len, void *arena, unsigned arena_bytes) {
  uint8_t d[SP_PAYLOAD_MAX], *a = (uint8_t *)arena;
  const uint8_t *w;
  unsigned dl = (unsigned)len - 6u, off, n, i, idx, cnt;
  if (len != SP_PAYLOAD_L && len != SP_PAYLOAD_XL) return KM_NOT_MINE;
  w = sp_pn9();
  if ((payload[0] ^ w[0]) != KM_MAGIC && (payload[0] ^ w[0]) != KM_MAGIC_ARQ) return KM_NOT_MINE;
  for (i = 0; i < (unsigned)len; i++) d[i] = payload[i] ^ w[i];
  K.chunk = d[2];
  crc_diag = (sp_crc16(d, len - 2) << 16) | ((unsigned)d[len - 2] << 8 | d[len - 1]);
  if ((uint16_t)(crc_diag >> 16) != (uint16_t)crc_diag) return K.last = KM_BAD_CRC;
  idx = d[2], cnt = d[3], off = idx * dl;
  if (!cnt || idx >= cnt || (cnt - 1u) * dl >= arena_bytes) return K.last = KM_BAD_CHUNK;
  if (!K.active && d[1] == K.xfer && cnt == K.count) return K.last = KM_DUP;   /* done already */
  if (!K.active || d[1] != K.xfer || cnt != K.count) {
    /* a new transfer: the pager's store is ours now, so leave it empty and harmless */
    sp_init(arena, 0);
    for (i = 0; i < sizeof seen; i++) seen[i] = 0;
    K.xfer = d[1], K.count = (uint8_t)cnt, K.have = 0, K.active = 1, ready = 0;
    K.reply = 0, K.tlm = 0, K.missing = (uint8_t)cnt;
  }
  if (d[0] == KM_MAGIC_ARQ) K.reply = 1;
  if (seen[idx >> 3] >> (idx & 7) & 1) return K.last = KM_DUP;
  seen[idx >> 3] |= (uint8_t)(1u << (idx & 7));
  n = arena_bytes - off < dl ? arena_bytes - off : dl;
  for (i = 0; i < n; i++) a[off + i] = d[4 + i];
  K.missing = (uint8_t)(cnt - ++K.have);
  if (K.have < cnt) {
    /* Leave the C return path free of diagnostic callbacks: a Rust cable frame emitted here
       obscures whether the original result survives the callback boundary. */
    K.last = KM_CHUNK;
    return KM_CHUNK;
  }
  K.active = 0;
  n = (cnt - 1u) * dl + n;      /* bytes of image held */
  return K.last = (int8_t)check(a, n, dl);
}

/* Separate physical cable and RF ingress; no sticky session permission. */
int km_packet(const uint8_t *payload,int len,void *arena,unsigned arena_bytes){
#ifdef KM_CABLE_ONLY
  (void)payload;(void)len;(void)arena;(void)arena_bytes;
  return KM_NOT_MINE;
#else
  return packet(payload,len,arena,arena_bytes);
#endif
}
int km_cable_packet(const uint8_t *payload,int len,void *arena,unsigned arena_bytes){
  return packet(payload,len,arena,arena_bytes);
}

int km_run(void *arena, const k6_api_t *api) {
  const uint8_t *a = (const uint8_t *)arena;
  if (!ready) return -100;
  ready = 0;
  /* Defense in depth: a resident callback never has the foreground API ABI. */
  if (a[13] & KM_F_RESIDENT) return KM_BAD_HDR;
#if defined(__thumb__)
  {
    typedef int (*entry_t)(const k6_api_t *);
    entry_t f = (entry_t)((uintptr_t)(a + KM_HDR + (a[6] | a[7] << 8)) | 1u);
    __asm volatile("dsb\n\tisb" ::: "memory");
    return f(api);
  }
#else
  (void)a, (void)api;
  return -100;
#endif
}

#if defined(__thumb__)
/* The compact Aura host has little free MSP at its module call site. Its LCD page buffer is
   unused for the whole module run (the lean host disables the module LCD), so it lends that
   buffer as an isolated module stack. Keep this as a naked ABI trampoline: a compiler prologue
   on the old stack would defeat the switch, and the saved r3 is restored only after km_run. */
__attribute__((naked)) int km_run_on_stack(void *arena, const k6_api_t *api, void *stack_top) {
  __asm volatile("push {r0, r1, r2, lr}\n"
                 "ldr r3, [sp, #12]\n"
                 "pop {r0, r1, r2}\n"
                 "add sp, #4\n"
                 "mov lr, r3\n"
                 "mov r3, sp\n"
                 "mov sp, r2\n"
                 "push {r3, lr}\n"
                 "bl km_run\n"
                 "pop {r3}\n"
                 "ldr r2, [sp]\n"
                 "add sp, #4\n"
                 "mov sp, r3\n"
                 "bx r2\n");
}

/* Run host callbacks on their own stack, preserving the module's live stack frames. */
__attribute__((naked)) void km_call_host_stack(void (*fn)(void *), void *arg, void *stack_top) {
  __asm volatile("push {r4, lr}\n"
                 "mov r4, sp\n"
                 "mov r3, r0\n"
                 "mov r0, r1\n"
                 "mov sp, r2\n"
                 "blx r3\n"
                 "mov sp, r4\n"
                 "pop {r4, pc}\n");
}

/* Boot constructs Aura's large App value while the normal MSP has too little headroom.
   Borrow the still-unused module arena as a temporary descending stack, then restore MSP. */
__attribute__((naked)) void km_call_on_stack(void *stack_top, void (*fn)(void *), void *arg) {
  __asm volatile("push {r4, lr}\n"
                 "mov r4, sp\n"
                 "mov sp, r0\n"
                 "mov r0, r2\n"
                 "blx r1\n"
                 "mov sp, r4\n"
                 "pop {r4, pc}\n");
}

/* The cable loop can arrive with less than km_packet's stack frame free above .bss. Switch
   this one call to the linker-reserved main stack at SRAM top. */
__attribute__((naked)) int km_packet_on_stack(const uint8_t *payload, int len, void *arena,
                                               unsigned arena_bytes) {
  __asm volatile("push {r4, lr}\n"
                 "mov r4, sp\n"
                 "ldr r3, =_stack_start\n"
                 "mov sp, r3\n"
                 "push {r4, lr}\n"
                 "bl km_cable_packet\n"
                 "pop {r4}\n"
                 "add sp, #4\n"
                 "mov sp, r4\n"
                 "pop {r4, pc}\n");
}
#endif

#if !defined(__thumb__)
int km_packet_on_stack(const uint8_t *payload, int len, void *arena, unsigned arena_bytes) {
  return km_cable_packet(payload, len, arena, arena_bytes);
}
#endif

void km_reset(void) { K.active = 0, ready = 0; }
void km_cable_begin(void) {
#ifdef SP_PKT_YIELD
  /* Cable mode has RF off. Clear the FSK receiver callback so CRC's optional yield hook cannot
     call a stale or uninitialized receiver context while a module is being uploaded. */
  sp_set_packet_yield(0, 0);
#endif
  km_reset();
  /* The cable session has RF disabled and always ends in a reboot. Start a fresh
     replay epoch here so stale or uninitialized RAM can never reject wired installs. */
  K.serial = 0;
}
int km_armed(void) { return ready; }

static void report_seal(uint8_t *out, uint8_t kind, uint8_t seq, uint8_t part, uint8_t parts) {
  const uint8_t *w = sp_pn9();
  unsigned c, i;
  out[0] = KM_MAGIC_REPORT, out[1] = kind, out[2] = seq, out[3] = part, out[4] = parts;
  c = sp_crc16(out, 62);
  out[62] = (uint8_t)(c >> 8), out[63] = (uint8_t)c;
  for (i = 0; i < 64; i++) out[i] ^= w[i];
}

static void le32(uint8_t *p, uint32_t v) {
  p[0] = (uint8_t)v, p[1] = (uint8_t)(v >> 8), p[2] = (uint8_t)(v >> 16), p[3] = (uint8_t)(v >> 24);
}

void km_report_status(uint8_t out[64], uint8_t seq, uint8_t parts, uint8_t state, const char *call,
                      int32_t ret, uint32_t ms, uint16_t stack_free) {
  unsigned i;
  for (i = 0; i < 64; i++) out[i] = 0;
  out[5] = K.xfer, out[6] = K.have, out[7] = K.count, out[8] = (uint8_t)K.last, out[9] = state;
  le32(out + 10, state == KM_ST_PARTWAY ? 0 : K.serial);
  if (state == KM_ST_PARTWAY)
    for (i = 0; i < K.count; i++)
      if (!(seen[i >> 3] >> (i & 7) & 1)) out[14 + (i >> 3)] |= (uint8_t)(1u << (i & 7));
  for (i = 0; i < 6 && call[i]; i++) out[46 + i] = (uint8_t)call[i];
  le32(out + 52, (uint32_t)ret);
  le32(out + 56, ms);
  out[60] = (uint8_t)stack_free, out[61] = (uint8_t)(stack_free >> 8);
  report_seal(out, KM_REP_STATUS, seq, 0, parts);
}

void km_report_text(uint8_t out[64], uint8_t seq, uint8_t part, uint8_t parts, const char *text,
                    unsigned n) {
  unsigned i;
  for (i = 0; i < 64; i++) out[i] = 0;
  for (i = 0; i < n && i < KM_TLM_PART; i++) out[5 + i] = (uint8_t)text[i];
  report_seal(out, KM_REP_TEXT, seq, part, parts);
}

const km_status_t *km_status(void) { return &K; }
