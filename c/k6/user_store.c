/* SPDX-License-Identifier: MIT */
#include "user_store.h"
#define MAGIC 0x324c554bu /* KUL2 */
static unsigned get32(const uint8_t*p){return (unsigned)p[0]|(unsigned)p[1]<<8|(unsigned)p[2]<<16|(unsigned)p[3]<<24;}
static void put32(uint8_t*p,unsigned n){for(unsigned i=0;i<4;i++)p[i]=n>>(8*i);}
static unsigned crc(const uint8_t*p,unsigned n){unsigned c=~0u;while(n--){c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;}
static unsigned size(const uint8_t*e){return e[2]|(unsigned)e[3]<<8;}
static int valid(const uint8_t*b){
 uint8_t used[KU_SECTORS]={0};
 if(get32(b)!=MAGIC||get32(b+8)!=crc(b+12,KU_DIR_BYTES-12)||get32(b+12)!=~get32(b+4))return 0;
 for(unsigned i=0;i<KU_IDS;i++){const uint8_t*e=b+16+4*i;unsigned n=size(e),s=e[0],k=e[1];
  if(!n){if(s||k)return 0;continue;}
  if(k!=(n+4095)/4096||s+k>KU_SECTORS)return 0;
  for(unsigned j=s;j<s+k;j++){if(used[j])return 0;used[j]=1;}
 }return 1;
}
int ku_open(ku_dir_t*d,const kl_flash_t*fl){
 uint8_t h[16];unsigned generation=0;int found=0;
 for(unsigned i=0;i<2;i++){
  if(fl->read(KU_BASE+i*KL_SECTOR,h,16))return KL_IO;
  if(get32(h)!=MAGIC)continue;
  if(found&&(int32_t)(get32(h+4)-generation)<=0)continue;
  if(fl->read(KU_BASE+i*KL_SECTOR,d->bytes,KU_DIR_BYTES))return KL_IO;
  if(valid(d->bytes)){d->copy=i;generation=get32(h+4);found=1;}
  else if(found){if(fl->read(KU_BASE+d->copy*KL_SECTOR,d->bytes,KU_DIR_BYTES))return KL_IO;}
 }return found?0:KL_BAD_HDR;
}
/* Verify before publishing magic. A failed transaction must reopen its directory. */
static int write_verify(const kl_flash_t*fl,uint32_t a,const uint8_t*p,unsigned n){
 uint8_t back[64];unsigned off=0;
 while(off<n){unsigned k=KL_PAGE-((a+off)&(KL_PAGE-1));if(k>n-off)k=n-off;if(fl->prog(a+off,p+off,k))return KL_IO;off+=k;}
 for(off=0;off<n;){unsigned k=n-off;if(k>sizeof back)k=sizeof back;if(fl->read(a+off,back,k))return KL_IO;for(unsigned j=0;j<k;j++)if(back[j]!=p[off+j])return KL_VERIFY;off+=k;}return 0;
}
static int publish(ku_dir_t*d,const kl_flash_t*fl){
 unsigned next=d->copy^1,a=KU_BASE+next*KL_SECTOR,g=get32(d->bytes+4)+1;int r;
 put32(d->bytes,MAGIC);put32(d->bytes+4,g);put32(d->bytes+12,~g);put32(d->bytes+8,crc(d->bytes+12,KU_DIR_BYTES-12));
 if(fl->erase(a))return KL_IO;
 r=write_verify(fl,a+4,d->bytes+4,KU_DIR_BYTES-4);if(r)return r;
 r=write_verify(fl,a,d->bytes,4);if(r)return r;d->copy=next;return 0;
}
int ku_format(ku_dir_t*d,const kl_flash_t*fl){
 /* Only call after exporting all legacy contents. Old directory must not survive. */
 if(fl->erase(KU_BASE)||fl->erase(KU_BASE+KL_SECTOR))return KL_IO;
 for(unsigned i=0;i<KU_DIR_BYTES;i++)d->bytes[i]=0;d->copy=1;return publish(d,fl);
}
int ku_lookup(const ku_dir_t*d,unsigned id,uint32_t*a,unsigned*n){
 if(id>=KU_IDS)return KL_SLOT;const uint8_t*e=d->bytes+16+4*id;*n=size(e);if(!*n)return KL_EMPTY;*a=KU_DATA+e[0]*KL_SECTOR;return 0;
}
int ku_put(ku_dir_t*d,const kl_flash_t*fl,unsigned id,const uint8_t*p,unsigned n){
 uint8_t used[KU_SECTORS]={0};unsigned k=(n+4095)/4096,s=0,run=0;int r;
 if(id>=KU_IDS||!n||n>65535||!valid(d->bytes))return KL_BAD_HDR;
 /* Keep the old allocation until the new generation commits. */
 for(unsigned i=0;i<KU_IDS;i++){const uint8_t*e=d->bytes+16+4*i;for(unsigned j=e[0];j<(unsigned)e[0]+e[1];j++)used[j]=1;}
 for(s=0;s<KU_SECTORS;s++){run=used[s]?0:run+1;if(run==k)break;}if(s==KU_SECTORS)return KL_SLOT;s=s+1-k;
 unsigned a=KU_DATA+s*KL_SECTOR;for(unsigned j=0;j<k;j++)if(fl->erase(a+j*KL_SECTOR))return KL_IO;
 r=write_verify(fl,a,p,n);if(r)return r;
 uint8_t*e=d->bytes+16+4*id;e[0]=s;e[1]=k;e[2]=n;e[3]=n>>8;
 r=publish(d,fl);if(r)ku_open(d,fl);return r;
}
int ku_remove(ku_dir_t*d,const kl_flash_t*fl,unsigned id){
 if(id>=KU_IDS||!valid(d->bytes))return KL_BAD_HDR;uint8_t*e=d->bytes+16+4*id;
 if(!size(e))return KL_EMPTY;for(unsigned i=0;i<4;i++)e[i]=0;
 int r=publish(d,fl);if(r)ku_open(d,fl);return r;
}
/* Typed theme operations, called only after authenticated envelope and inner validation. */
int ku_theme_put(const kl_flash_t*fl,unsigned id,const uint8_t*p,unsigned n){
 ku_dir_t d;uint32_t a;unsigned len;int r=ku_open(&d,fl);if(r)return r;
 r=ku_lookup(&d,id,&a,&len);if(r!=KL_EMPTY)return KL_NOT_MODULE;
 r=ku_put(&d,fl,id,p,n);return r?r:(int)n;
}
int ku_theme_remove(const kl_flash_t*fl,unsigned id){ku_dir_t d;int r=ku_open(&d,fl);return r?r:ku_remove(&d,fl,id);}
int ku_staged_valid(const uint8_t *b){return valid(b);}
