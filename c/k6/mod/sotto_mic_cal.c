/* Flux PA11 microphone-path calibration: RX-only result, no RF packets.
 * One short, PA-muted TX lease is needed to borrow TIM17 and the FD6818
 * analog path. Use a dummy load. Explicitly never call tx_pa(1) or FSK.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("MIC CAL");

typedef struct {
  int16_t pcm[320];
  volatile uint8_t produced, consumed, offset, lost;
} pcm_ring_t;
_Static_assert(sizeof(pcm_ring_t)==644, "API30 microphone ring layout changed");

static char *cat(char *p,const char *s){while(*s)*p++=*s++;return p;}
/* Avoid runtime divide symbols (Cortex-M0 has no hardware DIV). */
__attribute__((noinline,optnone)) uint32_t __aeabi_uidiv(uint32_t n,uint32_t d){
  uint32_t q=0,r=0;
  if(!d)return 0;
  for(int i=31;i>=0;i--){
    r=(r<<1)|((n>>i)&1u);
    if(r>=d){r-=d;q|=1u<<i;}
  }
  return q;
}
static char *num(char *p,uint32_t n){
  static const uint32_t powers[10]={1000000000u,100000000u,10000000u,1000000u,
                                   100000u,10000u,1000u,100u,10u,1u};
  uint32_t started=0;
  for(unsigned i=0;i<10;i++){
    unsigned digit=0;
    while(n>=powers[i]){n-=powers[i];digit++;}
    if(digit||started||i==9){*p++=(char)('0'+digit);started=1;}
  }
  return p;
}
static char *signed_num(char *p,int32_t n){
  if(n<0){*p++='-';return num(p,(uint32_t)(-n));}
  return num(p,(uint32_t)n);
}
/* Coherent 1-kHz magnitude in PCM peak counts at the nominal 8-kHz sample
 * rate. Eight sample-phase means reject DC without an analogue calibration;
 * quadrature projections also tolerate any unknown generator phase.
 * All intermediates fit in signed/unsigned 32 bits for <=8000 i16 samples.
 * Each phase is accumulated after discarding the first 20 ms of warm-up.
 * Q8 unit-circle coefficients 256*sin/cos(2*pi*k/8).
 * This estimates the fundamental, not clipping or total broadband energy. */
