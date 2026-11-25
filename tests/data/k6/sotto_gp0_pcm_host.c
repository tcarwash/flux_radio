/* Standalone signed PCM pipeline tests, no RFIC or MCU peripherals. MIT */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "../../../c/k6/mod/sotto_gp0_pcm.h"
int main(int argc,char**argv){
 if(argc!=3)return 2;
 unsigned fs=(unsigned)strtoul(argv[1],0,10),tone=(unsigned)strtoul(argv[2],0,10);
 gp0_pcm_t g;gp0_pcm_init(&g,fs);
 int16_t sample=0;uint32_t count=0,clipped=0;
 double a=0,b=0,dc=0,rms=0;
 int min=32767,max=-32768;
 for(unsigned i=0;i<fs*2u;i++){
  double theta=2.0*3.141592653589793*tone*i/fs;
  /* 2x one-bit density ripple and a 0.1 input/DC bias: GPs are digital,
   * but the decimator should preserve only the speech-band variation. */
  double x=32.0+0.5+(tone?7.0*sin(theta):0.0);
  unsigned bits=(unsigned)(x+0.5);
  int r=gp0_pcm_push(&g,bits,&sample);
  if(r<0)return 3;
  if(!r)continue;
  int n=(int)count++;
  if(n<1000)continue; /* DC filter startup (~125ms) */
  if(sample<min)min=sample;if(sample>max)max=sample;
  a+=sample*sin(2*3.141592653589793*tone*n/8000.0);
  b+=sample*cos(2*3.141592653589793*tone*n/8000.0);
  rms+=(double)sample*sample;
  dc+=sample;
 }
 if(count!=16000||g.samples!=count||g.clipped)return 4;
 double n=(double)count-1000;
 double signal=tone?(2.0*sqrt(a*a+b*b)/n):0;
 printf("fs=%u hz=%u samples=%u rms=%.1f tone_peak=%.1f dc=%.1f min=%d max=%d clipped=%u\n",
        fs,tone,count,sqrt(rms/n),signal,dc/n,min,max,g.clipped);
 if(tone){if(signal<4000.0||signal>30000.0||fabs(dc/n)>3500.0)return 5;}
 else {if(sqrt(rms/n)>300||abs(min)>600||abs(max)>600)return 6;}
 return 0;
}
