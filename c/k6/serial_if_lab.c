/* Receive-only GP0 DMA / concurrent UART IQ acquisition experiment.
 * Caller owns RF setup and supplies >= 11000 bytes of arena memory.
 * No flash writes, ADC mux writes, or TX keying. Short UART queue critical
 * sections leave the DMA engine running.
 * SPDX-License-Identifier: MIT */
#include <stdint.h>
#include "if_iq.h"
#include "if_cic.h"
#define RING 8192u
#define TIM17 0x40014800u
#define DMA 0x40020000u
#define UART 0x40013800u
#define REG(a) (*(volatile uint32_t *)(a))
#define CLOCK 96000000u
#ifndef K6_IQ_IRQ_ONLY
static uint32_t divu(uint32_t n,uint32_t d) {
  uint32_t q=0,b=1;
  if(!d) return 0;
  while(d<n && !(d&0x80000000u)) d<<=1,b<<=1;
  while(b) { if(n>=d) n-=d,q|=b; d>>=1;b>>=1; }
  return q;
}
static void le32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(i*8)); }
static uint16_t crc(const uint8_t *p,unsigned n) {
  static const uint16_t t[16]={0,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef};
  uint16_t c=0;
  while(n--) { uint8_t b=*p++; c=(c<<4)^t[(c>>12)^(b>>4)];c=(c<<4)^t[(c>>12)^(b&15)]; }
  return c;
}
#endif
typedef struct { uint8_t *buf; volatile uint16_t len[8],pos; volatile unsigned head,n,slots; } tx_t;
#ifdef K6_IQ_PAGE
extern tx_t *volatile active_tx;
#else
tx_t *volatile active_tx;

/* The UART ISR calls this after servicing RX; zero leaves normal debug TX alone. */
int k6_serial_if_tx_irq(void) {
  tx_t *t=active_tx;
  if(!t) return 0;
  if(t->n) {
    REG(UART+0x28)=t->buf[t->head*264+t->pos++];
    if(t->pos==t->len[t->head]) { t->pos=0;if(++t->head==t->slots)t->head=0;t->n--; }
  } else REG(UART)&=~(1u<<7);
  return 1;
}
#endif /* resident UART ISR */
#ifndef K6_IQ_IRQ_ONLY
static void pump(tx_t *t) { (void)t; /* TXE interrupt drains the queue. */ }
static uint32_t irq_lock(void) {
  uint32_t mask;__asm__ volatile("mrs %0, primask\n cpsid i":"=r"(mask)::"memory");return mask;
}
static void irq_unlock(uint32_t mask) { __asm__ volatile("msr primask, %0"::"r"(mask):"memory"); }
static int enqueue(tx_t *t,uint16_t seq,const uint8_t *p,unsigned n) {
  uint32_t mask=irq_lock();
  if(t->n==t->slots) { irq_unlock(mask);return 0; }
  unsigned at=t->head+t->n;if(at>=t->slots)at-=t->slots;
  irq_unlock(mask);
  uint8_t *q=t->buf+at*264;
  q[0]=0xa6;q[1]=0x69;q[2]=(uint8_t)(seq>>8);q[3]=(uint8_t)seq;
  q[4]=(uint8_t)(n>>8);q[5]=(uint8_t)n;
  for(unsigned i=0;i<n;i++) q[6+i]=p[i];
  uint16_t c=crc(q+1,n+5);q[n+6]=(uint8_t)(c>>8);q[n+7]=(uint8_t)c;
  t->len[at]=(uint16_t)(n+8);
  mask=irq_lock();t->n++;REG(UART)|=1u<<7;irq_unlock(mask);return 1;
}
typedef struct { uint32_t t,c17,c14; } clock_t;
static uint32_t now(clock_t *c) {
  uint32_t a=REG(TIM17+0x24)&65535,b=REG(0x40002024)&65535;
  uint32_t coarse=((b-c->c14)&65535)*9600u, fine=(a-c->c17)&65535;
  c->t+=coarse+(uint32_t)(int32_t)(int16_t)(uint16_t)(fine-coarse);
  c->c17=a;c->c14=b;return c->t;
}
static int ones(const uint8_t *p) {
  const volatile uint32_t *q=(const volatile uint32_t *)p;
  uint32_t a=0,m=0x02020202u;
  for(unsigned i=0;i<16;i+=4) a+=(q[i]&m)+(q[i+1]&m)+(q[i+2]&m)+(q[i+3]&m);
  a>>=1;a=(a&0x00ff00ffu)+((a>>8)&0x00ff00ffu);
  return (int)((a&65535)+(a>>16))-32;
}
/* s: status,mode,ticks,produced,consumed,overruns,uncertain,max_poll_cycles,
 * counts,outputs,frames,tx_drops,dma_errors,clips,cps8,max_lag,min,max,sum,sumsq,
 * ratio,raw_cap_count,final_partial_pairs,reserved. */
