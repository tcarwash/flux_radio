/* libls: list the K6's SPI module library (c/k6/k6_lib.h) with the modules' names, over the
   serial report and the upload's telemetry. The release plan's module list (Jeff 2026-11-13)
   shows the same names; this reads them on any image from level 5 (fl_read), before the core
   does:

     K6 MOD say LIB 3 code=5726 lvl=6 fl=01 EDTRIAL
     K6 MOD say LIB 9 code=3020 lvl=1 fl=03 PAGE
     K6 MOD say LIB 12 code=11264 lvl=1 fl=05 CARDS
     K6 MOD say LIB 17 code=900 lvl=2 fl=01 -              (a module without a name)
     K6 MOD say LIB 20 BAD                                  (not an image)
     K6 MOD say LIB used=5 of 32

   It reads the 32 slots' headers and name blocks (k6_lib.h), no tags, and writes nothing.
   Returns the slots used, -3 on an image below level 5. No libraries, no division, no writable
   statics. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 5
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../k6_lib.h"

K6_MODULE_NAME("LIBLS");

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* decimal without a divide */
static char *dec(char *p, uint32_t u) {
  static const uint32_t pw[] = {10000, 1000, 100, 10, 1};
  int i, started = 0;
  for (i = 0; i < 5; i++) {
    int d = 0;
    while (u >= pw[i]) u -= pw[i], d++;
    if (d || started || i == 4) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}

static char *hex2(char *p, unsigned v) {
  static const char H[] = "0123456789abcdef";
  *p++ = H[(v >> 4) & 15];
  *p++ = H[v & 15];
  return p;
}

/* Read-only directory parser. No change to the module ABI. */
static uint32_t le32(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static int directory(const k6_api_t *api, uint32_t base, uint8_t *d) {
  if (api->fl_read(base,d,1036)<0 || le32(d)!=0x324c554bu || le32(d+12)!=~le32(d+4)) return 0;
  uint32_t crc=~0u;
  for (unsigned i=12;i<1036;i++) {crc^=d[i];for(unsigned j=0;j<8;j++)crc=(crc>>1)^(0xedb88320u&-(crc&1));}
  if (~crc!=le32(d+8)) return 0;
  uint8_t used[126];
  for (volatile unsigned j=0;j<126;j++) used[j]=0;
  for(unsigned i=0;i<255;i++) {const uint8_t *e=d+16+4*i;unsigned n=e[2]|(unsigned)e[3]<<8;
    if(!n){if(e[0]||e[1])return 0;continue;}
    if(e[1]!=(n+4095u)/4096u || e[0]+e[1]>126)return 0;
    for(unsigned j=e[0];j<(unsigned)e[0]+e[1];j++) {if(used[j])return 0;used[j]=1;}
  }
  return 1;
}

int k6_main(const k6_api_t *api) {
  uint8_t h[KM_HDR + 4 + KL_NAME];
  char ln[64], *p;
  int n, i, used = 0;
  if (api->level < 5) return -3;
  uint8_t d[1036],h0[16],h1[16];
  if(api->fl_read(KL_BASE,h0,16)<0 || api->fl_read(KL_BASE+4096,h1,16)<0)return -3;
  int packed=le32(h0)==0x324c554bu || le32(h1)==0x324c554bu;
  if(packed){
    uint32_t first=KL_BASE,second=KL_BASE+4096;
    if(le32(h1)==0x324c554bu && (le32(h0)!=0x324c554bu || (int32_t)(le32(h1+4)-le32(h0+4))>0)){first=second;second=KL_BASE;}
    if(!directory(api,first,d)&&!directory(api,second,d)){api->say("LIB invalid directory");return -2;}
  }
  for (n = 0; n < KL_SLOTS; n++) {
    unsigned code, blank = 1, ok;
    uint32_t addr=KL_SLOT_ADDR(n);unsigned extent=KL_SLOT_BYTES;
    if(packed){const uint8_t*e=d+16+4*n;extent=e[2]|(unsigned)e[3]<<8;if(!extent)continue;addr=KL_BASE+8192u+4096u*e[0];}
    if(extent<KM_HDR)continue;
    if (api->fl_read(addr, h, KM_HDR) < 0) break;
    for (i = 0; i < KM_HDR; i++) blank &= h[i] == 0xFF;
    if (blank) continue;
    used++;
    p = put(ln, "LIB ");
    p = dec(p, (uint32_t)n);
    code = (unsigned)h[4] | (unsigned)h[5] << 8;
    ok = h[0] == 'K' && h[1] == '6' && h[2] == 'M' && h[3] == KM_ABI && code &&
         KM_HDR + code + KM_TAG <= extent;
    if (ok && code >= 4 + KL_NAME && !(h[13] & (KM_F_PAGE | KM_F_CARDS))) {
      if (api->fl_read(addr + KM_HDR, h + KM_HDR, 4 + KL_NAME) < 0) break;
    }
    if (!ok) {
      p = put(p, " BAD");
    } else {
      p = put(p, " code=");
      p = dec(p, code);
      p = put(p, " lvl=");
      p = dec(p, h[12]);
      p = put(p, " fl=");
      p = hex2(p, h[13]);
      *p++ = ' ';
      if (h[13] & KM_F_PAGE) {
        p = put(p, "PAGE");
      } else if (h[13] & KM_F_CARDS) {
        p = put(p, "CARDS");
      } else if (code >= 4 + KL_NAME && h[16] == 'K' && h[17] == '6' && h[18] == 'N' && h[19] == 'M' &&
                 h[20]) {
        for (i = 0; i < KL_NAME && h[20 + i]; i++) {
          char c = (char)h[20 + i];
          *p++ = c >= ' ' && c <= '~' ? c : '?';
        }
      } else {
        *p++ = '-';
      }
    }
    *p = 0;
    api->say(ln);
  }
  p = put(ln, "LIB used=");
  p = dec(p, (uint32_t)used);
  p = put(p, " of ");
  p = dec(p, KL_SLOTS);
  *p = 0;
  api->say(ln);
  return used;
}
