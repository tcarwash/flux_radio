/* Copied MIC CAL helpers, parity enforced in tests. */
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
static __attribute__((unused)) char *signed_num(char *p,int32_t n){
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
