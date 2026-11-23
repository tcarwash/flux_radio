/* Sotto 1200 slim codec SPI table transport for the K6 module arena.
 *
 * - The existing K6 Sotto shape-codebook SPI region at 0x100020.
 * - The exact upstream fmsotto K6SC v1 IVF deck layout in a signed
 *   library module. The deck's 4,182 bytes are CRC-independent checked
 *   by checksum + signed slot attestation by the outer radio driver.
 * - The DCT is computed (no 800B ROM table).
 *
 * Build the UNMODIFIED sotto_fx_slim.c with
 *   -include /path/to/dv_live_slim_spi.h
 * and link dv_live_slim_spi.c. This keeps the large 30.7KiB codebook and
 * ~3KiB IVF data out of the 15KiB radio module arena. Neither source
 * performs hardware I/O by itself: callers supply a read-only callback.
 *
 * Failure is sticky: a fault sets dv_flash_fault(), the outer radio
 * controller must abort without transmitting any generated packet.
 * Caller must not use the UART's transient fl_read during an SPI1/ISR
 * overlap unless its firmware/clock has been separately qualified.
 *
 * Inspired by upstream Blowfang fmsotto 90s RX->Sotto on-device encoder.
 * This implementation is an OFFLINE PORT until B-chip physical testing.
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_DV_LIVE_SLIM_SPI_H
#define FLUX_DV_LIVE_SLIM_SPI_H
#include <stdint.h>
#include "dv_live_slim_dct.h"

#define DV_FLASH_CB_BASE 0x100020u
#define DV_FLASH_DECK_SIZE 4182u
#define DV_FLASH_DECK_HEADER 16u
#define DV_FLASH_IVF_CD 8u
#define DV_FLASH_IVF_MAXN 26u

typedef int (*dv_flash_read_cb)(uint32_t addr,void *buf,unsigned len,void *ctx);
typedef struct {
    dv_flash_read_cb read;
    void *ctx;
    uint32_t deck_addr;
    unsigned fault;
} dv_flash_ctx_t;
/* Host supplies ordinary storage; actual K6 module MUST bind this to a
 * caller-allocated scratch context (e.g. the fmsotto remapped vector
 * pointer), NEVER a mutable .bss global in the signed K6M module. */
dv_flash_ctx_t *dv_flash_active(void);

int dv_flash_configure(dv_flash_read_cb read,void *ctx,uint32_t deck_address);
int dv_flash_fault(void);
const signed char *dv_flash_cb_fetch(int stage,int vector,signed char *buf);
const signed char *dv_flash_ivf_centroid(int stage,int first,signed char *buf);
int dv_flash_ivf_cell(int stage,int cell,int16_t *values);

#define SLIM_CB_FETCH(s,v,buf) dv_flash_cb_fetch((s),(v),(signed char *)(buf))
#define SLIM_IVF_CEN(s,c0,buf) dv_flash_ivf_centroid((s),(c0),(signed char *)(buf))
#define SLIM_IVF_CELL(s,c,v) dv_flash_ivf_cell((s),(c),(v))
#endif
