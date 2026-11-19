/* Firmware-owned SPI overlays. No user library slot numbers. SPDX-License-Identifier: MIT */
#ifndef FLUX_SYSTEM_PAGE_H
#define FLUX_SYSTEM_PAGE_H
#include "k6_lib.h"
#define KS_BASE 0x19C000u
#define KS_END 0x200000u
#define KS_PAYLOAD (KS_BASE + 4096u)
#define KS_DIRECTORY_MAGIC 0x3144534bu /* KSD1 */
#define KS_MAX_ID 255u
/* Direct-index directory: 16-byte header, 8 bytes per page ID. */
int ks_entry(const uint8_t header[12], const uint8_t entry[8], const uint8_t build[8], unsigned cap);
/* Read-only loading; caller owns an exclusive arena lease and separate stack.
   On success return code bytes (table at dst+KM_HDR); on failure never call it. */
int ks_load(unsigned slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap,
            unsigned id, unsigned functions, const uint8_t build[8]);
int ks_validate(unsigned slot, uint8_t *dst, unsigned total, unsigned id, unsigned functions, const uint8_t build[8]);
#endif
