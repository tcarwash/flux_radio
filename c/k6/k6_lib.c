/* k6_lib: the K6's SPI module library (see k6_lib.h). SPDX-License-Identifier: MIT */
#include "k6_lib.h"
#ifdef KM_USER_STORE
#include "user_store.h"
#endif

int kl_locate(int slot,const kl_flash_t *fl,uint32_t *addr,unsigned *len){
 if(slot<0||slot>=KL_SLOTS)return KL_SLOT;
#ifdef KM_USER_STORE
 ku_dir_t d;int r=ku_open(&d,fl);if(r)return r;return ku_lookup(&d,slot,addr,len);
#else
 (void)fl;*addr=KL_SLOT_ADDR(slot);*len=KL_SLOT_BYTES;return 0;
#endif
}


static unsigned u16le(const uint8_t *p) { return (unsigned)p[0] | (unsigned)p[1] << 8; }

/* the header's own checks (k6_mod.c check() without the transfer): the image's length, or 0 */
static unsigned hdr_len(const uint8_t *a) {
  unsigned code = u16le(a + 4), entry = u16le(a + 6), total = KM_HDR + code + KM_TAG;
  if (a[0] != 'K' || a[1] != '6' || a[2] != 'M' || a[3] != KM_ABI || a[12] > KM_LEVEL || !code ||
      entry >= code || (entry & 1) || total > KL_SLOT_BYTES)
    return 0;
  return total;
}

int kl_install(const uint8_t *img, const kl_flash_t *fl) {
  unsigned total = hdr_len(img), slot = img[15], off, n;
  uint32_t base;
  uint8_t back[64];
  if (!total || !(img[13] & KM_F_LIB) || (img[13] & (KM_F_REMOVE | KM_F_THEME))) return KL_BAD_HDR;
  if (slot >= KL_SLOTS) return KL_SLOT;
#ifdef KM_USER_STORE
  ku_dir_t d;int r=ku_open(&d,fl);if(r)return r;
  r=ku_lookup(&d,slot,&base,&n);if(!r){if(fl->read(base,back,4))return KL_IO;if(back[0]=='K'&&back[1]=='6'&&back[2]=='T')return KL_NOT_MODULE;}else if(r!=KL_EMPTY)return r;
  r=ku_put(&d,fl,slot,img,total);return r?r:(int)total;
#else
  base = KL_SLOT_ADDR(slot);
  if (fl->read(base, back, 4)) return KL_IO;
  if (back[0]=='K' && back[1]=='6' && back[2]=='T') return KL_NOT_MODULE;
  /* only the sectors the image needs; a shorter image leaves 0xFF behind it in the last one */
  for (off = 0; off < total; off += KL_SECTOR)
    if (fl->erase(base + off)) return KL_IO;
  for (off = 0; off < total; off += n) {
    n = total - off < KL_PAGE ? total - off : KL_PAGE;
    if (fl->prog(base + off, img + off, n)) return KL_IO;
  }
  for (off = 0; off < total; off += n) {
    unsigned i;
    n = total - off < sizeof back ? total - off : (unsigned)sizeof back;
    if (fl->read(base + off, back, n)) return KL_IO;
    for (i = 0; i < n; i++)
      if (back[i] != img[off + i]) return KL_VERIFY;
  }
  return (int)total;
#endif
}

int kl_remove(int slot, const kl_flash_t *fl) {
  kl_info_t info;
  uint8_t check[64];
  uint32_t base;
  unsigned off, i;
  if (slot < 0 || slot >= KL_SLOTS) return KL_SLOT;
  if (kl_info(slot, fl, &info) != 0 || !(info.flags & KM_F_LIB) ||
      ((info.flags & (KM_F_PAGE | KM_F_CARDS)) && info.flags != KL_RESIDENT_FLAGS)) return KL_NOT_MODULE;
#ifdef KM_USER_STORE
  ku_dir_t d;int r=ku_open(&d,fl);return r?r:ku_remove(&d,fl,slot);
#else
  base = KL_SLOT_ADDR(slot);
  /* Clear the full slot, including stale data beyond the previous image's final sector. */
  for (off = 0; off < KL_SLOT_BYTES; off += KL_SECTOR)
    if (fl->erase(base + off)) return KL_IO;
  for (off = 0; off < KL_SLOT_BYTES; off += sizeof check) {
    if (fl->read(base + off, check, sizeof check)) return KL_IO;
    for (i = 0; i < sizeof check; i++)
      if (check[i] != 0xFF) return KL_VERIFY;
  }
  return 0;
#endif
}

/* Live updates are limited to regular foreground modules in KUL2.
   Never replace reserved, resident, page, theme or opaque objects. */
