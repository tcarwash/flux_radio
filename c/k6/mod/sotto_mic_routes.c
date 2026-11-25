/* MIC ROUTES: physical PTT, PA muted, time-isolated RFIC AF-output and
 * GP0->PB1 input experiments. No RF transmission, no flash writes.
 * Result v4 is retained in existing 33-byte MAB1 live-serial mailbox.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("MIC ROUTES");
#include "sotto_mic_cal_math.inc"
#ifdef MIC_ROUTES_HOST_TEST
extern volatile uint32_t mic_routes_host_moder;
extern uint32_t mic_routes_host_gp0_read(void);
#define MIC_ROUTES_READ_GP0() mic_routes_host_gp0_read()
#define MIC_ROUTES_PB1_INPUT() ((mic_routes_host_moder&(3u<<2))==0)
#else
#define MIC_ROUTES_READ_GP0() (*(volatile const uint32_t *)0x48000410u)
#define MIC_ROUTES_PB1_INPUT() ((*(volatile const uint32_t *)0x48000400u&(3u<<2))==0)
#endif
#define GP0_BIT 2u
#define N_AF 4
#define N_GP0 5
#define NSTAGES (N_AF+N_GP0)
#define STAGE_SETTLE_BLOCKS 4
#define STAGE_MEASURE_BLOCKS 16
#define STAGE_TOTAL (STAGE_SETTLE_BLOCKS+STAGE_MEASURE_BLOCKS)
static const uint8_t af_modes[N_AF]={4,9,5,1};
static const uint8_t gp0_modes[N_GP0]={9,11,12,13,14};
typedef struct{int16_t pcm[320];volatile uint8_t produced,consumed,offset,lost;} pcm_ring_t;
_Static_assert(sizeof(pcm_ring_t)==644,"API30 PCM ring changed");

static uint8_t compress(uint32_t v){
 if(v<16)return (uint8_t)v;
 uint8_t exp=1;
 while(exp<15 && v>=(32u<<(exp-1u)))exp++;
 uint32_t mant=v>>(exp-1u);
 if(mant>31)mant=31;
 return (uint8_t)((exp<<4u)|(mant-16u));
}
static uint16_t crc16(const uint8_t *x,unsigned n){
 uint16_t c=0;for(unsigned i=0;i<n;i++){
   c^=(uint16_t)x[i]<<8;
   for(unsigned j=0;j<8;j++)c=(uint16_t)((c<<1)^((c&0x8000u)?0x1021u:0));
 }return c;
}
static void save(const k6_api_t*a,uint8_t x[33]){
 static const char b64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
 uint16_t c=crc16(x,31);x[31]=(uint8_t)c;x[32]=(uint8_t)(c>>8);
 char line[50];char *p=cat(line,"MAB1:");
 for(unsigned i=0;i<33;i+=3){
  uint32_t v=((uint32_t)x[i]<<16)|((uint32_t)x[i+1]<<8)|x[i+2];
  *p++=b64[(v>>18)&63];*p++=b64[(v>>12)&63];
  *p++=b64[(v>>6)&63];*p++=b64[v&63];
 }*p=0;a->say(line);
}
static void gp0_observe(uint32_t *ones,uint32_t *edges,uint32_t *count){
 uint32_t previous=(MIC_ROUTES_READ_GP0()&GP0_BIT)!=0;
 for(unsigned i=0;i<256;i++){
  uint32_t v=(MIC_ROUTES_READ_GP0()&GP0_BIT)!=0;
  *ones+=v;*edges+=(previous^v);previous=v;
 }
 *count+=256;
}
/* Read one 80-sample PCM block and optionally PB1; no blocking UART I/O.
 * Ring ownership stays with the module, and the ISR can preempt the loop. */
