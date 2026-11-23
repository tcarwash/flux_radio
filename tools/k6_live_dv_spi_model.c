/* OFFLINE host simulation of the signed K6SC IVF deck plus the existing
 * 30,720B Sotto shape-codebook SPI region. Only public codec constants,
 * no voice data. The actual K6 driver must supply its own read callback.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fx_tables.h"
#include "../c/k6/mod/dv_live_slim_spi.h"

static uint8_t deck[DV_FLASH_DECK_SIZE];
static dv_flash_ctx_t state;

dv_flash_ctx_t *dv_flash_active(void) {return &state;}

static int model_read(uint32_t address,void *buf,unsigned n,void *ctx) {
    unsigned i;
    uint8_t *dst=(uint8_t *)buf;
    (void)ctx;
    if(address>=DV_FLASH_CB_BASE &&
       address-DV_FLASH_CB_BASE<=30720u &&
       n<=30720u-(address-DV_FLASH_CB_BASE)) {
        static const unsigned base[4]={0,512u,1024u,1280u};
        static const unsigned extent[4]={512u,512u,256u,256u};
        for(i=0;i<n;i++) {
            unsigned pos=(address-DV_FLASH_CB_BASE+i);
            unsigned stage;
            for(stage=0;stage<4;stage++)
                if(pos<(base[stage]+extent[stage])*20u)break;
            if(stage==4)return -1;
            dst[i]=((const uint8_t *)fx_cb[stage])[pos-base[stage]*20u];
        }
        return 0;
    }
    if(address>=0x148010u &&
       address-0x148010u<=DV_FLASH_DECK_SIZE &&
       n<=DV_FLASH_DECK_SIZE-(address-0x148010u)) {
        memcpy(buf,deck+(address-0x148010u),n);
        return 0;
    }
    return -1;
}

void dv_flash_host_setup(const char *deck_file) {
    FILE *f=fopen(deck_file,"rb");
    if(!f || fread(deck,1,sizeof(deck),f)!=sizeof(deck) ||
       fgetc(f)!=EOF || fclose(f)!=0) {
        fprintf(stderr,"Invalid or missing offline Sotto IVF deck\n");
        exit(6);
    }
    if(dv_flash_configure(model_read,NULL,0x148010u)!=0 ||
       dv_flash_fault()) {
        fprintf(stderr,"K6SC V1 deck checksum/signature/layout verification failed\n");
        exit(6);
    }
}