#ifdef KM_USER_STORE
int kl_live_mutate(const uint8_t *img, unsigned n, const kl_flash_t *fl) {
  unsigned slot, extent;
  uint32_t addr;
  kl_info_t info;
  int r=km_live_lib_verify(img,n);
  if(r<=0)return r-100; /* distinguish envelope validation from storage errors */
  slot=img[15];
  r=kl_locate(slot,fl,&addr,&extent);
  if(r==0) {
    r=kl_info(slot,fl,&info);
    if(r || !info.level || extent!=KM_HDR+info.code_len+KM_TAG) return KL_NOT_MODULE;
#if KM_LEVEL >= 27
    if(img[13] == (KM_F_LIB|KM_F_CARDS)) {
      uint8_t h[5];
      if(info.flags!=img[13] || info.code_len!=4182 ||
         fl->read(addr+KM_HDR,h,sizeof h) ||
         h[0]!='K'||h[1]!='6'||h[2]!='S'||h[3]!='C'||h[4]!=1)
        return KL_NOT_MODULE;
    } else
#endif
    if(info.flags!=KM_F_LIB) return KL_NOT_MODULE;
  } else if(r!=KL_EMPTY) return r;
  if(img[13] & KM_F_REMOVE) return r==KL_EMPTY ? KL_NOT_MODULE : kl_remove(slot,fl);
  return kl_install(img,fl);
}
#endif

int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out) {
  uint8_t h[KM_HDR + 4 + KL_NAME];
  unsigned i, blank = 1, named;
  if (slot < 0 || slot >= KL_SLOTS) return KL_SLOT;
  uint32_t base;unsigned extent;int r=kl_locate(slot,fl,&base,&extent);if(r)return r;
  if(extent<KM_HDR)return KL_BAD_HDR;
  if (fl->read(base, h, KM_HDR)) return KL_IO;
  for (i = 0; i < KM_HDR; i++) blank &= h[i] == 0xFF;
  if (blank) return KL_EMPTY;
  if (!hdr_len(h) || hdr_len(h)>extent || h[15]!=slot) return KL_BAD_HDR;
  out->code_len = (uint16_t)u16le(h + 4), out->entry = (uint16_t)u16le(h + 6);
  out->serial = (uint32_t)h[8] | (uint32_t)h[9] << 8 | (uint32_t)h[10] << 16 | (uint32_t)h[11] << 24;
  out->level = h[12], out->flags = h[13];
  /* the name block: a module's (not a page's or a deck's), whole inside its code */
  named = !(h[13] & (KM_F_PAGE | KM_F_CARDS)) && out->code_len >= 4 + KL_NAME;
  if (named) {
    if (fl->read(base + KM_HDR, h + KM_HDR, 4 + KL_NAME)) return KL_IO;
    named = h[16] == 'K' && h[17] == '6' && h[18] == 'N' && h[19] == 'M';
  }
  for (i = 0; i < KL_NAME; i++) out->name[i] = named ? (char)h[KM_HDR + 4 + i] : 0;
  return 0;
}

/* read slot n into dst and check its header and tag: the code length, or KL_* */
static int load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap, unsigned page) {
  unsigned total, code, flags;
  if (slot < 0 || slot >= KL_SLOTS) return KL_SLOT;
  if (cap < KM_HDR) return KL_BAD_HDR;
  /* Keep this path shallow: the Aura host calls kl_load from its keypad handler with little
     stack left. Calling kl_info here nested another C frame and a Rust SPI callback before the
     image was even read. Read the header into the destination, validate it, then load the image. */
  uint32_t base;unsigned extent;int r=kl_locate(slot,fl,&base,&extent);if(r)return r;
  if(extent<KM_HDR)return KL_BAD_HDR;
  if (fl->read(base, dst, KM_HDR)) return KL_IO;
  total = hdr_len(dst);
  if (!total) {
    unsigned i;
    for (i = 0; i < KM_HDR; ++i) if (dst[i] != 0xff) return KL_BAD_HDR;
    return KL_EMPTY;
  }
  code = u16le(dst + 4);
  flags = dst[13];
  if (page == 2) {
    if (flags != KL_RESIDENT_FLAGS) return KL_BAD_HDR;
  } else if (!!(flags & KM_F_PAGE) != !!page ||
             (flags & (KM_F_CARDS | KM_F_THEME | KM_F_RESIDENT))) return KL_BAD_HDR;
  if (total > cap || total > extent || dst[15] != slot) return KL_BAD_HDR;
  if (fl->read(base, dst, total)) return KL_IO;
  if (hdr_len(dst) != total || u16le(dst + 4) != code || dst[13] != flags)
    return KL_BAD_HDR;       /* changed between the two reads */
  if (km_tag_check(dst, code)) return KL_BAD_TAG;
  return (int)code;
}

