/* Bounded, non-executable PXK6 card store in the documented unused SPI range. SPDX-License-Identifier: MIT */
#ifndef K6_CARD_STORE_H
#define K6_CARD_STORE_H
#include "card_record.h"

#define PXK6_STORE_BASE 0x190000u
#define PXK6_STORE_END 0x19A000u
#define PXK6_STORE_SECTOR 4096u
#define PXK6_STORE_SLOTS ((PXK6_STORE_END - PXK6_STORE_BASE) / PXK6_STORE_SECTOR)
#define PXK6_STORE_PAGE 256u

typedef struct {
  int (*read)(uint32_t addr, void *dst, uint32_t bytes);
  int (*program)(uint32_t addr, const void *src, uint32_t bytes);
  int (*erase)(uint32_t sector_addr);
} pxk6_store_flash_t;

/* Count valid records. Damaged/non-card sectors are left untouched and excluded. */
int pxk6_store_count(const pxk6_store_flash_t *flash, uint8_t *scratch,
                     size_t scratch_bytes);
/* Store a validated record. Uses 64 bytes of scratch, exact duplicate is idempotent, and a
   new record is written to one erased sector then read back and checked. */
int pxk6_store_put(const pxk6_store_flash_t *flash, const uint8_t *record,
                   size_t record_bytes, uint8_t *scratch, size_t scratch_bytes);
/* Erase one card-store sector. Already-erased sectors are a successful no-op. */
int pxk6_store_erase_slot(const pxk6_store_flash_t *flash, unsigned slot);
/* Read the record in a slot, validate it, and return its byte count; negative on error. */
int pxk6_store_get(const pxk6_store_flash_t *flash, unsigned slot,
                   uint8_t *dst, size_t dst_bytes);
#endif
