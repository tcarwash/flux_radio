/* MIC GP0: TX microphone RFIC-only route (API30 op7) + high-speed GP0 PB1
 * DMA density spectrum. No PA or RF power, no PA11/TIM17 ISR, no SPI writes.
 * A 1kHz/2kHz comparison tests whether GP0 code12 encodes mic audio.
 * No generator or PCB scope required on serial retrieval. SPDX: MIT. */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#ifdef MIC_GP0_IRQ_ONLY
K6_MODULE_NAME("MIC IRQ");
#elif defined(MIC_GP0_PCM_ONLY)
K6_MODULE_NAME("MIC PCM");
#else
K6_MODULE_NAME("MIC GP0");
#endif
#define GPIOB 0x48000400u
#define RCC_AHB 0x40021014u
#define RCC_APB2 0x40021018u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17+0x24u)
#define TIM17EN (1u<<18)
#define DMA 0x40020000u
#define DMA_CCR1 (DMA+0x08u)
#define DMA_CNDTR1 (DMA+0x0cu)
#define DMA_CPAR1 (DMA+0x10u)
#define DMA_CMAR1 (DMA+0x14u)
#define RINGSZ 1024u
#define L 64u
#define CAL_OUT 2048u
#define OUT 4096u
#define NW 4u
#ifdef MIC_GP0_HOST_TEST
extern uint32_t mic_gp0_host_rd(uint32_t);
extern void mic_gp0_host_wr(uint32_t,uint32_t);
#define rd(a) mic_gp0_host_rd(a)
#define wr(a,v) mic_gp0_host_wr(a,v)
#else
static uint32_t rd(uint32_t a){return *(volatile const uint32_t*)a;}
static void wr(uint32_t a,uint32_t v){*(volatile uint32_t*)a=v;}
#endif
static uint32_t divu(uint32_t n,uint32_t d){
 uint32_t q=0,bit=1;
 if(!d)return 0;
 while(d<n && !(d&0x80000000u)){d<<=1;bit<<=1;}
 while(bit){if(n>=d){n-=d;q|=bit;}d>>=1;bit>>=1;}
 return q;
}
static uint32_t root(uint32_t x){
 uint32_t r=0,b=1u<<30;
 while(b>x)b>>=2;
 while(b){if(x>=r+b){x-=r+b;r=(r>>1)+b;}else r>>=1;b>>=2;}
 return r;
}
static uint32_t abs32(int32_t x){return (uint32_t)(x<0?-x:x);}
static uint16_t crc16(const uint8_t *x,unsigned n){
 uint16_t c=0;for(unsigned i=0;i<n;i++){
  c^=(uint16_t)x[i]<<8;
  for(unsigned j=0;j<8;j++)c=(uint16_t)((c<<1)^((c&0x8000u)?0x1021u:0));
 }return c;
}
static void emit(const k6_api_t*a,uint8_t x[33]){
 static const char b64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
 uint16_t c=crc16(x,31);x[31]=(uint8_t)c;x[32]=(uint8_t)(c>>8);
 char line[50],*p=line;*p++='M';*p++='A';*p++='B';*p++='1';*p++=':';
 for(unsigned i=0;i<33;i+=3){
  uint32_t v=((uint32_t)x[i]<<16)|((uint32_t)x[i+1]<<8)|x[i+2];
  *p++=b64[(v>>18)&63];*p++=b64[(v>>12)&63];
  *p++=b64[(v>>6)&63];*p++=b64[v&63];
 }*p=0;a->say(line);
}
static void put16(uint8_t*p,uint32_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static const int8_t sn[64]={
   0, 12, 25, 37, 49, 60, 71, 81, 90, 98,106,112,117,122,125,126,
 127,126,125,122,117,112,106, 98, 90, 81, 71, 60, 49, 37, 25, 12,
   0,-12,-25,-37,-49,-60,-71,-81,-90,-98,-106,-112,-117,-122,-125,-126,
 -127,-126,-125,-122,-117,-112,-106,-98,-90,-81,-71,-60,-49,-37,-25,-12};
static unsigned ones64(const uint8_t*ring,uint32_t off){
 const uint32_t *p=(const uint32_t *)(ring+off);
 uint32_t acc=0;
 for(unsigned i=0;i<16;i++)acc+=p[i]&0x02020202u;
 acc>>=1;
 acc=(acc&0x00ff00ffu)+((acc>>8)&0x00ff00ffu);
 return (unsigned)((acc&0xffffu)+(acc>>16));
}
static void stop_dma(void){
 wr(TIM17,0);wr(TIM17+0x0cu,0);wr(DMA_CCR1,0);wr(DMA+0x04,0xf);
}
static void start_dma(uint8_t*ring){
 wr(DMA_CCR1,0);wr(DMA+0x04,0xf);
 wr(DMA_CPAR1,GPIOB+0x10);wr(DMA_CMAR1,(uint32_t)(uintptr_t)ring);
 wr(DMA_CNDTR1,RINGSZ);
 wr(TIM17,0);wr(TIM17+0x28u,0);wr(TIM17+0x2cu,0xffffu);
 wr(TIM17+0x14u,1);wr(TIM17+0x10u,0);
 wr(TIM17+0x0cu,1u<<8);wr(TIM17,1);
 wr(DMA_CCR1,(3u<<12)|(1u<<7)|(1u<<5)|1u);
}
typedef struct{
 int32_t i1,q1,i2,q2;
 uint32_t sumsq,sumones,sumdiff,cycles;
 uint32_t count;uint16_t ph1,ph2;
 int prev;
} metrics_t;
static void feed(metrics_t *m,unsigned bits,unsigned inc1,unsigned inc2){
 int x=(int)bits*2-64;
 unsigned idx1=(m->ph1>>10)&63u,idx2=(m->ph2>>10)&63u;
 m->i1+=x*sn[(idx1+16u)&63u];m->q1+=x*sn[idx1];
 m->i2+=x*sn[(idx2+16u)&63u];m->q2+=x*sn[idx2];
 m->ph1=(uint16_t)(m->ph1+inc1);m->ph2=(uint16_t)(m->ph2+inc2);
 m->sumones+=bits;m->sumsq+=(uint32_t)(x*x);
 if(m->count)m->sumdiff+=abs32(x-m->prev);
 m->prev=x;m->count++;
}
#ifndef MIC_GP0_PCM_ONLY
static uint8_t spectrum(int32_t a,int32_t b,uint32_t n){
 if(!n)return 0;
 int32_t x=a<0?-(int32_t)divu(abs32(a),n):(int32_t)divu(abs32(a),n);
 int32_t y=b<0?-(int32_t)divu(abs32(b),n):(int32_t)divu(abs32(b),n);
 uint32_t v=(uint32_t)(x*x+y*y);
 uint32_t mag=root(v)/16u;
 return (uint8_t)(mag>255u?255u:mag);
}
 #endif
/* ring DMA is free-running at ~5.5MS/s, independently of TIM17's
 * nominal update configuration on this chip. Poll within 186us or abort. */
static int capture(uint8_t*ring,uint32_t goal,unsigned inc1,unsigned inc2,metrics_t*m){
 for(unsigned j=0;j<sizeof(*m);j++)((uint8_t*)m)[j]=0;
 start_dma(ring);
 uint32_t laps=0,lastw=0,consumed=0,produced=0;
 uint32_t lastclock=rd(TIM17_CNT)&0xffffu;
 while(m->count<goal){
  uint32_t w=RINGSZ-(rd(DMA_CNDTR1)&0xffffu);
  if(w<lastw)laps++;
  lastw=w;produced=laps*RINGSZ+w;
  uint32_t now=rd(TIM17_CNT)&0xffffu;
  m->cycles+=(now-lastclock)&0xffffu;lastclock=now;
  uint32_t avail=produced-consumed;
  if(avail>RINGSZ-L){stop_dma();return -11;}
  if(avail<L)continue;
  unsigned bits=ones64(ring,consumed&(RINGSZ-1u));
  consumed+=L;
  feed(m,bits,inc1,inc2);
  if(m->cycles>120000000u){stop_dma();return -12;}
 }
 stop_dma();
 return 0;
}
/* Shared DMA capture machinery; optionally embed in MIC PCM module. */
#ifndef MIC_GP0_PCM_ONLY
int k6_main(const k6_api_t*a){
 if(!a||a->abi!=1||a->level<30)return -3;
 uint32_t space=0;uint8_t*scratch=a->scratch(&space);
 if(!scratch||space<RINGSZ+4u)return -8;
 uint8_t*ring=(uint8_t*)(((uintptr_t)scratch+3u)&~(uintptr_t)3u);
 if((rd(GPIOB)&(3u<<2))!=0)return -15;
 a->lcd_text(0,"MIC GP0 / NO RF");
 a->lcd_text(1,"GP0-12 DMA mic test");
 a->lcd_text(2,"Hold PTT ~2 seconds");
 a->lcd_flush();a->say("MAB1:CLR");
 uint32_t t=a->ms();int ptt=0;
 while(a->ms()-t<15000u){
  ptt=a->mic_stream(3,0,0);
  if(ptt<0||a->key_down())return -4;
  if(ptt==1)break;
  a->delay_us(5000);
 }
 if(ptt!=1)return -4;
 a->delay_us(30000);
 if(a->mic_stream(3,0,0)!=1)return -4;
 uint8_t result[33];for(unsigned j=0;j<33;j++)result[j]=0;result[0]=6;
 int tx=0,route=0,enabled=0,gpio=0,ret=0;
 unsigned completed=0;
 uint32_t oldahb=0,oldapb=0;
 uint16_t old33=0,old34=0;
 if(a->tx_on(2500u)!=0){ret=-5;goto done;}
 tx=1;
 /* Op7 is deliberately unsupported on older firmware. It enables the
  * real RFIC mic ADC/route but NEVER starts PA11's TIM17 sampler. */
 if(a->mic_stream(7,0,0)!=0){ret=-6;goto done;}
 route=1;
 old33=a->fd_read(0x33);old34=a->fd_read(0x34);
 if(a->fd_write(0x33,(uint16_t)(old33&~0x0100u))!=0){ret=-17;goto done;}
 gpio=1;
 if(a->fd_write(0x34,(uint16_t)((old34&~0xfu)|12u))!=0||
     (a->fd_read(0x34)&0xfu)!=12u){ret=-18;goto done;}
 /* Allow the RFIC MIC ADC/bias to settle before claiming a negative
  * result. The previous unpowered module TX mode had MICIN disabled.
  * RFIC VOX still determines whether the tone-on stimulus was strong. */
 a->delay_us(800000u);
 if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;goto done;}
 oldahb=rd(RCC_AHB);oldapb=rd(RCC_APB2);
 if(oldapb&TIM17EN){ret=-19;goto done;}
 wr(RCC_AHB,oldahb|1u);
 if(rd(DMA_CCR1)&1u){ret=-20;wr(RCC_AHB,oldahb);goto done;}
 wr(RCC_APB2,oldapb|TIM17EN);enabled=1;
 metrics_t m;
 ret=capture(ring,CAL_OUT,0,0,&m);
 if(ret)goto done;
 /* Output sample rate = CAL_OUT * 96MHz / timer cycles. This
  * fits in 32-bit as CAL_OUT*96000 / (cycles/1000). */
 uint32_t fs=divu(CAL_OUT*96000u, divu(m.cycles,1000u) ? divu(m.cycles,1000u):1u);
 if(fs<35000u||fs>130000u){ret=-21;goto done;}
 uint32_t raw_ks=divu(fs*L,1000u);
 put16(result+3,divu(fs,10u));put16(result+5,raw_ks);
 unsigned inc1=divu(1000u*65536u,fs),inc2=divu(2000u*65536u,fs);
 uint32_t voxmax=0;
 for(unsigned win=0;win<NW;win++){
  if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;goto done;}
  uint32_t v=a->fd_read(0x64)&1023u;if(v>voxmax)voxmax=v;
  ret=capture(ring,OUT,inc1,inc2,&m);
  if(ret)goto done;
  v=a->fd_read(0x64)&1023u;if(v>voxmax)voxmax=v;
  uint8_t*dest=result+7u+win*6u;
  dest[0]=spectrum(m.i1,m.q1,m.count);
  dest[1]=spectrum(m.i2,m.q2,m.count);
  dest[2]=(uint8_t)root(divu(m.sumsq,m.count));
  dest[3]=(uint8_t)divu(m.sumones*255u,m.count*L);
  dest[4]=(uint8_t)divu(m.sumdiff,m.count);
  dest[5]=(uint8_t)(v>>2);
  completed=win+1;
 }
 put16(result+1,voxmax);
done:
 if(enabled){
  stop_dma();wr(DMA_CNDTR1,0);wr(DMA_CPAR1,0);wr(DMA_CMAR1,0);
  wr(RCC_APB2,oldapb);wr(RCC_AHB,oldahb);
 }
 if(gpio){
  if(a->fd_write(0x34,old34)!=0 && !ret)ret=-22;
  if(a->fd_write(0x33,old33)!=0 && !ret)ret=-23;
 }
 if(route)a->mic_stream(1,0,0);
 if(tx)a->tx_off();
 if(ret){
  result[0]=7;result[1]=(uint8_t)(-ret);result[2]=(uint8_t)completed;
  emit(a,result);
  a->lcd_text(0,"MIC GP0 ABORT");
  a->lcd_text(1,"Error saved to serial");
  a->lcd_flush();return ret;
 }
 emit(a,result);
 a->lcd_text(0,"MIC GP0 SAVED");
 a->lcd_text(1,"Reconnect no reboot");
 a->lcd_flush();
 uint32_t end=a->ms();
 while(a->ms()-end<6000u){if(a->key_down())break;a->delay_us(25000);}
 return (int)NW;
}

#endif /* !MIC_GP0_PCM_ONLY */
