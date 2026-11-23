/* Read-only telemetry during finite FD6818B native-FSK frames.
 * Default inert. This probe preloads each frame in full and never appends,
 * reads REG_5F, or changes REG_5E. REG_02 is sampled without clearing it.
 * SPDX-License-Identifier: MIT
 */
#ifndef FSK_STATUS_ENABLE
#define FSK_STATUS_ENABLE 0
#endif
#ifndef FSK_STATUS_FIXTURE_ACK
#define FSK_STATUS_FIXTURE_ACK 0
#endif
#ifndef FSK_STATUS_WATCHDOG_ACK
#define FSK_STATUS_WATCHDOG_ACK 0
#endif
#if FSK_STATUS_ENABLE && (!FSK_STATUS_FIXTURE_ACK || !FSK_STATUS_WATCHDOG_ACK)
#error "FSK status probe requires dummy-load and watchdog acknowledgements"
#endif
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "fsk_lab_vector.h"

#define HZ 433500000u
#define LEASE_MS 1800u
#define FRAME_TIMEOUT_MS 500u
#define R_CHIP 0x00u
#define R_IRQ 0x02u
#define R_STATUS 0x0cu
#define R_STATE 0x30u
#define R_MODE 0x3fu
#define R_CTRL 0x59u
#define R_LEVEL 0x5eu
#define R_FIFO 0x5fu
#define R_RSSI 0x67u
#define N_FRAMES 3u
#define N_SAMPLES 18u

#if FSK_STATUS_ENABLE
static const uint8_t frame_words[N_FRAMES]={16u,32u,48u};
static const uint16_t nominal_ms[N_FRAMES]={53u,107u,160u};
typedef struct {
  uint8_t frame;
  uint16_t elapsed_ms,irq,status,mode,level,state;
} sample_t;
static const uint8_t restore_regs[]={0x3fu,0x59u,0x58u,0x70u,0x72u,
                                     0x5au,0x5bu,0x5cu,0x5du};
#define N_RESTORE (sizeof(restore_regs)/sizeof(restore_regs[0]))

static int safe(const k6_api_t *a){return !a->key_down()&&!km_power_switch_off();}
static int wr(const k6_api_t *a,uint8_t r,uint16_t v){return a->fd_write(r,v)==0?0:-1;}
static int free_channel(const k6_api_t *a){return KM_RSSI_DBM(a->fd_read(R_RSSI))<=-90;}
static void hex4(char *p,uint16_t v){
  static const char h[]="0123456789ABCDEF";
  unsigned i;for(i=0;i<4;i++)p[i]=h[(v>>(12-4*i))&15u];
}
static int record(const k6_api_t *a,sample_t *samples,unsigned *sample_count,
                  unsigned frame,uint32_t start){
  sample_t *s;
  if(*sample_count>=N_SAMPLES)return -1;
  s=&samples[(*sample_count)++];
  s->frame=(uint8_t)frame;
  s->elapsed_ms=(uint16_t)(a->ms()-start);
  s->irq=a->fd_read(R_IRQ);       /* read only: deliberately no REG_02 write */
  s->status=a->fd_read(R_STATUS);
  s->mode=a->fd_read(R_MODE);
  s->level=a->fd_read(R_LEVEL);   /* unknown B semantics; never modified */
  s->state=a->fd_read(R_STATE);
  return 0;
}
static void say_samples(const k6_api_t *a,sample_t *samples,unsigned sample_count){
  unsigned i;
  for(i=0;i<sample_count;i++){
    char line[]="S f=0 t=000 i=0000 c=0000 m=0000 l=0000 x=0000";
    sample_t *s=&samples[i];
    line[4]=(char)('0'+s->frame);
    line[8]=(char)('0'+(s->elapsed_ms/100u)%10u);
    line[9]=(char)('0'+(s->elapsed_ms/10u)%10u);
    line[10]=(char)('0'+s->elapsed_ms%10u);
    hex4(line+14,s->irq);hex4(line+21,s->status);hex4(line+28,s->mode);
    hex4(line+35,s->level);hex4(line+42,s->state);
    a->say(line);
  }
}
static int send_frame(const k6_api_t *a,unsigned f,uint32_t *duration_ms,
                      sample_t *samples,unsigned *sample_count){
  unsigned i,n=frame_words[f];
  uint32_t start,nominal=nominal_ms[f];
  uint32_t timeout=FRAME_TIMEOUT_MS;
  if(wr(a,R_CTRL,0x00f8u)!=0||wr(a,R_IRQ,0u)!=0||
     wr(a,0x5du,(uint16_t)(((n*2u-1u)<<8)))!=0||
     wr(a,R_CTRL,0x80f8u)!=0||wr(a,R_CTRL,0x00f8u)!=0)return -13;
  for(i=0;i<n;i++){
    if(!safe(a))return -4;
    if(wr(a,R_FIFO,fsk_lab_words[i])!=0)return -13;
  }
  if(wr(a,R_CTRL,0x08f8u)!=0)return -13;
  start=a->ms();
  if(record(a,samples,sample_count,f,start)!=0)return -16;
  for(i=1;i<=4u;i++){
    uint32_t target=nominal*i/4u;
    while((uint32_t)(a->ms()-start)<target){
      if(!safe(a))return -4;
      if(a->fd_read(R_STATUS)&1u){
        *duration_ms=(uint32_t)(a->ms()-start);
        if(record(a,samples,sample_count,f,start)!=0)return -16;
        return 0;
      }
      a->delay_us(500u);
    }
    if(record(a,samples,sample_count,f,start)!=0)return -16;
  }
  for(;;){
    if(!safe(a))return -4;
    if(a->fd_read(R_STATUS)&1u){
      *duration_ms=(uint32_t)(a->ms()-start);
      if(record(a,samples,sample_count,f,start)!=0)return -16;
      return 0;
    }
    if((uint32_t)(a->ms()-start)>timeout)return -14;
    a->delay_us(500u);
  }
}
#endif