int k6_serial_if_lab(uint8_t *mem,uint32_t bytes,uint32_t ms,uint32_t mode,
                     uint32_t ratio,uint32_t mix_step,uint32_t *s) {
  for(unsigned i=0;i<24;i++) s[i]=0;
  s[1]=mode;s[20]=ratio;s[16]=32;s[17]=(uint32_t)-32;
  if(bytes<11000 || mode>2 || ms<10 || ms>10000 || (mode && (!ratio || ratio>32 || (ratio&(ratio-1))))) return -1;
  uint32_t ahb=REG(0x40021014), apb=REG(0x40021018);
  if(REG(0x48000400)&12u) return -2;
  REG(0x40021014)=ahb|1;
  if((apb&(1u<<18)) || (REG(DMA+8)&1)) { REG(0x40021014)=ahb;return -3; }
  while(!(REG(UART+0x1c)&(1u<<6))) {}
  tx_t tx={mem+RING,{0},0,0,0,mode?8:2};
  active_tx=&tx;
  uint8_t *raw=mem+RING+528;
  k6_if_iq_t mixer;k6_if_cic_t cic;
  k6_if_iq_init(&mixer,0,1);k6_if_cic_init(&cic,(uint16_t)ratio);
  REG(0x40021018)=apb|(1u<<18);
  REG(TIM17)=0;REG(TIM17+0x28)=0;REG(TIM17+0x2c)=65535;
  REG(TIM17+0x14)=1;REG(TIM17+0x10)=0;REG(TIM17+0xc)=1u<<8;
  REG(DMA+8)=0;REG(DMA+4)=15;REG(DMA+0x10)=0x48000410;
  REG(DMA+0x14)=(uint32_t)mem;REG(DMA+0xc)=RING;
  REG(TIM17)=1;
  clock_t clk={0,REG(TIM17+0x24)&65535,REG(0x40002024)&65535};
  REG(DMA+8)=(3u<<12)|(1u<<7)|(1u<<5)|1;
  uint32_t lastw=0,laps=0,consumed=0,produced=0,lastt=0,cpb=4320;
  uint32_t rate_t=0,rate_p=0,end=ms*96000u,seq=0,filled=0,stamp=0;
  uint8_t frame[256];
  while(now(&clk)<end) {
    uint32_t t=clk.t,dt=t-lastt;lastt=t;
    if(dt>s[7]) s[7]=dt;
    if(dt>RING*12u) s[6]++;
    pump(&tx);
    uint32_t w=RING-(REG(DMA+0xc)&65535);
    if(w<lastw) laps++;
    lastw=w;produced=laps*RING+w;
    uint32_t lag=produced-consumed;
    if(lag>s[15]) s[15]=lag;
    if(REG(DMA)&8) { s[12]++;s[0]=(uint32_t)-6;break; }
    if(!produced && t>1920000u) { s[0]=(uint32_t)-5;break; }
    if(lag>RING-64) { s[5]++;consumed=produced&~63u;k6_if_cic_init(&cic,(uint16_t)ratio);continue; }
    if(t-rate_t>=960000u && produced>rate_p) {
      cpb=divu((t-rate_t)<<8,produced-rate_p);rate_t=t;rate_p=produced;
    }
    if(lag<64) continue;
    int x=ones(mem+(consumed&(RING-1)));
    consumed+=64;s[8]++;
    if(x<(int32_t)s[16]) s[16]=(uint32_t)x;
    if(x>(int32_t)s[17]) s[17]=(uint32_t)x;
    s[18]+=(uint32_t)x;s[19]+=(uint32_t)(x*x);
    stamp=t-(((produced-consumed+32)*cpb)>>8);
    if(!mode) { raw[s[21]++]=(uint8_t)(int8_t)x;if(s[21]==2048) break;continue; }
    int16_t i,q,oi,oq;
    mixer.phase=stamp*mix_step;
    /* Preserve five fractional bits through mixing/CIC before int8 quantization. */
    k6_if_iq_push(&mixer,(int16_t)(x*32),&i,&q);
    if(!k6_if_cic_push(&cic,i,q,&oi,&oq)) continue;
    if(!filled) { le32(frame,consumed);frame[12]=(uint8_t)mode;frame[13]=(uint8_t)ratio;frame[14]=frame[15]=0; }
    int a=(oi+4)>>3,b=(oq+4)>>3;
    if(a>127 || a< -128 || b>127 || b< -128) s[13]++;
    if(mode==2) {
      /* Signed 6-bit components; quantize the legacy scale with rounding.
       * Two complex pairs occupy three bytes, low bits first. */
      int u=(a+2)>>2,v=(b+2)>>2;
      unsigned pair=((unsigned)(u>31?31:u< -32?-32:u)&63u) |
                    (((unsigned)(v>31?31:v< -32?-32:v)&63u)<<6);
      unsigned at=16+(filled>>1)*3;
      if(!(filled&1)) {frame[at]=(uint8_t)pair;frame[at+1]=(uint8_t)(pair>>8);}
      else {frame[at+1]|=(uint8_t)(pair<<4);frame[at+2]=(uint8_t)(pair>>4);}
    } else {
      frame[16+filled*2]=(uint8_t)(int8_t)(a>127?127:a< -128?-128:a);
      frame[17+filled*2]=(uint8_t)(int8_t)(b>127?127:b< -128?-128:b);
    }
    s[9]++;
    if(++filled==(mode==2?160u:120u)) {
      le32(frame+4,stamp);le32(frame+8,cpb);
      if(enqueue(&tx,(uint16_t)seq++,frame,256)) s[10]++;else s[11]++;
      filled=0;
    }
  }
  s[2]=now(&clk);s[3]=produced;s[4]=consumed;s[14]=cpb;
  REG(DMA+8)=0;REG(TIM17)=0;REG(TIM17+0xc)=0;REG(DMA+4)=15;
  REG(DMA+0xc)=0;REG(DMA+0x10)=0;REG(DMA+0x14)=0;
  REG(0x40021018)=apb;REG(0x40021014)=ahb;
  if(mode && filled) {
    while(tx.n==tx.slots) pump(&tx);
    le32(frame+4,stamp);le32(frame+8,cpb);
    enqueue(&tx,(uint16_t)seq++,frame,16+(mode==2?(filled*3+1)/2:filled*2));s[10]++;s[22]=filled;
  }
  if(!mode) {
    for(unsigned at=0;at<s[21];at+=240) {
      unsigned n=s[21]-at;if(n>240)n=240;
      while(tx.n==tx.slots) pump(&tx);
      le32(frame,at*64);le32(frame+4,divu((at+n)*64*cpb,256));le32(frame+8,cpb);
      frame[12]=0;frame[13]=1;frame[14]=frame[15]=0;
      for(unsigned j=0;j<n;j++) frame[16+j]=raw[at+j];
      enqueue(&tx,(uint16_t)seq++,frame,16+n);s[10]++;
    }
    s[9]=s[21];
  }
  while(tx.n) pump(&tx);
  while(!(REG(UART+0x1c)&(1u<<6))) {}
  uint32_t mask=irq_lock();REG(UART)&=~(1u<<7);active_tx=0;irq_unlock(mask);
  return (int32_t)s[0];
}

#endif /* acquisition engine */

#ifdef K6_IQ_PAGE
__attribute__((section(".kp_tab"),used)) const uintptr_t kp_tab[]={0x0113504bu,(uintptr_t)kp_tab,(uintptr_t)k6_serial_if_lab};
#endif
