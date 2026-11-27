/* Shared decoder pool and module arena reuse, host-only.
 * Same 4KB store + 1280B PWM ring + 9600B decoder tail mapping as K6's
 * 14976B expanded arena. No radio, SPI or encoder invoked.
 * SPDX-License-Identifier: MIT */
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include "../../../c/k6/sotto_pager.h"

extern void *sp_calloc(size_t,size_t);
extern void sp_free(void*);
#define ARENA_BYTES 14976u
#define DEC_OFF (4096u+1280u)
static uint32_t arena[ARENA_BYTES/4];
int main(void){
  uint8_t *mem=(uint8_t*)arena;
  unsigned bytes=sp_dec_need();
  void *first,*second;
  assert(bytes==3232u); /* 864+2048 synthesis +320 1200 LR state budget */
  assert(ARENA_BYTES-DEC_OFF >= bytes);
  sp_dec_pool(mem+DEC_OFF,ARENA_BYTES-DEC_OFF);
  assert(sp_init(mem,4096u)>0);
  first=sp_calloc(1,bytes);
  assert(first==mem+DEC_OFF);
  memset(first,0x5a,bytes);
  /* Emulate successful half-duplex TX module use of shared decoder tail:
   * the following sp_init must forget its previous decoder allocations. */
  memset(arena,0xb4,sizeof arena);
  assert(sp_init(mem,4096u)>0);
  second=sp_calloc(1,bytes);
  assert(second==first);
  for(unsigned i=0;i<bytes;i++)assert(((unsigned char*)second)[i]==0);
  assert(sp_calloc(1,ARENA_BYTES)==NULL);
  sp_free(second);
  printf("shared_arena=%u decoder_offset=%u decoder_need=%u reuse=yes\n",
          ARENA_BYTES,DEC_OFF,bytes);
  return 0;
}