static int action_id(const uint8_t *a) {
  if (a[13] != KM_F_LIB || u16le(a+4) <= 24 || u16le(a+6) != 24 ||
      a[16]!='K' || a[17]!='6' || a[18]!='N' || a[19]!='M' ||
      a[32]!='K' || a[33]!='6' || a[34]!='A' || a[35]!=1 ||
      a[36]<32 || a[36]==255 || a[37]!=0 || a[38]!=0 || a[39]!=0) return KL_NOT_MODULE;
  return a[36];
}
int kl_action(int slot, const kl_flash_t *fl) {
  uint8_t h[40];
  if (slot<0 || slot>=KL_SLOTS) return KL_SLOT;
  uint32_t base;unsigned extent;int r=kl_locate(slot,fl,&base,&extent);if(r)return r;
  if(extent<sizeof h)return KL_BAD_HDR;
  if (fl->read(base,h,sizeof h)) return KL_IO;
  if (!hdr_len(h) || hdr_len(h)>extent || h[15]!=slot) return KL_BAD_HDR;
  return action_id(h);
}
int kl_load_action(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap, unsigned action) {
  int r=load(slot,fl,dst,cap,0);
  if (r<=0) return r;
  if (action_id(dst)!=(int)action) return KL_NOT_MODULE;
  km_arm(dst,0);
  return r;
}

int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  int r = load(slot, fl, dst, cap, 0);
  if (r > 0) km_arm(dst, 0);
  return r;
}

int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  return load(slot, fl, dst, cap, 1);
}

static int resident_desc(const uint8_t *a, kl_resident_t *out) {
  const uint8_t *d = a + KM_HDR;
  unsigned code = u16le(a + 4), i, ended = 0;
  if (a[13] != KL_RESIDENT_FLAGS || u16le(a + 6) != 0 ||
      code < KL_RESIDENT_DESC_BYTES + 2 ||
      d[0]!='K' || d[1]!='6' || d[2]!='V' || d[3]!='M' || d[4]!=1 ||
      d[5]<32 || d[5]==255 || d[10]!=1 || d[11]!=0 ||
      !u16le(d+6) || (u16le(d+6)&3) || u16le(d+8)<KL_RESIDENT_DESC_BYTES ||
      u16le(d+8)>code-2 || (u16le(d+8)&1) || !d[12]) return KL_BAD_HDR;
  for (i=0; i<8; i++) {
    unsigned c=d[12+i];
    if (!c) ended=1;
    else if (ended || c<32 || c>126) return KL_BAD_HDR;
  }
  out->id=d[5]; out->state_bytes=(uint16_t)u16le(d+6);
  out->callback=(uint16_t)u16le(d+8);
  out->state_offset=(uint16_t)((KM_HDR+code+KM_TAG+3u)&~3u);
  for (i=0; i<8; i++) out->label[i]=(char)d[12+i];
  return 0;
}

int kl_resident_info(int slot, const kl_flash_t *fl, kl_resident_t *out) {
  uint8_t h[KM_HDR+KL_RESIDENT_DESC_BYTES];
  if (slot<0 || slot>=KL_SLOTS) return KL_SLOT;
  uint32_t base;unsigned extent;int r=kl_locate(slot,fl,&base,&extent);if(r)return r;
  if(extent<sizeof h)return KL_BAD_HDR;
  if (fl->read(base,h,sizeof h)) return KL_IO;
  if (!hdr_len(h) || hdr_len(h)>extent || h[15]!=slot) return KL_BAD_HDR;
  return resident_desc(h,out);
}

int kl_find_resident(unsigned id, const kl_flash_t *fl) {
  int slot, found=KL_EMPTY;
  kl_resident_t info;
  if (id<32 || id>254) return KL_BAD_HDR;
  for (slot=0; slot<KL_SLOTS; slot++) {
    int r=kl_resident_info(slot,fl,&info);
    if (r==KL_IO) return r;
    if (!r && info.id==id) {
      if (found>=0) return KL_NOT_MODULE; /* ambiguous provider: fail closed */
      found=slot;
    }
  }
  return found;
}

int kl_load_resident(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap,
                     unsigned id, kl_resident_t *out) {
  kl_resident_t info;
  int r=load(slot,fl,dst,cap,2);
  unsigned i;
  if (r<=0) return r;
  if (resident_desc(dst,&info)) return KL_BAD_HDR;
  if (info.id!=id) return KL_NOT_MODULE;
  if ((unsigned)info.state_offset+info.state_bytes>cap) return KL_BAD_HDR;
  for (i=0; i<info.state_bytes; i++) dst[info.state_offset+i]=0;
  *out=info;
  return r;
}