int k6_main(const k6_api_t *a){
  if(!a||a->abi!=1||a->level<3)return -3;
  if(a->fd_read(R_CHIP)!=0x4829u)return -6;
#if !FSK_STATUS_ENABLE
  a->say("fsk status inert; bench build required");return -9;
#else
  uint16_t saved[N_RESTORE];sample_t samples[N_SAMPLES];
  unsigned sample_count=0,i,f;int keyed=0,restore=0,ret=0;
  uint32_t durations[N_FRAMES]={0,0,0};
  if(a->rx_hz()!=HZ||!safe(a)||(a->fd_read(R_STATE)&2u))return -2;
  if(!free_channel(a))return -7;
  a->delay_us(50000u);
  if(!safe(a)||!free_channel(a))return -7;
  if(a->fd_read(R_MODE)&0xa000u)return -8;
  if(a->tx_on(LEASE_MS)!=0)return -5;keyed=1;
  if(a->tx_pa(0)!=0){ret=-4;goto cleanup;}
  for(i=0;i<N_RESTORE;i++)saved[i]=a->fd_read(restore_regs[i]);
  if((saved[0]&0xa000u)||(saved[1]&0x0800u)){ret=-8;goto cleanup;}
  restore=1;
#define W(r,v) do{if(wr(a,(r),(uint16_t)(v))!=0){ret=-13;goto cleanup;}}while(0)
  W(R_CTRL,0x00f8u);W(R_IRQ,0u);W(R_MODE,0x8000u);
  W(0x58u,0x00e1u);W(0x70u,0x0095u);W(0x72u,0xc194u);
  W(0x5au,0x076bu);W(0x5bu,0x3e36u);W(0x5cu,0x5625u);
  if(a->tx_pa(1)!=0){ret=-4;goto cleanup;}
  for(f=0;f<N_FRAMES;f++){
    ret=send_frame(a,f,&durations[f],samples,&sample_count);
    if(ret<0)goto cleanup;
  }
  ret=16u+32u+48u;
#undef W
cleanup:
  if(keyed){
    (void)a->tx_pa(0);
    if(restore){
      if(wr(a,R_CTRL,0x00f8u)!=0||wr(a,R_MODE,0u)!=0||
         wr(a,R_IRQ,0u)!=0)ret=-15;
      for(i=N_RESTORE;i>0;i--)if(wr(a,restore_regs[i-1],saved[i-1])!=0)ret=-15;
    }
    a->tx_off();
  }
  say_samples(a,samples,sample_count);
  if(ret==96)a->say("fsk status: 16/32/48-word probes complete");
  else a->say("fsk status: aborted; receiver restored");
  return ret;
#endif
}