static int get_block(const k6_api_t*a,pcm_ring_t*r,unsigned *nblocks,
                     uint32_t *abs_sum,volatile int32_t phase[8],
                     uint32_t *pb1ones,uint32_t *pb1edges,uint32_t *pb1total,
                     uint32_t *vox,int do_measure,int measure_pb1){
 uint32_t wait=a->ms();
 while(r->consumed==r->produced){
  if(r->lost||a->mic_stream(3,0,0)!=1||a->key_down())return -9;
  if(a->ms()-wait>50)return -10;
  a->delay_us(200);
 }
 if(r->lost)return -11;
 unsigned off=((unsigned)r->consumed&3u)*80u;
 if(do_measure){
  for(unsigned j=0;j<80;j++){
    int32_t x=r->pcm[off+j];*abs_sum+=absi(x);
    phase[((*nblocks)*80u+j)&7u]+=x;
  }
  if(measure_pb1)gp0_observe(pb1ones,pb1edges,pb1total);
  uint32_t v=a->fd_read(0x64)&1023u;
  if(v>*vox)*vox=v;
 }
 __asm__ volatile("":::"memory");
 r->consumed=(uint8_t)(r->consumed+1u);
 (*nblocks)++;
 return 0;
}
int k6_main(const k6_api_t*a){
 if(!a||a->abi!=1||a->level<30)return -3;
 uint32_t cap=0;uint8_t*mem=a->scratch(&cap);
 if(!mem||cap<sizeof(pcm_ring_t)||((uintptr_t)mem&3u))return -8;
 if(!MIC_ROUTES_PB1_INPUT())return -15;
 pcm_ring_t*r=(pcm_ring_t*)mem;
 a->lcd_text(0,"MIC ROUTES / NO RF");
 a->lcd_text(1,"AF4/9/5/1 + GP0");
 a->lcd_text(2,"Hold PTT 3 seconds");
 a->lcd_text(3,"External 1kHz / 0.1V");
 a->lcd_flush();a->say("MAB1:CLR");
 uint32_t wait=a->ms();int ptt=0;
 while(a->ms()-wait<15000u){
  ptt=a->mic_stream(3,0,0);
  if(ptt<0||a->key_down())return -4;
  if(ptt==1)break;
  a->delay_us(5000);
 }
 if(ptt!=1)return -4;
 a->delay_us(30000);
 if(a->mic_stream(3,0,0)!=1)return -4;
 /* Capture GPIO restore values in the TX configuration, not before it:
  * tx_on() changes non-GP0 GPIO outputs (e.g. TX LED / band lines).
  * The register guard forbids changes to ANY bits outside GP0. */
 uint16_t saved33=0,saved34=0;
 uint8_t raw[33];for(unsigned j=0;j<33;j++)raw[j]=0;raw[0]=4;
 for(unsigned i=0;i<sizeof(*r);i++)mem[i]=0;
 if(a->tx_on(4500u)!=0)return -5; // API30 keeps PA muted
 int started=0,gp0_enabled=0,ret=0;
 unsigned completed=0;
 if(a->mic_stream(0,r,sizeof(*r))!=0){ret=-6;goto done;}
 started=1;
 saved33=a->fd_read(0x33);saved34=a->fd_read(0x34);
 uint32_t start=a->ms();unsigned discard=0;
 while(discard<12){
  unsigned b=discard;uint32_t zero=0;volatile int32_t p[8];for(unsigned k=0;k<8;k++)p[k]=0;
  ret=get_block(a,r,&b,&zero,p,&zero,&zero,&zero,&zero,0,0);
  if(ret)goto done;discard++;
 }
 uint32_t total_vox=0;
 for(unsigned stage=0;stage<NSTAGES;stage++){
  int is_gp0=stage>=N_AF;
  if(!is_gp0){
   uint16_t current=a->fd_read(0x47);
   uint16_t next=(uint16_t)((current&~0x0f00u)|((uint16_t)af_modes[stage]<<8));
   if(a->fd_write(0x47,next)!=0||a->fd_read(0x47)!=next){ret=-17;goto done;}
  }else{
   if(!gp0_enabled){
    uint16_t now=a->fd_read(0x47);
    if(a->fd_write(0x47,(uint16_t)((now&~0x0f00u)|0x0400u))!=0){ret=-18;goto done;}
    if(a->fd_write(0x33,(uint16_t)(saved33&~0x0100u))!=0){ret=-19;goto done;}
    gp0_enabled=1;
   }
   uint16_t next=(uint16_t)((saved34&~0xfu)|gp0_modes[stage-N_AF]);
   if(a->fd_write(0x34,next)!=0||a->fd_read(0x34)!=next){ret=-20;goto done;}
  }
  unsigned blocks=0;uint32_t abs_sum=0,pb1ones=0,pb1edges=0,pb1total=0,vox=0;
  volatile int32_t phase[8];for(unsigned k=0;k<8;k++)phase[k]=0;
  uint32_t prev_ones=0;
  for(unsigned k=0;k<STAGE_TOTAL;k++){
   if(k==STAGE_SETTLE_BLOCKS)prev_ones=(uint32_t)a->mic_stream(4,0,0);
   ret=get_block(a,r,&blocks,&abs_sum,phase,
                &pb1ones,&pb1edges,&pb1total,&vox,
                k>=STAGE_SETTLE_BLOCKS,is_gp0);
   if(ret)goto done;
  }
  uint8_t *out=&raw[1+3*stage];
  if(!is_gp0){
   uint32_t one=(uint32_t)a->mic_stream(4,0,0);
   if(one<prev_ones){ret=-21;goto done;}
   out[0]=compress(__aeabi_uidiv(abs_sum,16u*80u));
   out[1]=compress(tone_1k_amp(phase,(16u*80u)/8u));
   uint32_t fraction=__aeabi_uidiv((one-prev_ones)*255u,16u*80u*48u);
   out[2]=(uint8_t)(fraction>255u?255u:fraction);
  }else{
   out[0]=(uint8_t)__aeabi_uidiv(pb1ones*255u,pb1total);
   out[1]=(uint8_t)__aeabi_uidiv(pb1edges*255u,pb1total);
   out[2]=(uint8_t)(vox>1023?255:vox>>2);
  }
  if(vox>total_vox)total_vox=vox;
  if(a->ms()-start>3900u){ret=-22;goto done;}
  completed=stage+1;raw[30]=(uint8_t)completed;
 }
 raw[28]=(uint8_t)total_vox;raw[29]=(uint8_t)(total_vox>>8);
done:
 if(gp0_enabled){
  if(a->fd_write(0x34,saved34)!=0 && !ret)ret=-23;
  if(a->fd_write(0x33,saved33)!=0 && !ret)ret=-24;
 }
 if(started)a->mic_stream(1,0,0);
 a->tx_off();
 if(ret){
  /* Persist a *failure*, not an apparent valid 9-stage report, so an
   * unattended cable swap can still explain why a completed report is
   * absent. V5: positive error code, completed stages, exact status. */
  raw[0]=5;raw[1]=(uint8_t)(-ret);raw[2]=(uint8_t)completed;
  save(a,raw);
  a->lcd_text(0,"MIC ROUTES ABORT");
  a->lcd_text(1,"Error saved for serial");
  a->lcd_text(2,"Reconnect / no reboot");
  a->lcd_flush();
  return ret;
 }
 save(a,raw);
 a->lcd_text(0,"MIC ROUTES SAVED");
 a->lcd_text(1,"AF4/9/5/1 complete");
 a->lcd_text(2,"GP0 9/11/12/13/14");
 a->lcd_text(3,"Connect serial / no off");
 a->lcd_flush();
 uint32_t end=a->ms();
 while(a->ms()-end<8000u){if(a->key_down())break;a->delay_us(30000);}
 return 9;
}