static uint32_t absi(int32_t x){return x<0?(uint32_t)(-x):(uint32_t)x;}
static int32_t divi(int32_t x,uint32_t n){
  if(!n)return 0;
  uint32_t q=__aeabi_uidiv(absi(x),n);
  return x<0?-(int32_t)q:(int32_t)q;
}
static uint32_t isqrt_u32(uint32_t v){
  uint32_t bit=1u<<30,ans=0;
  while(bit>v)bit>>=2;
  while(bit){
    if(v>=ans+bit){v-=ans+bit;ans=(ans>>1)+bit;}
    else ans>>=1;
    bit>>=2;
  }
  return ans;
}
static uint32_t tone_1k_amp(const volatile int32_t phase_sum[8],uint32_t phase_cycles){
  static const int16_t si[8]={0,181,256,181,0,-181,-256,-181};
  static const int16_t co[8]={256,181,0,-181,-256,-181,0,181};
  if(!phase_cycles)return 0;
  int32_t ip=0,qp=0;
  for(unsigned k=0;k<8;k++){
    int32_t avg=divi(phase_sum[k],phase_cycles);
    ip+=avg*si[k];qp+=avg*co[k];
  }
  uint32_t a=absi(ip)>>10,b=absi(qp)>>10;
  return isqrt_u32(a*a+b*b);
}
static void message(const k6_api_t*a,const char*head,const char*body){
  a->lcd_text(0,head);a->lcd_text(1,body);a->lcd_flush();
}
static void stats(const k6_api_t*a,uint32_t ns,int32_t mn,int32_t mx,
                  uint32_t peak,uint32_t abs_sum,uint32_t zc,
                  uint32_t vox_lo,uint32_t vox_hi,uint32_t gain,
                  uint8_t lost,uint32_t isr,uint32_t tone_1k,uint32_t clipped){
  char b[24];char*p;
  a->lcd_text(0,"MIC CAL RESULT");
  p=cat(b,"N=");p=num(p,ns);p=cat(p," LOST=");p=num(p,lost);*p=0;a->lcd_text(1,b);
  p=cat(b,"PK=");p=num(p,peak);p=cat(p," AVG=");p=num(p,ns?__aeabi_uidiv(abs_sum,ns):0);*p=0;a->lcd_text(2,b);
  p=cat(b,"1K=");p=num(p,tone_1k);p=cat(p," CLIP=");p=num(p,clipped);*p=0;a->lcd_text(3,b);
  p=cat(b,"V=");p=num(p,vox_lo);*p++='/';p=num(p,vox_hi);p=cat(p," G=");p=num(p,gain);*p=0;a->lcd_text(4,b);
  p=cat(b,"ZC=");p=num(p,zc);p=cat(p," ISR=");p=num(p,isr);*p=0;a->lcd_text(5,b);
  a->lcd_flush();
}
int k6_main(const k6_api_t *a){
  if(!a||a->level<30||a->abi!=1)return -3;
  uint32_t room=0;uint8_t*scratch=a->scratch(&room);
  if(!scratch||room<sizeof(pcm_ring_t)||((uintptr_t)scratch&3))return -8;
  pcm_ring_t *r=(pcm_ring_t*)scratch;
  message(a,"MIC CAL / NO RF","Hold PTT 1 second");
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
  for(uint32_t i=0;i<sizeof(*r);i++)scratch[i]=0;
  // API31 digital TX lease is PA-muted; Sotto mic host repeats tx_pa(0).
  if(a->tx_on(2000u)!=0)return -5;
  if(a->mic_stream(0,r,sizeof(*r))!=0){a->tx_off();return -6;}
  uint16_t gain=a->fd_read(0x7d)&31u;
  uint32_t start=a->ms();uint32_t samples=0, peak=0, abs_sum=0,zc=0;
  int32_t mn=32767,mx=-32768,prev=0;uint8_t have_prev=0;
  uint32_t vox_lo=1023,vox_hi=0;
  volatile int32_t phase_sum[8];
  for(unsigned k=0;k<8;k++)phase_sum[k]=0;
  uint32_t tone_samples=0,clipped=0;
  // No LCD SPI or flash work while the 8-kHz PDM ISR is running.
  while(a->ms()-start<1000u){
    if(a->mic_stream(3,0,0)!=1||a->key_down()||r->lost)break;
    uint8_t h=r->produced;
    if(h==r->consumed){a->delay_us(400);continue;}
    uint32_t slot=(r->consumed&3u)*80u;
    for(unsigned j=0;j<80;j++){
      int32_t v=r->pcm[slot+j];
      uint32_t av=(uint32_t)(v<0?-v:v);
      if(v<mn)mn=v;if(v>mx)mx=v;
      if(av>peak)peak=av;
      abs_sum+=av;
      if(have_prev&&((v>0)!=(prev>0)))zc++;
      // Skip the first 160 PCM samples (20 ms) to suppress first-TX
      // and GPIO-mode-change transients in the 1-kHz/clipping diagnostics.
      if(samples>=160){
        phase_sum[tone_samples&7u]+=v;
        tone_samples++;
        if(v==32767||v==-32768)clipped++;
      }
      have_prev=1;prev=v;samples++;
    }
    __asm__ volatile("" ::: "memory");
    r->consumed=(uint8_t)(r->consumed+1);
    uint32_t vox=a->fd_read(0x64)&1023u;
    if(vox<vox_lo)vox_lo=vox;if(vox>vox_hi)vox_hi=vox;
  }
  uint8_t lost=r->lost;
  uint32_t isr=(uint32_t)a->mic_stream(2,0,0);
  a->mic_stream(1,0,0);
  a->tx_off();
  if(samples==0){mn=0;mx=0;vox_lo=0;}
  uint32_t tone=tone_1k_amp(phase_sum,tone_samples/8u);
  stats(a,samples,mn,mx,peak,abs_sum,zc,vox_lo,vox_hi,gain,lost,isr,tone,clipped);
  uint32_t end=a->ms();uint32_t second_page=0;
  while(a->ms()-end<12000u){
    if(a->key_down())break;
    if(!second_page&&a->ms()-end>=6000u){
      char b[24];char *p=cat(b,"MIN=");
      p=signed_num(p,mn);p=cat(p," MAX=");p=signed_num(p,mx);*p=0;
      a->lcd_text(3,b);a->lcd_flush();second_page=1;
    }
    a->delay_us(30000);
  }
  return samples>0&&!lost?(int)(samples/80):-12;
}
