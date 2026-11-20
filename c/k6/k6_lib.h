/* k6_lib: the K6's SPI module library (Jeff approved it, 2026-11-08; docs/k6-modules.md).

   A RAM module (k6_mod.h) can be kept in the radio's SPI flash and run again later without
   sending it over the air each time. The library is 32 slots of 16 KB from 0x110000 to
   0x18FFFF, above sotto's codebooks (0x100000-0x10D320) and the calibration sector (0x10F000);
   12 slots until the release plan (Jeff 2026-11-13) gave it 20 more. 0x190000 up stays unused.

     slot n at KL_BASE + n * KL_SLOT_BYTES: the module image exactly as it arrived, header,
     code and SipHash tag (k6_mod.h), and 0xFF after it

   Slots by use (a plan for the tools, the library itself takes any image in any slot):
     0-8    modules
     9-11   EXPLORE's code pages (KM_F_PAGE, docs/k6-voice-explore.md)
     12     the station editor's code page (KM_F_PAGE)
     13-15  help card decks (KM_F_CARDS, the release plan's help cards)
     16-31  modules

   A module may carry its name (K6_MODULE_NAME in k6_mod.h): a 16-byte block, 'K' '6' 'N' 'M'
   and 12 characters (NUL padded), as the first bytes of its code. kl_info reads it with the
   header, so the module list can show names without loading anything.

   Install: a module sent with header flag KM_F_LIB (byte 13) and its slot in byte 15 is checked
   as any module is (km_packet: the tag under the radio's key, the serial), and then written to
   its slot instead of being run: kl_install erases the sectors the image needs, programs it a
   page at a time and reads it back. The only SPI writes are these, once per install, and never
   while keyed (the caller's flash calls refuse them). Nothing else of the radio's state goes
   to SPI: the message store never does (Jeff's rule).

   Load: kl_load reads a slot into RAM (the shared block, as a module arriving over the air),
   checks the header and the tag again (so a slot that wore, or that something else wrote, is
   refused), and arms km_run, which then runs it as it runs any module. No serial check here:
   the serial guards against a replay over the air, and a slot was checked when it went in.

   SPDX-License-Identifier: MIT */
#ifndef K6_LIB_H
#define K6_LIB_H
#include <stdint.h>
#include "k6_mod.h"
#ifdef __cplusplus
extern "C" {
#endif

#define KL_BASE 0x110000u
#define KL_SLOT_BYTES 0x4000u
#define KL_SLOTS 32
#define KL_END (KL_BASE + KL_SLOTS * KL_SLOT_BYTES)   /* 0x190000 */
#define KL_SECTOR 4096u
#define KL_PAGE 256u

/* header byte 13 (flags): install into the library, slot in byte 15, instead of running */
#define KM_F_LIB 0x01
/* header byte 13 (flags), with KM_F_LIB: a code page (kl_page), never run as a module: code
   linked at a fixed RAM address against one image, which calls into it (docs/k6-voice-explore.md) */
#define KM_F_PAGE 0x02
/* header byte 13 (flags), with KM_F_LIB: a deck of help cards (the release plan), never run:
   kl_load and kl_page refuse it */
#define KM_F_CARDS 0x04
/* signed cable-only request: erase the module named in byte 15 instead of installing */
#define KM_F_REMOVE 0x08
/* Signed cable-only theme envelope. Also sets CARDS so older load paths refuse execution. */
#define KM_F_THEME 0x10
/* Experimental resident callback envelope. CARDS keeps older foreground loaders
   from executing it; it is not a help deck. No published runtime capability. */
#define KM_F_RESIDENT 0x20
#define KL_RESIDENT_FLAGS (KM_F_LIB | KM_F_CARDS | KM_F_RESIDENT)
/* the module name block (K6_MODULE_NAME): magic, then KL_NAME characters */
#define KL_NAME 12

/* The SPI flash, as the core gives it: read anywhere in the library, program up to KL_PAGE
   bytes inside one page, erase one KL_SECTOR sector. 0, or -1 (refused: keyed, out of range). */
typedef struct {
  int (*read)(uint32_t addr, void *buf, uint32_t n);
  int (*prog)(uint32_t addr, const void *buf, uint32_t n);
  int (*erase)(uint32_t addr);
} kl_flash_t;

enum {
  KL_EMPTY = -1,     /* the slot is erased */
  KL_BAD_HDR = -2,   /* not a module image, or longer than the slot / the RAM given */
  KL_BAD_TAG = -3,   /* the tag doesn't check under this radio's key (or it has none) */
  KL_IO = -4,        /* a flash call refused */
  KL_VERIFY = -5,    /* read back differs from what was written */
  KL_SLOT = -6,      /* no such slot */
  KL_NOT_MODULE = -7 /* empty slot, page, deck, or non-library data */
};

/* Slot n's address */
#define KL_SLOT_ADDR(n) (KL_BASE + (uint32_t)(n) * KL_SLOT_BYTES)

int kl_locate(int slot,const kl_flash_t *fl,uint32_t *addr,unsigned *len);

/* Write the checked image at img (KM_READY from km_packet, flag KM_F_LIB) to the slot in its
   byte 15. The image's length, or KL_*. */
int kl_install(const uint8_t *img, const kl_flash_t *fl);
int kl_live_mutate(const uint8_t *img, unsigned n, const kl_flash_t *fl);
/* Erase one module slot, refusing EXPLORE pages, help decks, and malformed entries. */
int kl_remove(int slot, const kl_flash_t *fl);
/* Read slot n into dst (cap bytes) and check it: the code length and arm km_run, or KL_*.
   A page (KM_F_PAGE) is refused: KL_BAD_HDR. */
int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
/* Experimental RX-only resident descriptor at code offset zero: K6VM, version=1,
   ID (32..254), state bytes u16, callback offset u16, RX-only=1, reserved=0,
   eight printable ASCII label bytes (NUL padded). Header entry must be zero.
   Discovery is advisory; only kl_load_resident authenticates and checks RAM fit.
   cap is the caller's exclusive lease, excluding stack reserve and guard.
   On success state follows the aligned image+tag; no foreground entry is armed.
   Callers must stop any prior resident before overwriting its arena. */
#define KL_RESIDENT_DESC_BYTES 20u
typedef struct {
  uint16_t state_bytes, callback, state_offset;
  uint8_t id;
  char label[8];
} kl_resident_t;
int kl_resident_info(int slot, const kl_flash_t *fl, kl_resident_t *out);
int kl_find_resident(unsigned id, const kl_flash_t *fl);
int kl_load_resident(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap,
                     unsigned id, kl_resident_t *out);
/* Discover a declared action; metadata is advisory until authenticated load. */
int kl_action(int slot, const kl_flash_t *fl);
/* Load only if the signed image still exports this action. */
int kl_load_action(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap, unsigned action);
/* Read the page in slot n into dst (cap bytes) and check it as kl_load does, without arming
   km_run: the code length (its code at dst + KM_HDR), or KL_* (KL_BAD_HDR: not a page). The
   caller checks that the page was linked against it (the page's last bytes). */
int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
/* What slot n holds, from its header and the name block alone (no tag check). name: the
   module's name (NUL padded; all NUL when it has none, or is a page or a deck) */
typedef struct {
  uint16_t code_len, entry;
  uint32_t serial;
  uint8_t level, flags;
  char name[KL_NAME];
} kl_info_t;
int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out);   /* 0, or KL_* */

#ifdef __cplusplus
}
#endif
#endif
