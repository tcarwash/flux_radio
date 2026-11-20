/* Experimental KUL2 library allocation, independent of legacy slot mapping. MIT */
#ifndef FLUX_USER_STORE_H
#define FLUX_USER_STORE_H
#include "k6_lib.h"
#define KU_BASE KL_BASE
#define KU_DATA (KU_BASE+2*KL_SECTOR)
#define KU_SECTORS 126u
#define KU_IDS 255u
#define KU_DIR_BYTES (16u+4u*KU_IDS)
/* Caller supplies scratch; never implicitly formats legacy media. */
typedef struct { uint8_t bytes[KU_DIR_BYTES]; unsigned copy; } ku_dir_t;
int ku_open(ku_dir_t *d,const kl_flash_t *fl);
int ku_format(ku_dir_t *d,const kl_flash_t *fl); /* explicit destructive migration only */
int ku_lookup(const ku_dir_t *d,unsigned id,uint32_t *addr,unsigned *len);
int ku_put(ku_dir_t *d,const kl_flash_t *fl,unsigned id,const uint8_t *data,unsigned len);
int ku_remove(ku_dir_t *d,const kl_flash_t *fl,unsigned id);
#endif
